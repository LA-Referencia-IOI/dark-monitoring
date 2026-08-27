import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).parents[1] / "scripts" / "generate_targets.py"
SPEC = importlib.util.spec_from_file_location("generate_targets", MODULE)
generator = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(generator)

DNS_MODULE = Path(__file__).parents[1] / "scripts" / "configure_docker_dns.py"
DNS_SPEC = importlib.util.spec_from_file_location("configure_docker_dns", DNS_MODULE)
dns_helper = importlib.util.module_from_spec(DNS_SPEC)
assert DNS_SPEC and DNS_SPEC.loader
DNS_SPEC.loader.exec_module(dns_helper)


class TargetGenerationTests(unittest.TestCase):
    def test_loopback_is_visible_from_blackbox_container(self):
        self.assertEqual(
            generator.host_visible_to_container("http://127.0.0.1:8003/health"),
            "http://host.docker.internal:8003/health",
        )

    def test_topology_generates_kubo_and_cluster_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            topology = root / "storage-topology.json"
            topology.write_text(json.dumps({"sites": [{"id": "site-a", "peers": [{
                "id": "storage-1",
                "host_ipfs_api_url": "http://127.0.0.1:5001",
                "host_cluster_api_url": "http://127.0.0.1:9094",
            }]}]}))
            output = root / "out"
            old_argv = __import__("sys").argv
            try:
                __import__("sys").argv = ["generate", "--env", str(root / "missing"), "--integration-env", str(root / "missing2"), "--topology", str(topology), "--output-dir", str(output)]
                generator.main()
            finally:
                __import__("sys").argv = old_argv
            http = json.loads((output / "http-targets.json").read_text())
            ipfs = json.loads((output / "ipfs-targets.json").read_text())
            self.assertTrue(any(item["labels"]["service"] == "ipfs-cluster" for item in http))
            self.assertEqual(ipfs[0]["targets"], ["http://host.docker.internal:5001/api/v0/version"])

    def test_topology_prefers_vpn_endpoint_over_host_loopback(self):
        peer = {
            "ipfs_api_url": "http://10.20.30.31:5001",
            "host_ipfs_api_url": "http://127.0.0.1:5001",
        }
        self.assertEqual(
            generator.topology_endpoint(peer, "ipfs_api_url", "host_ipfs_api_url"),
            "http://10.20.30.31:5001",
        )


class DockerDnsTests(unittest.TestCase):
    def test_dry_run_preserves_static_dns(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "daemon.json"
            config.write_text('{"dns": ["8.8.8.8"], "log-driver": "local"}')
            self.assertFalse(dns_helper.remove_static_dns(config, apply=False))
            self.assertEqual(dns_helper.load_config(config)["dns"], ["8.8.8.8"])

    def test_no_dns_needs_no_change(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "daemon.json"
            config.write_text('{"log-driver": "local"}')
            self.assertFalse(dns_helper.remove_static_dns(config, apply=False))


if __name__ == "__main__":
    unittest.main()
