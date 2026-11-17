import unittest
from unittest.mock import patch

from tower.cluster import ClusterNotFound
from tower.drivers.kubernetes_application import KubernetesApplicationDriver
from tower.workloads import WorkloadRequest


class KubernetesApplicationDriverTests(unittest.TestCase):
    def setUp(self):
        self.driver = KubernetesApplicationDriver()
        self.resource = {
            "id": "01234567-89ab-cdef-0123-456789abcdef",
            "kind": "Application",
            "workspace": "acme",
            "name": "portal",
            "generation": 1,
            "desired": WorkloadRequest(name="portal", image="ghcr.io/acme/portal:v1.2.3").model_dump(mode="json"),
        }

    @patch("tower.drivers.kubernetes_application.apply_objects")
    @patch("tower.drivers.kubernetes_application.get_json")
    def test_compiles_and_applies_only_resource_owned_objects(self, get_json, apply_objects):
        calls = {"deployment.apps": 0}

        def fake_get(resource, name=None, namespace=None, label_selector=None):
            if resource == "namespace":
                return {"metadata": {"labels": {"gamgee.cloud/tenant": "acme"}}}
            if resource == "deployment.apps":
                calls["deployment.apps"] += 1
                if calls["deployment.apps"] == 1:
                    raise ClusterNotFound("not found")
                return {
                    "metadata": {
                        "generation": 3,
                        "labels": {
                            "gamgee.cloud/managed-by": "gamgee",
                            "gamgee.cloud/tenant": "acme",
                            "gamgee.cloud/resource-id": "01234567-89ab-cdef-0123-456789abcdef",
                        },
                    },
                    "spec": {
                        "replicas": 1,
                        "template": {"spec": {"containers": [{"image": "ghcr.io/acme/portal:v1.2.3"}]}},
                    },
                    "status": {"readyReplicas": 1, "availableReplicas": 1},
                }
            raise ClusterNotFound("not found")

        get_json.side_effect = fake_get
        observed = self.driver.reconcile(self.resource)
        objects = apply_objects.call_args.args[0]
        self.assertEqual([item["kind"] for item in objects], ["Deployment", "Service"])
        self.assertTrue(
            all(
                item["metadata"]["labels"]["gamgee.cloud/resource-id"] == self.resource["id"]
                for item in objects
            )
        )
        self.assertEqual(observed["state"], "ready")
        self.assertEqual(observed["ready_replicas"], 1)

    @patch("tower.drivers.kubernetes_application.apply_objects")
    @patch("tower.drivers.kubernetes_application.get_json")
    def test_refuses_to_adopt_foreign_same_named_deployment(self, get_json, apply_objects):
        def fake_get(resource, name=None, namespace=None, label_selector=None):
            if resource == "namespace":
                return {"metadata": {"labels": {"gamgee.cloud/tenant": "acme"}}}
            if resource == "deployment.apps":
                return {
                    "metadata": {"labels": {
                        "gamgee.cloud/managed-by": "gamgee",
                        "gamgee.cloud/tenant": "acme",
                        "gamgee.cloud/resource-id": "another-resource",
                    }}
                }
            raise ClusterNotFound("not found")

        get_json.side_effect = fake_get
        with self.assertRaises(RuntimeError):
            self.driver.reconcile(self.resource)
        apply_objects.assert_not_called()

    @patch("tower.drivers.kubernetes_application.run_kubectl")
    @patch("tower.drivers.kubernetes_application.get_json")
    def test_delete_workspace_missing_is_idempotently_complete(self, get_json, run_kubectl):
        resource = dict(self.resource)
        resource["desired"] = {"deleted": True}

        def missing_namespace(resource_kind, name=None, namespace=None, label_selector=None):
            raise ClusterNotFound("namespace missing")

        get_json.side_effect = missing_namespace
        observed = self.driver.reconcile(resource)
        self.assertEqual(observed["state"], "deleted")
        run_kubectl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
