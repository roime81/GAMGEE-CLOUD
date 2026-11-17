import tempfile
import unittest
from pathlib import Path

from tower.resource_store import IdempotencyConflict, ResourceStore


class ResourceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ResourceStore(Path(self.temp.name) / "resources.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, desired=None, key=None):
        return self.store.upsert(
            kind="Application",
            workspace="acme",
            name="portal",
            desired=desired or {"image": "nginx:1.27", "replicas": 1},
            idempotency_key=key,
        )

    def test_create_and_idempotent_replay_return_same_resource_and_operation(self):
        first, operation, replayed = self.submit(key="request-1")
        replay, replay_operation, was_replay = self.submit(key="request-1")
        self.assertFalse(replayed)
        self.assertTrue(was_replay)
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(operation, replay_operation)
        self.assertEqual(first["generation"], 1)

    def test_reusing_idempotency_key_with_different_body_conflicts(self):
        self.submit(key="request-1")
        with self.assertRaises(IdempotencyConflict):
            self.submit({"image": "nginx:1.28", "replicas": 1}, key="request-1")

    def test_new_desired_state_increments_generation_and_creates_operation(self):
        first, first_operation, _ = self.submit()
        second, second_operation, replayed = self.submit({"image": "nginx:1.28", "replicas": 2})
        self.assertFalse(replayed)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["generation"], 2)
        self.assertNotEqual(first_operation, second_operation)
        operations = self.store.list_operations(first["id"])
        self.assertEqual([item["generation"] for item in operations], [2, 1])

    def test_only_one_worker_can_claim_a_due_generation(self):
        resource, _, _ = self.submit()
        first = self.store.claim_due()
        second = self.store.claim_due()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["id"], resource["id"])
        self.assertEqual(second, [])

    def test_success_persists_observed_generation_and_operation(self):
        resource, operation_id, _ = self.submit()
        self.store.claim_due()
        observed = {"state": "ready", "ready_replicas": 1}
        self.store.mark_success(
            resource_id=resource["id"],
            generation=resource["generation"],
            observed=observed,
        )
        loaded = self.store.get(resource["id"])
        operation = self.store.get_operation(operation_id)
        self.assertEqual(loaded["phase"], "ready")
        self.assertEqual(loaded["observed_generation"], 1)
        self.assertEqual(loaded["observed"], observed)
        self.assertEqual(operation["status"], "succeeded")

    def test_failure_records_error_and_schedules_retry(self):
        resource, operation_id, _ = self.submit()
        self.store.claim_due()
        self.store.mark_failure(
            resource_id=resource["id"],
            generation=resource["generation"],
            error="temporary API timeout",
        )
        loaded = self.store.get(resource["id"])
        operation = self.store.get_operation(operation_id)
        self.assertEqual(loaded["phase"], "error")
        self.assertEqual(loaded["retry_count"], 1)
        self.assertIn("temporary API timeout", loaded["last_error"])
        self.assertEqual(operation["status"], "retrying")
        self.assertEqual(operation["attempts"], 1)

    def test_delete_is_a_new_desired_generation_and_tombstones_after_success(self):
        resource, _, _ = self.submit()
        deleted, operation_id, replayed = self.store.delete(resource["id"], workspace="acme")
        self.assertFalse(replayed)
        self.assertEqual(deleted["phase"], "deleting")
        self.assertEqual(deleted["generation"], 2)
        claimed = self.store.claim_due()
        self.assertEqual(claimed[0]["desired"], {"deleted": True})
        self.store.mark_success(
            resource_id=resource["id"],
            generation=2,
            observed={"state": "deleted"},
            deleted=True,
        )
        self.assertEqual(self.store.get(resource["id"])["phase"], "deleted")
        self.assertEqual(self.store.get_operation(operation_id)["status"], "succeeded")
        self.assertEqual(self.store.list(), [])

    def test_delete_idempotency_key_is_scoped_to_workspace_and_payload(self):
        resource, _, _ = self.submit()
        _, op1, replay1 = self.store.delete(resource["id"], workspace="acme", idempotency_key="delete-1")
        _, op2, replay2 = self.store.delete(resource["id"], workspace="acme", idempotency_key="delete-1")
        self.assertFalse(replay1)
        self.assertTrue(replay2)
        self.assertEqual(op1, op2)


if __name__ == "__main__":
    unittest.main()
