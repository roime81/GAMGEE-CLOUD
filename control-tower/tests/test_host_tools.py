import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tower.host_tools import resolve_tool


class HostToolResolutionTests(unittest.TestCase):
    def test_prefers_system_path_when_available(self):
        with patch("tower.host_tools.shutil.which", return_value="/usr/bin/ansible-playbook"):
            self.assertEqual(resolve_tool("ansible-playbook"), "/usr/bin/ansible-playbook")

    def test_finds_ansible_inside_control_tower_virtual_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "control-tower" / ".venv" / "bin" / "ansible-playbook"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
            executable.chmod(0o755)
            with patch("tower.host_tools.shutil.which", return_value=None):
                self.assertEqual(
                    resolve_tool("ansible-playbook", root=Path(temp)),
                    str(executable),
                )

    def test_does_not_accept_path_fragments_as_tool_names(self):
        with patch("tower.host_tools.shutil.which") as which:
            self.assertIsNone(resolve_tool("../ansible-playbook"))
            which.assert_not_called()


if __name__ == "__main__":
    unittest.main()
