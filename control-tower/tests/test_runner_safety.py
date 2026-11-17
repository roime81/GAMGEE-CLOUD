import unittest

from tower.runner import _run_playbook


class RunnerSafetyTests(unittest.TestCase):
    def test_runner_rejects_paths_outside_approved_directory(self):
        with self.assertRaises(ValueError):
            _run_playbook("scripts/teardown.sh", 1)

    def test_runner_rejects_parent_traversal(self):
        with self.assertRaises(ValueError):
            _run_playbook("automation/ansible/../../scripts/teardown.sh", 1)


if __name__ == "__main__":
    unittest.main()
