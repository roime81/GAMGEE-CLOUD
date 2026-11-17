import unittest

from tower.catalog import CatalogError, load_catalog, resolve_profiles


class CatalogTests(unittest.TestCase):
    def test_all_seven_profiles_are_registered(self):
        self.assertEqual(len(load_catalog()), 7)

    def test_dependencies_are_resolved_before_profiles(self):
        ordered = resolve_profiles(["hybrid-cloud", "function-cloud"], load_catalog())
        self.assertEqual(ordered[0], "core-cloud")
        self.assertLess(ordered.index("enterprise-security"), ordered.index("hybrid-cloud"))
        self.assertLess(ordered.index("federated-observability"), ordered.index("hybrid-cloud"))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(CatalogError):
            resolve_profiles(["not-a-profile"], load_catalog())

    def test_empty_selection_is_rejected(self):
        with self.assertRaises(CatalogError):
            resolve_profiles([], load_catalog())


if __name__ == "__main__":
    unittest.main()
