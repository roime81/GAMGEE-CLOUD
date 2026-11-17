import tempfile
import unittest
from pathlib import Path

from tower.resource_engine import ResourceEngine
from tower.resource_store import ResourceStore


class FakeDriver:
    kind = "Application"

    def __init__(self, store=None, fail_once=False, update_during_reconcile=False):
        self.store = store
        self.fail_once = fail_once
        self.update_during_reconcile = update_during_reconcile
        self.calls = []

    def reconcile(self, resource):
        self.calls.append((resource["generation"], dict(resource["desired"])))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("transient fake failure")
        if self.update_during_reconcile:
            self.update_during_reconcile = False
            self.store.upsert(
                kind="Application",
                workspace=resource["workspace"],
                name=resource["name"],
                desired={"image": "nginx:1.28", "replicas": 2},
            )
        if resource["desired"].get("deleted"):
            if getattr(self, "delete_pending_once", False):
                self.delete_pending_once = False
                return {"state": "deleting", "remaining_resources": ["deployment.apps/portal"]}
            return {"state": "deleted"}
        if self.calls[-1][0] == 1 and self.update_during_reconcile is False and getattr(self, "progress_once", False):
            self.progress_once = False
            return {"state": "progressing", "ready_replicas": 0}
        return {"state": "ready", "ready_replicas": resource["desired"].get("replicas", 1)}

    def observe(self, resource):
        self.calls.append(("observe", resource["generation"]))
        return {"state": "ready", "ready_replicas": resource["desired"].get("replicas", 1)}


class ResourceEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ResourceStore(Path(self.temp.name) / "resources.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_engine_reconciles_desired_state_and_records_observed_state(self):
        driver = FakeDriver()
        engine = ResourceEngine(self.store, [driver])
        created, operation_id, _ = engine.submit(
            kind="Application",
            workspace="acme",
            name="portal",
            desired={"image": "nginx:1.27", "replicas": 1},
        )
        result = engine.reconcile_due()
        loaded = self.store.get(created["id"])
        self.assertEqual(result, {"claimed": 1, "succeeded": 1, "failed": 0})
        self.assertEqual(loaded["phase"], "ready")
        self.assertEqual(loaded["observed"]["state"], "ready")
        self.assertEqual(self.store.get_operation(operation_id)["status"], "succeeded")

    def test_transient_driver_failure_is_retried(self):
        driver = FakeDriver(fail_once=True)
        engine = ResourceEngine(self.store, [driver])
        resource, operation_id, _ = engine.submit(
            kind="Application",
            workspace="acme",
            name="portal",
            desired={"image": "nginx:1.27", "replicas": 1},
        )
        failed = engine.reconcile_due()
        self.assertEqual(failed["failed"], 1)
        self.assertEqual(self.store.get(resource["id"])["phase"], "error")
        self.assertEqual(self.store.get_operation(operation_id)["status"], "retrying")
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE resources SET next_attempt_at=? WHERE id=?",
                ("2000-01-01T00:00:00+00:00", resource["id"]),
            )
        retried = engine.reconcile_due()
        self.assertEqual(retried["succeeded"], 1)
        self.assertEqual(self.store.get(resource["id"])["phase"], "ready")
        self.assertEqual(self.store.get_operation(operation_id)["attempts"], 2)

    def test_new_generation_during_reconcile_is_not_lost(self):
        driver = FakeDriver(store=self.store, update_during_reconcile=True)
        engine = ResourceEngine(self.store, [driver])
        resource, _, _ = engine.submit(
            kind="Application",
            workspace="acme",
            name="portal",
            desired={"image": "nginx:1.27", "replicas": 1},
        )
        engine.reconcile_due()
        after_first = self.store.get(resource["id"])
        self.assertEqual(after_first["generation"], 2)
        self.assertEqual(after_first["phase"], "pending")
        engine.reconcile_due()
        final = self.store.get(resource["id"])
        self.assertEqual(final["phase"], "ready")
        self.assertEqual(final["observed_generation"], 2)
        self.assertEqual(final["observed"]["ready_replicas"], 2)

    def test_progressing_resource_is_observed_until_ready_without_reapplying(self):
        driver = FakeDriver()
        driver.progress_once = True
        engine = ResourceEngine(self.store, [driver])
        resource, _, _ = engine.submit(
            kind="Application",
            workspace="acme",
            name="portal",
            desired={"image": "nginx:1.27", "replicas": 1},
        )
        first = engine.reconcile_due()
        self.assertEqual(first["succeeded"], 1)
        self.assertEqual(self.store.get(resource["id"])["phase"], "progressing")
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE resources SET next_attempt_at=? WHERE id=?",
                ("2000-01-01T00:00:00+00:00", resource["id"]),
            )
        second = engine.reconcile_due()
        self.assertEqual(second["succeeded"], 1)
        self.assertEqual(self.store.get(resource["id"])["phase"], "ready")
        self.assertEqual(driver.calls[-1][0], "observe")

    def test_delete_remains_pending_until_driver_confirms_absence(self):
        driver = FakeDriver()
        engine = ResourceEngine(self.store, [driver])
        resource, _, _ = engine.submit(
            kind="Application",
            workspace="acme",
            name="portal",
            desired={"image": "nginx:1.27", "replicas": 1},
        )
        engine.reconcile_due()
        driver.delete_pending_once = True
        deleted, operation_id, _ = engine.delete(resource["id"], workspace="acme")
        self.assertEqual(deleted["phase"], "deleting")

        first = engine.reconcile_due()
        self.assertEqual(first["succeeded"], 1)
        pending = self.store.get(resource["id"])
        self.assertEqual(pending["phase"], "deleting")
        self.assertEqual(self.store.get_operation(operation_id)["status"], "waiting")

        with self.store._connection() as connection:
            connection.execute(
                "UPDATE resources SET next_attempt_at=? WHERE id=?",
                ("2000-01-01T00:00:00+00:00", resource["id"]),
            )
        second = engine.reconcile_due()
        self.assertEqual(second["succeeded"], 1)
        final = self.store.get(resource["id"])
        self.assertEqual(final["phase"], "deleted")
        self.assertEqual(self.store.get_operation(operation_id)["status"], "succeeded")

    def test_unregistered_resource_kind_is_rejected(self):
        engine = ResourceEngine(self.store, [FakeDriver()])
        with self.assertRaises(ValueError):
            engine.submit(
                kind="Machine",
                workspace="acme",
                name="vm1",
                desired={"image": "ubuntu"},
            )


if __name__ == "__main__":
    unittest.main()
