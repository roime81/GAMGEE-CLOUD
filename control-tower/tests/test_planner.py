import unittest

from tower.planner import build_plan


class PlannerTests(unittest.TestCase):
    def test_plan_hash_is_stable(self):
        first = build_plan(["core-cloud", "function-cloud"])
        second = build_plan(["core-cloud", "function-cloud"])
        self.assertEqual(first["plan_hash"], second["plan_hash"])

    def test_dependency_is_included(self):
        plan = build_plan(["function-cloud"])
        self.assertIn("core-cloud", plan["ordered_profiles"])

    def test_plan_contains_preflight_and_resources(self):
        plan = build_plan(["core-cloud"])
        self.assertIn("apply_ready", plan)
        self.assertIn("vcpu", plan["required_resources"])
        self.assertIn("missing_tools", plan)


if __name__ == "__main__":
    unittest.main()
