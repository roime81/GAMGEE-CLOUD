import unittest
from unittest.mock import patch, Mock

from tower.cluster import ClusterError, ClusterNotFound, get_json, run_kubectl


class ClusterAdapterTests(unittest.TestCase):
    @patch("tower.cluster.shutil.which", return_value="/usr/bin/kubectl")
    @patch("tower.cluster.subprocess.run")
    def test_uses_argv_without_shell(self, run, _which):
        run.return_value = Mock(returncode=0, stdout='{"kind":"NodeList","items":[]}', stderr="")
        value = get_json("nodes")
        self.assertEqual(value["items"], [])
        args = run.call_args
        self.assertEqual(args.args[0], ["/usr/bin/kubectl", "get", "nodes", "--output", "json"])
        self.assertFalse(args.kwargs.get("shell", False))

    @patch("tower.cluster.shutil.which", return_value="/usr/bin/kubectl")
    @patch("tower.cluster.subprocess.run")
    def test_maps_not_found(self, run, _which):
        run.return_value = Mock(returncode=1, stdout="", stderr='Error from server (NotFound): namespaces "acme" not found')
        with self.assertRaises(ClusterNotFound):
            run_kubectl(["get", "namespace", "acme"])

    @patch("tower.cluster.shutil.which", return_value=None)
    def test_missing_kubectl_is_a_safe_error(self, _which):
        with self.assertRaises(ClusterError):
            run_kubectl(["get", "nodes"])


if __name__ == "__main__":
    unittest.main()
