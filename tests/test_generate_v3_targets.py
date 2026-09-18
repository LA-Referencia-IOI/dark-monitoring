import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).parents[1] / "scripts" / "generate_v3_targets.py"
SPEC = importlib.util.spec_from_file_location("generate_v3_targets", MODULE)
generator = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(generator)


def snapshot() -> dict:
    return {
        "version": 3,
        "deployment": {"id": "dark-example"},
        "machines": {
            "apps": {"execution": "local", "site": "site-a", "addresses": {}},
            "remote": {
                "execution": "ssh",
                "site": "site-b",
                "addresses": {"vpn": "10.20.0.8"},
            },
        },
        "services": {
            "admin-api": {"type": "admin-api", "machine": "apps", "configuration": {}},
            "store-api": {"type": "store-api", "machine": "apps", "configuration": {}},
            "explorer": {"type": "explorer", "machine": "apps", "configuration": {}},
            "rpc01": {"type": "besu-rpc", "machine": "apps", "configuration": {}},
            "remote-kubo": {
                "type": "ipfs-kubo",
                "machine": "remote",
                "configuration": {},
                "exposure": {"mode": "private", "network": "vpn", "port": 5001},
            },
            "hidden-resolver": {
                "type": "resolver-api",
                "machine": "remote",
                "configuration": {},
            },
        },
    }


class V3TargetGenerationTests(unittest.TestCase):
    def test_local_services_use_docker_dns_and_managed_network(self):
        targets, override = generator.generate(snapshot())
        admin = next(item for item in targets["http"] if item["labels"]["service"] == "admin-api")
        self.assertEqual(admin["targets"], ["http://admin-api:8000/health"])
        self.assertEqual(admin["labels"]["deployment"], "dark-example")
        self.assertEqual(admin["labels"]["site"], "site-a")
        self.assertEqual(
            override["networks"]["dark_dark_example_apps"],
            {"external": True, "name": "dark-example-apps"},
        )

    def test_store_readiness_probes_are_distinct(self):
        targets, _ = generator.generate(snapshot())
        store = [item for item in targets["http"] if item["labels"]["service"] == "store-api"]
        self.assertEqual({item["labels"]["probe"] for item in store}, {"live", "read", "write"})

    def test_explorer_health_and_rpc_proxy_are_monitored(self):
        targets, _ = generator.generate(snapshot())
        explorer_http = next(
            item for item in targets["http"] if item["labels"]["service"] == "explorer"
        )
        explorer_rpc = next(
            item for item in targets["rpc"] if item["labels"]["service"] == "explorer"
        )
        self.assertEqual(explorer_http["targets"], ["http://explorer:80/health"])
        self.assertEqual(explorer_rpc["targets"], ["http://explorer:80/jsonrpc"])

    def test_remote_service_uses_declared_private_exposure(self):
        targets, _ = generator.generate(snapshot())
        self.assertEqual(targets["ipfs"][0]["targets"], ["http://10.20.0.8:5001/api/v0/version"])

    def test_unexposed_remote_service_is_not_invented(self):
        targets, _ = generator.generate(snapshot())
        self.assertFalse(any(item["labels"]["service"] == "hidden-resolver" for item in targets["http"]))

    def test_cli_writes_prometheus_and_compose_contracts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "deployment-topology.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            output = root / "generated"
            old_argv = __import__("sys").argv
            try:
                __import__("sys").argv = ["generate", "--snapshot", str(source), "--output-dir", str(output)]
                self.assertEqual(generator.main(), 0)
            finally:
                __import__("sys").argv = old_argv
            self.assertTrue((output / "http-targets.json").is_file())
            self.assertTrue((output / "compose.networks.json").is_file())


if __name__ == "__main__":
    unittest.main()
