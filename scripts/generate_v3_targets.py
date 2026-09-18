#!/usr/bin/env python3
"""Generate Blackbox targets from an applied dark-deployer v3 snapshot.

The deployed snapshot is authoritative: an edited inventory that has not been
applied must not change monitoring. Local services are reached through their
managed Docker network. Remote services require a declared exposure/listener
that is reachable from the monitoring host.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse


DEFAULT_PORTS = {
    "besu-rpc": 8545,
    "besu-observer": 8545,
    "admin-api": 8000,
    "minter-api": 8001,
    "resolver-api": 8002,
    "store-api": 8003,
    "dashboard": 8080,
    "ipfs-kubo": 5001,
    "ipfs-cluster": 9094,
}


def host_visible_to_container(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return url.rstrip("/")
    netloc = "host.docker.internal" + (f":{parsed.port}" if parsed.port else "")
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", "")).rstrip("/")


def _endpoint(service_id: str, service: dict, machine: dict) -> str | None:
    port = int(service.get("configuration", {}).get("port", DEFAULT_PORTS.get(service.get("type"), 0)))
    if not port:
        return None
    if machine.get("execution") in {"local", "docker-lab"}:
        return f"http://{service_id}:{port}"
    candidates = [service.get("exposure"), *service.get("listeners", [])]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        address = candidate.get("advertise_address")
        if not address:
            network = candidate.get("network")
            address = machine.get("addresses", {}).get(network, "") if network else ""
        if address:
            advertised_port = int(candidate.get("advertise_port") or candidate.get("port") or port)
            return f"http://{address}:{advertised_port}"
    return None


def _target(url: str, deployment: str, service_id: str, service: dict, machine: dict, probe: str) -> dict:
    labels = {
        "deployment": deployment,
        "service": service_id,
        "service_type": service["type"],
        "machine": service["machine"],
        "site": machine.get("site") or "default",
        "probe": probe,
    }
    return {"targets": [host_visible_to_container(url)], "labels": labels}


def generate(snapshot: dict) -> tuple[dict[str, list[dict]], dict]:
    if snapshot.get("version") != 3:
        raise ValueError("deployment snapshot must use topology version 3")
    deployment = snapshot.get("deployment", {}).get("id")
    machines = snapshot.get("machines")
    services = snapshot.get("services")
    if not isinstance(deployment, str) or not deployment:
        raise ValueError("deployment snapshot has no deployment.id")
    if not isinstance(machines, dict) or not isinstance(services, dict):
        raise ValueError("deployment snapshot must contain machines and services objects")

    result: dict[str, list[dict]] = {"http": [], "rpc": [], "ipfs": []}
    local_networks: set[str] = set()
    for service_id, service in sorted(services.items()):
        machine_id = service.get("machine")
        machine = machines.get(machine_id)
        if not isinstance(machine, dict):
            raise ValueError(f"service {service_id} references unknown machine {machine_id}")
        if machine.get("execution") in {"local", "docker-lab"}:
            local_networks.add(f"{deployment}-{machine_id}")
        base = _endpoint(service_id, service, machine)
        if not base:
            continue
        service_type = service.get("type")
        paths: list[tuple[str, str]] = []
        bucket = "http"
        if service_type in {"besu-rpc", "besu-observer"}:
            bucket, paths = "rpc", [("", "json-rpc")]
        elif service_type == "admin-api":
            paths = [("/health", "health")]
        elif service_type == "minter-api":
            paths = [("/health", "health")]
        elif service_type == "resolver-api":
            paths = [("/health", "health")]
        elif service_type == "store-api":
            paths = [("/health/live", "live"), ("/health/read", "read"), ("/health/write", "write")]
        elif service_type == "dashboard":
            paths = [("/", "http")]
        elif service_type == "ipfs-kubo":
            bucket, paths = "ipfs", [("/api/v0/version", "version")]
        elif service_type == "ipfs-cluster":
            paths = [("/id", "identity")]
        for path, probe in paths:
            result[bucket].append(_target(base.rstrip("/") + path, deployment, service_id, service, machine, probe))

    network_items = {"default": {}}
    declared_networks = {}
    for network in sorted(local_networks):
        key = "dark_" + network.replace("-", "_")
        network_items[key] = {}
        declared_networks[key] = {"external": True, "name": network}
    override = {
        "services": {"blackbox-exporter": {"networks": network_items}},
        "networks": declared_networks,
    }
    return result, override


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        targets, override = generate(snapshot)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("http", "rpc", "ipfs"):
        (args.output_dir / f"{name}-targets.json").write_text(
            json.dumps(targets[name], indent=2) + "\n", encoding="utf-8"
        )
    (args.output_dir / "compose.networks.json").write_text(
        json.dumps(override, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Generated {len(targets['http'])} HTTP, {len(targets['ipfs'])} IPFS "
        f"and {len(targets['rpc'])} RPC targets from {args.snapshot}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
