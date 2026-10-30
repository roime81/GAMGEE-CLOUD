import unittest

from pydantic import ValidationError

from app.validator import ProvisionRequest, build_tenant_manifest


class ProvisionRequestTests(unittest.TestCase):
    def test_builds_bounded_tenant_manifest(self):
        request = ProvisionRequest(
            prompt="Create a tenant",
            tenant_name="acme",
            owner_email="owner@example.com",
        )

        manifest = build_tenant_manifest(request)

        self.assertEqual(manifest["metadata"]["name"], "acme")
        self.assertEqual(
            manifest["spec"]["additionalNamespaces"],
            ["acme-production", "acme-staging"],
        )
        self.assertEqual(manifest["spec"]["resourceQuotas"]["hard"]["pods"], "30")

    def test_rejects_unsafe_tenant_names_and_extra_fields(self):
        with self.assertRaises(ValidationError):
            ProvisionRequest(
                prompt="Create a tenant",
                tenant_name="acme; delete everything",
                owner_email="owner@example.com",
            )
        with self.assertRaises(ValidationError):
            ProvisionRequest(
                prompt="Create a tenant",
                tenant_name="acme",
                owner_email="owner@example.com",
                manifest={"kind": "ClusterRoleBinding"},
            )

    def test_rejects_blank_prompt_and_invalid_email(self):
        with self.assertRaises(ValidationError):
            ProvisionRequest(
                prompt="  ",
                tenant_name="acme",
                owner_email="owner@example.com",
            )
        with self.assertRaises(ValidationError):
            ProvisionRequest(
                prompt="Create a tenant",
                tenant_name="acme",
                owner_email="not-an-email",
            )


if __name__ == "__main__":
    unittest.main()
