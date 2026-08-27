#!/usr/bin/env python3
"""Generate Prometheus file-SD targets from dARK deployment configuration.

This deliberately reads only endpoint configuration; secret values in .env and
.env.integration are never copied into generated files.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse


ROOT = Path(__file__).resolve().parents[2]


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def host_visible_to_container(url: str) -> str:
    """Translate a host-local URL for the Docker-based blackbox exporter."""
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return url.rstrip("/")
    netloc = "host.docker.internal" + (f":{parsed.port}" if parsed.port else "")
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", "")).rstrip("/")


def add(targets: list[dict], url: str, service: str, site: str = "default", node: str = "") -> None:
    if not url:
        return
    labels = {"service": service, "site": site}
    if node:
        labels["node"] = node
    targets.append({"targets": [host_visible_to_container(url)], "labels": labels})


def topology_endpoint(peer: dict, network_key: str, local_key: str) -> str:
    """Prefer the VPN endpoint, except for developer Docker service names."""
    network = peer.get(network_key, "")
    hostname = urlparse(network).hostname or ""
    if network and not hostname.startswith("dark-"):
        return network
    return peer.get(local_key, "") or network


def app_urls(env: dict[str, str], dashboard_env: dict[str, str]) -> dict[str, str]:
    # Public URLs take precedence for a decoupled deployment. Defaults match the
    # local deployer and make developer monitoring work without extra settings.
    defaults = {
        "admin": "http://127.0.0.1:8000",
        "minter": "http://127.0.0.1:8001",
        "resolver": "http://127.0.0.1:8002",
        "store_api": "http://127.0.0.1:8003",
        "dashboard": "http://127.0.0.1:8081",
    }
    result = {}
    for name, default in defaults.items():
        key = f"PRODUCTION_{name.upper()}_PUBLIC_URL"
        result[name] = env.get(key) or env.get(f"SANDBOX_{name.upper()}_PUBLIC_URL") or default
    # Dashboard URLs vary by frontend framework; use the explicitly advertised
    # endpoint first, then common dashboard variables if present.
    result["dashboard"] = (
        env.get("PRODUCTION_DASHBOARD_PUBLIC_URL")
        or dashboard_env.get("DASHBOARD_URL")
        or dashboard_env.get("APP_URL")
        or dashboard_env.get("VITE_APP_URL")
        or result["dashboard"]
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--integration-env", type=Path, default=ROOT / ".env.integration")
    parser.add_argument("--topology", type=Path, default=ROOT / "storage-topology.json")
    parser.add_argument("--dashboard-env", type=Path, default=ROOT / "components/frontend/dashboard-web/.env")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dark-monitoring/generated")
    args = parser.parse_args()

    env = read_env(args.env)
    integration = read_env(args.integration_env)
    dashboard = read_env(args.dashboard_env)
    http_targets: list[dict] = []
    ipfs_targets: list[dict] = []
    rpc_targets: list[dict] = []
    urls = app_urls(env, dashboard)
    add(http_targets, urls["admin"] + "/health", "admin-api")
    add(http_targets, urls["minter"] + "/health", "minter-api")
    add(http_targets, urls["resolver"] + "/health", "resolver-api")
    add(http_targets, urls["store_api"] + "/health/live", "store-api-live")
    add(http_targets, urls["store_api"] + "/health/read", "store-api-read")
    add(http_targets, urls["store_api"] + "/health/write", "store-api-write")
    add(http_targets, urls["dashboard"], "dashboard")
    add(rpc_targets, integration.get("DARK_RPC_URL") or env.get("RPC_URL"), "blockchain-rpc")

    if args.topology.exists():
        topology = json.loads(args.topology.read_text(encoding="utf-8"))
        for site in topology.get("sites", []):
            site_id = site.get("id", "unknown")
            for peer in site.get("peers", []):
                peer_id = peer.get("id", "unknown")
                ipfs = topology_endpoint(peer, "ipfs_api_url", "host_ipfs_api_url")
                cluster = topology_endpoint(peer, "cluster_api_url", "host_cluster_api_url")
                # Kubo's version endpoint is POST-only, while Cluster's /id is
                # safe to probe with GET.
                add(http_targets, cluster + "/id" if cluster else "", "ipfs-cluster", site_id, peer_id)
                if ipfs:
                    add(ipfs_targets, ipfs + "/api/v0/version", "ipfs-kubo", site_id, peer_id)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "http-targets.json").write_text(json.dumps(http_targets, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "rpc-targets.json").write_text(json.dumps(rpc_targets, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "ipfs-targets.json").write_text(json.dumps(ipfs_targets, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(http_targets)} HTTP, {len(ipfs_targets)} IPFS and {len(rpc_targets)} RPC targets in {args.output_dir}")


if __name__ == "__main__":
    main()
