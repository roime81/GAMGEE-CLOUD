import unittest
from unittest.mock import patch

from tower.cluster import ClusterNotFound
from tower.drivers.kubernetes_function import (
    KubernetesFunctionDriver,
    build_function_objects,
    function_resource_name,
)


def sample_resource(*, public=False, deleted=False):
    desired = {
        "name": "thumbnail",
        "image": "ghcr.io/acme/thumbnail:v1.2.0",
        "path": "/resize",
        "min_replicas": 0,
        "max_replicas": 8,
        "concurrency": 40,
        "cooldown_seconds": 90,
        "hostname": "functions.example.com" if public else None,
        "tls_secret": "gamgee-functions-tls" if public else None,
    }
    if deleted:
        desired = {"deleted": True}
    return {
        "id": "12345678-1234-5678-1234-567812345678",
        "kind": "Function",
        "workspace": "acme",
        "name": "thumbnail",
        "desired": desired,
    }


class KubernetesFunctionDriverTests(unittest.TestCase):
    def test_compiler_creates_spinapp_http_route_proxy_and_zero_scale_keda(self):
        resource = sample_resource()
        objects = build_function_objects(resource)
        by_kind = {item["kind"]: item for item in objects}

        self.assertEqual(
            [item["kind"] for item in objects],
            ["ServiceAccount", "SpinApp", "InterceptorRoute", "Service", "ScaledObject"],
        )
        service_account = by_kind["ServiceAccount"]
        self.assertFalse(service_account["automountServiceAccountToken"])
        spin = by_kind["SpinApp"]
        self.assertEqual(spin["apiVersion"], "core.spinkube.dev/v1alpha1")
        self.assertEqual(spin["spec"]["executor"], "containerd-shim-spin")
        self.assertTrue(spin["spec"]["enableAutoscaling"])
        self.assertEqual(spin["spec"]["serviceAccountName"], spin["metadata"]["name"])
        self.assertEqual(spin["spec"]["image"], "ghcr.io/acme/thumbnail:v1.2.0")

        route = by_kind["InterceptorRoute"]
        self.assertEqual(route["apiVersion"], "http.keda.sh/v1beta1")
        self.assertEqual(route["spec"]["target"]["port"], 80)
        self.assertEqual(route["spec"]["rules"][0]["paths"], [{"value": "/resize"}])
        self.assertEqual(route["spec"]["coldStart"]["maxPendingRequests"], 100)

        scaled = by_kind["ScaledObject"]
        self.assertEqual(scaled["spec"]["minReplicaCount"], 0)
        self.assertEqual(scaled["spec"]["maxReplicaCount"], 8)
        self.assertEqual(scaled["spec"]["triggers"][0]["type"], "external-push")

        proxy = by_kind["Service"]
        self.assertEqual(proxy["spec"]["type"], "ExternalName")
        self.assertIn("keda-add-ons-http-interceptor-proxy.keda.svc", proxy["spec"]["externalName"])

    def test_public_function_routes_ingress_to_interceptor_and_uses_tls(self):
        objects = build_function_objects(sample_resource(public=True))
        ingress = next(item for item in objects if item["kind"] == "Ingress")
        self.assertEqual(ingress["metadata"]["namespace"], "keda")
        backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
        self.assertEqual(backend["name"], "keda-add-ons-http-interceptor-proxy")
        self.assertEqual(backend["port"]["number"], 8080)
        self.assertEqual(ingress["spec"]["tls"][0]["secretName"], "gamgee-functions-tls")

    def test_resource_name_is_dns_safe_and_unique_per_resource_id(self):
        first = function_resource_name("acme", "thumbnail", "12345678-1234-5678-1234-567812345678")
        second = function_resource_name("acme", "thumbnail", "87654321-4321-8765-4321-876543218765")
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 63)
        self.assertRegex(first, r"^[a-z0-9-]+$")

    @patch("tower.drivers.kubernetes_function.apply_object")
    @patch("tower.drivers.kubernetes_function.get_json")
    def test_reconcile_applies_interceptor_route_before_scaled_object(self, get_json_mock, apply_mock):
        resource = sample_resource()

        def get_json_side_effect(resource_type, name=None, namespace=None, **kwargs):
            if resource_type == "namespace" and name == "acme-production":
                return {"metadata": {"labels": {"gamgee.cloud/tenant": "acme"}}}
            if resource_type == "namespace" and name == "keda":
                return {"metadata": {"name": "keda"}}
            if resource_type == "customresourcedefinition":
                return {"metadata": {"name": name}}
            raise ClusterNotFound(f"{resource_type} not found")

        get_json_mock.side_effect = get_json_side_effect
        driver = KubernetesFunctionDriver()
        with patch.object(driver, "observe", return_value={"state": "progressing"}):
            observed = driver.reconcile(resource)

        self.assertEqual(observed["state"], "progressing")
        kinds = [call.args[0]["kind"] for call in apply_mock.call_args_list]
        self.assertEqual(kinds, ["ServiceAccount", "SpinApp", "InterceptorRoute", "Service", "ScaledObject"])

    @patch("tower.drivers.kubernetes_function.apply_object")
    @patch("tower.drivers.kubernetes_function.get_json")
    def test_reconcile_refuses_to_adopt_foreign_spinapp(self, get_json_mock, apply_mock):
        resource = sample_resource()
        key = function_resource_name("acme", "thumbnail", resource["id"])

        def get_json_side_effect(resource_type, name=None, namespace=None, **kwargs):
            if resource_type == "namespace" and name == "acme-production":
                return {"metadata": {"labels": {"gamgee.cloud/tenant": "acme"}}}
            if resource_type == "namespace" and name == "keda":
                return {"metadata": {"name": "keda"}}
            if resource_type == "customresourcedefinition":
                return {"metadata": {"name": name}}
            if resource_type == "spinapps.core.spinkube.dev" and name == key:
                return {"metadata": {"labels": {"app": "not-gamgee"}}}
            raise ClusterNotFound(f"{resource_type} not found")

        get_json_mock.side_effect = get_json_side_effect
        with self.assertRaisesRegex(RuntimeError, "not owned by this GAMGEE Function"):
            KubernetesFunctionDriver().reconcile(resource)
        apply_mock.assert_not_called()

    @patch("tower.drivers.kubernetes_function.run_kubectl")
    @patch("tower.drivers.kubernetes_function.get_json")
    def test_delete_waits_for_owned_custom_resources_to_disappear(self, get_json_mock, run_mock):
        original = sample_resource(public=True)
        resource = dict(original, desired={"deleted": True})
        key = function_resource_name("acme", "thumbnail", resource["id"])
        proxy = f"{key}-proxy"
        deleted = set()
        owned = {
            "gamgee.cloud/managed-by": "gamgee",
            "gamgee.cloud/tenant": "acme",
            "gamgee.cloud/resource-id": resource["id"],
        }

        def run_side_effect(args, **kwargs):
            deleted.add(args[1])
            return ""

        def get_json_side_effect(resource_type, name=None, namespace=None, **kwargs):
            if resource_type == "namespace" and name == "keda":
                return {"metadata": {"name": "keda"}}
            if resource_type == "namespace" and name == "acme-production":
                return {"metadata": {"labels": {"gamgee.cloud/tenant": "acme"}}}
            mapping = {
                ("ingress", key): f"ingress/{key}",
                ("serviceaccount", key): f"serviceaccount/{key}",
                ("spinapps.core.spinkube.dev", key): f"spinapps.core.spinkube.dev/{key}",
                ("interceptorroutes.http.keda.sh", key): f"interceptorroutes.http.keda.sh/{key}",
                ("scaledobjects.keda.sh", key): f"scaledobjects.keda.sh/{key}",
                ("service", proxy): f"service/{proxy}",
            }
            item = mapping.get((resource_type, name))
            if item and item not in deleted:
                return {"metadata": {"labels": owned}}
            raise ClusterNotFound(f"{resource_type} not found")

        get_json_mock.side_effect = get_json_side_effect
        run_mock.side_effect = run_side_effect
        observed = KubernetesFunctionDriver().reconcile(resource)
        self.assertEqual(observed["state"], "deleted")
        self.assertEqual(len(deleted), 6)


if __name__ == "__main__":
    unittest.main()
