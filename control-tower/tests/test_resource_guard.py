import unittest

from tower.resource_guard import is_gamgee_managed


class ResourceOwnershipTests(unittest.TestCase):
    def test_requires_both_gamgee_manager_and_matching_tenant(self):
        resource = {
            "metadata": {
                "labels": {
                    "gamgee.cloud/managed-by": "gamgee",
                    "gamgee.cloud/tenant": "acme",
                }
            }
        }
        self.assertTrue(is_gamgee_managed(resource, "acme"))
        self.assertFalse(is_gamgee_managed(resource, "other"))

    def test_rejects_missing_or_foreign_management_label(self):
        self.assertFalse(is_gamgee_managed({}, "acme"))
        self.assertFalse(
            is_gamgee_managed(
                {"metadata": {"labels": {"gamgee.cloud/tenant": "acme"}}},
                "acme",
            )
        )


if __name__ == "__main__":
    unittest.main()
