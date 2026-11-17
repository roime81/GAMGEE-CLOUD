import unittest

from pydantic import ValidationError

from tower.workloads import (
    WorkloadRequest,
    WorkspaceRequest,
    build_workload_objects,
    build_workspace_manifest,
)


class WorkloadTests(unittest.TestCase):
    def test_workspace_manifest_is_scoped_and_quota_limited(self):
        manifest = build_workspace_manifest(WorkspaceRequest(name="acme", owner_email="owner@example.com"))
        self.assertEqual(manifest["kind"], "Tenant")
        self.assertEqual(
            manifest["spec"]["additionalNamespaces"],
            ["acme-production", "acme-staging"],
        )
        self.assertEqual(manifest["spec"]["namespaceOptions"]["additionalMetadata"]["labels"]["gamgee.cloud/tenant"], "acme")

    def test_app_defaults_to_private_and_hardened(self):
        request = WorkloadRequest(name="portal", image="ghcr.io/acme/portal:v1.2.3")
        objects = build_workload_objects("acme", request)
        self.assertEqual([item["kind"] for item in objects], ["Deployment", "Service"])
        deployment = objects[0]
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(container["securityContext"]["runAsNonRoot"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertEqual(objects[1]["spec"]["type"], "ClusterIP")

    def test_public_app_requires_tls_and_adds_secure_ingress(self):
        with self.assertRaises(ValidationError):
            WorkloadRequest(name="portal", image="ghcr.io/acme/portal:v1.2.3", hostname="portal.example.com")
        request = WorkloadRequest(
            name="portal",
            image="ghcr.io/acme/portal:v1.2.3",
            hostname="portal.example.com",
            tls_secret="portal-cert",
        )
        objects = build_workload_objects("acme", request)
        ingress = objects[2]
        self.assertEqual(ingress["kind"], "Ingress")
        self.assertEqual(ingress["spec"]["tls"][0]["secretName"], "portal-cert")

    def test_rejects_latest_tag_and_root_port(self):
        with self.assertRaises(ValidationError):
            WorkloadRequest(name="portal", image="nginx:latest")
        with self.assertRaises(ValidationError):
            WorkloadRequest(name="portal", image="nginx:1.27", port=80)

    def test_rejects_injected_or_unknown_fields(self):
        with self.assertRaises(ValidationError):
            WorkloadRequest(name="portal; rm -rf /", image="nginx:1.27")
        with self.assertRaises(ValidationError):
            WorkloadRequest(name="portal", image="nginx:1.27", command="sh -c id")

    def test_digest_image_reference_is_allowed(self):
        image = "ghcr.io/acme/portal@sha256:" + "a" * 64
        self.assertEqual(WorkloadRequest(name="portal", image=image).image, image)


if __name__ == "__main__":
    unittest.main()
