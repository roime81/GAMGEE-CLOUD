import tempfile
import unittest
from pathlib import Path

from tower.store import JobStore


class StoreTests(unittest.TestCase):
    def test_job_lifecycle_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3")
            job = store.create(["core-cloud"], "a" * 64, {"mode": "dry-run"})
            store.update(job["id"], status="succeeded", append_log="complete\n")
            loaded = store.get(job["id"])
            self.assertEqual(loaded["status"], "succeeded")
            self.assertIn("complete", loaded["logs"])


if __name__ == "__main__":
    unittest.main()
