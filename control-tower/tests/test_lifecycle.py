import unittest

from tower.lifecycle import resume_replicas


class LifecycleTests(unittest.TestCase):
    def test_restores_previous_replica_count(self):
        deployment = {
            "metadata": {"annotations": {"gamgee.cloud/previous-replicas": "4"}}
        }
        self.assertEqual(resume_replicas(deployment), 4)

    def test_defaults_to_one_when_annotation_is_missing_or_invalid(self):
        self.assertEqual(resume_replicas({}), 1)
        self.assertEqual(
            resume_replicas({"metadata": {"annotations": {"gamgee.cloud/previous-replicas": "nope"}}}),
            1,
        )

    def test_never_resumes_zero_or_exceeds_limit(self):
        self.assertEqual(
            resume_replicas({"metadata": {"annotations": {"gamgee.cloud/previous-replicas": "0"}}}),
            1,
        )
        self.assertEqual(
            resume_replicas({"metadata": {"annotations": {"gamgee.cloud/previous-replicas": "99"}}}),
            20,
        )


if __name__ == "__main__":
    unittest.main()
