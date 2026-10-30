import os
import unittest
from unittest.mock import patch

from app.agent_core import interpret_intent


class IntentTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_deterministic_mode_accepts_tenant_request(self):
        result = interpret_intent("Please create a tenant for my team")
        self.assertTrue(result.provision_tenant)

    @patch.dict(os.environ, {}, clear=True)
    def test_deterministic_mode_rejects_unrelated_request(self):
        result = interpret_intent("Tell me a joke")
        self.assertFalse(result.provision_tenant)


if __name__ == "__main__":
    unittest.main()
