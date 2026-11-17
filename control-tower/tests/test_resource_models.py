import unittest

from pydantic import ValidationError

from tower.resource_models import ApplicationResourceRequest, FunctionResourceRequest, to_resource_document
from tower.workloads import WorkloadRequest


class ResourceModelTests(unittest.TestCase):
    def test_application_resource_has_versioned_envelope(self):
        request = ApplicationResourceRequest(
            workspace="ACME",
            spec={"name": "portal", "image": "ghcr.io/acme/portal:v1.2.3"},
        )
        self.assertEqual(request.workspace, "acme")
        self.assertEqual(request.spec.name, "portal")


    def test_function_resource_is_a_versioned_native_wasm_resource(self):
        request = FunctionResourceRequest(
            workspace="ACME",
            spec={
                "name": "thumbnail",
                "image": "ghcr.io/acme/thumbnail:v1.2.0",
                "path": "/resize",
                "min_replicas": 0,
                "max_replicas": 8,
                "concurrency": 40,
            },
        )
        self.assertEqual(request.workspace, "acme")
        self.assertEqual(request.spec.name, "thumbnail")
        self.assertEqual(request.spec.min_replicas, 0)
        self.assertEqual(request.spec.max_replicas, 8)

    def test_function_rejects_mutable_image_and_arbitrary_fields(self):
        with self.assertRaises(ValidationError):
            FunctionResourceRequest(
                workspace="acme",
                spec={"name": "thumbnail", "image": "ghcr.io/acme/thumbnail:latest"},
            )
        with self.assertRaises(ValidationError):
            FunctionResourceRequest(
                workspace="acme",
                spec={
                    "name": "thumbnail",
                    "image": "ghcr.io/acme/thumbnail:v1.0.0",
                    "command": "id",
                },
            )

    def test_function_requires_consistent_scaling_and_public_route_settings(self):
        with self.assertRaises(ValidationError):
            FunctionResourceRequest(
                workspace="acme",
                spec={
                    "name": "thumbnail",
                    "image": "ghcr.io/acme/thumbnail:v1.0.0",
                    "min_replicas": 5,
                    "max_replicas": 2,
                },
            )
        with self.assertRaises(ValidationError):
            FunctionResourceRequest(
                workspace="acme",
                spec={
                    "name": "thumbnail",
                    "image": "ghcr.io/acme/thumbnail:v1.0.0",
                    "hostname": "functions.example.com",
                },
            )

    def test_function_resource_document_uses_shared_resource_envelope(self):
        item = {
            "id": "function-id",
            "kind": "Function",
            "workspace": "acme",
            "name": "thumbnail",
            "generation": 1,
            "desired": {"image": "ghcr.io/acme/thumbnail:v1.0.0"},
            "observed": {"state": "idle", "runtime": "spin-wasm"},
            "phase": "ready",
            "observed_generation": 1,
            "retry_count": 0,
            "last_error": None,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        document = to_resource_document(item)
        self.assertEqual(document["kind"], "Function")
        self.assertEqual(document["status"]["observed"]["runtime"], "spin-wasm")

    def test_rejects_unknown_resource_request_fields(self):
        with self.assertRaises(ValidationError):
            ApplicationResourceRequest(
                workspace="acme",
                spec={"name": "portal", "image": "nginx:1.27", "command": "id"},
            )

    def test_resource_document_separates_desired_and_observed_state(self):
        item = {
            "id": "resource-id",
            "kind": "Application",
            "workspace": "acme",
            "name": "portal",
            "generation": 2,
            "desired": {"image": "nginx:1.27"},
            "observed": {"state": "ready"},
            "phase": "ready",
            "observed_generation": 2,
            "retry_count": 0,
            "last_error": None,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        document = to_resource_document(item)
        self.assertEqual(document["apiVersion"], "gamgee.io/v1alpha1")
        self.assertEqual(document["metadata"]["generation"], 2)
        self.assertEqual(document["spec"]["image"], "nginx:1.27")
        self.assertEqual(document["status"]["observed"]["state"], "ready")


if __name__ == "__main__":
    unittest.main()
