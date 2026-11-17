import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from tower.recommendation import ai_factory_ready, recommend_local_bundle


class LocalBundleTests(unittest.TestCase):
    def capacity(self, cpu=100, ram=128, disk=1000):
        return {"vcpu": cpu, "available_ram_gib": ram, "free_disk_gib": disk}

    def test_small_single_node_keeps_core_and_skips_optional_capabilities(self):
        bundle = recommend_local_bundle(
            kvm_available=False,
            ai_available=False,
            host_capacity=self.capacity(cpu=2, ram=4, disk=40),
        )
        self.assertEqual(bundle["profiles"], ["core-cloud"])
        skipped = {item["id"] for item in bundle["skipped"]}
        self.assertIn("private-iaas", skipped)
        self.assertIn("ai-enterprise", skipped)
        self.assertIn("hybrid-cloud", skipped)
        self.assertIn("function-cloud", skipped)

    def test_mid_sized_host_adds_security_and_functions_without_heavy_observability(self):
        bundle = recommend_local_bundle(
            kvm_available=False,
            ai_available=False,
            host_capacity=self.capacity(cpu=6, ram=12, disk=100),
        )
        self.assertEqual(bundle["profiles"], ["core-cloud", "enterprise-security", "function-cloud"])
        self.assertNotIn("federated-observability", bundle["profiles"])

    def test_capacity_and_kvm_add_private_iaas_and_observability(self):
        bundle = recommend_local_bundle(
            kvm_available=True,
            ai_available=False,
            host_capacity=self.capacity(cpu=12, ram=24, disk=250),
        )
        self.assertIn("private-iaas", bundle["profiles"])
        self.assertIn("federated-observability", bundle["profiles"])
        self.assertEqual(bundle["required_resources"], {"vcpu": 9, "ram_gib": 17, "disk_gib": 195})

    def test_adds_ai_only_when_ready_and_capacity_is_sufficient(self):
        bundle = recommend_local_bundle(
            kvm_available=True,
            ai_available=True,
            host_capacity=self.capacity(cpu=32, ram=64, disk=500),
        )
        self.assertIn("ai-enterprise", bundle["profiles"])

    def test_ai_env_must_have_real_secrets_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "AUTHENTIK_POSTGRES_PASSWORD=" + "a" * 32 + "\n"
                "AUTHENTIK_SECRET_KEY=" + "b" * 32 + "\n"
                "GAMGEE_API_TOKEN=" + "c" * 32 + "\n"
                "FACTORY_POSTGRES_PASSWORD=" + "d" * 32 + "\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            with patch("tower.recommendation.shutil.which", return_value="/usr/bin/docker"), patch(
                "tower.recommendation.subprocess.run",
                side_effect=[
                    Mock(returncode=0, stdout="Docker Compose version v2", stderr=""),
                    Mock(returncode=0, stdout="27.0.0", stderr=""),
                ],
            ):
                self.assertTrue(ai_factory_ready(Path(tmp)))

    def test_ai_env_placeholders_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "AUTHENTIK_POSTGRES_PASSWORD=replace-with-a-long-random-password\n"
                "AUTHENTIK_SECRET_KEY=replace-with-a-long-random-secret\n"
                "GAMGEE_API_TOKEN=replace-with-a-long-random-token\n"
                "FACTORY_POSTGRES_PASSWORD=replace-with-a-long-random-password\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            self.assertFalse(ai_factory_ready(Path(tmp)))

    def test_ai_env_with_group_or_world_permissions_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "AUTHENTIK_POSTGRES_PASSWORD=" + "a" * 32 + "\n"
                "AUTHENTIK_SECRET_KEY=" + "b" * 32 + "\n"
                "GAMGEE_API_TOKEN=" + "c" * 32 + "\n"
                "FACTORY_POSTGRES_PASSWORD=" + "d" * 32 + "\n",
                encoding="utf-8",
            )
            env_file.chmod(0o644)
            with patch("tower.recommendation.shutil.which", return_value="/usr/bin/docker"):
                self.assertFalse(ai_factory_ready(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
