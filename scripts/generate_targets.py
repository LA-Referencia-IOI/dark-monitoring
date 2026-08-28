#!/usr/bin/env python3
"""Generate Prometheus file-SD targets from dARK deployment configuration.

This deliberately reads only endpoint configuration; secret values in .env and
.env.integration are never copied into generated files.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse


MONITORING_ROOT = Path(__file__).resolve().parents[1]
DEPLOYER_ROOT = MONITORING_ROOT.parents[1]
TARGET_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*$")
LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*$")


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


def configured_http_targets(value: object, source: str) -> list[dict]:
    """Validate the deployer contract or an inline list and convert it to file-SD."""
    if isinstance(value, dict):
        if value.get("version") != 1 or not isinstance(value.get("targets"), list):
            raise ValueError(f"{source} must use monitoring target schema version 1")
        value = value["targets"]
    if not isinstance(value, list):
        raise ValueError(f"{source} must be a JSON array or monitoring target contract")

    result: list[dict] = []
    names: set[str] = set()
    for index, target in enumerate(value):
        if not isinstance(target, dict):
            raise ValueError(f"{source}[{index}] must be an object")
        unknown = set(target) - {"name", "url", "labels"}
        if unknown:
            raise ValueError(f"{source}[{index}] has unsupported fields: {', '.join(sorted(unknown))}")
        name = target.get("name")
        url = target.get("url")
        labels = target.get("labels", {})
        if not isinstance(name, str) or not TARGET_NAME.fullmatch(name):
            raise ValueError(f"{source}[{index}].name is invalid")
        if name in names:
            raise ValueError(f"{source} contains duplicate target name {name!r}")
        names.add(name)
        if not isinstance(url, str):
            raise ValueError(f"{source}[{index}].url must be an HTTP(S) URL")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"{source}[{index}].url must be an HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError(f"{source}[{index}].url must not contain credentials")
        if not isinstance(labels, dict):
            raise ValueError(f"{source}[{index}].labels must be an object")
        normalized_labels = {"service": name}
        for key, label_value in labels.items():
            if not isinstance(key, str) or not LABEL_NAME.fullmatch(key):
                raise ValueError(f"{source}[{index}].labels contains an invalid name")
            if not isinstance(label_value, str) or not label_value:
                raise ValueError(f"{source}[{index}].labels.{key} must be a non-empty string")
            normalized_labels[key] = label_value
        result.append({
            "targets": [host_visible_to_container(url)],
            "labels": normalized_labels,
        })
    return result


def monitoring_http_targets(env: dict[str, str], env_path: Path) -> list[dict] | None:
    """Return an authoritative configured target list, or None for discovery."""
    contract_path = env.get("DARK_MONITOR_TARGETS_FILE", "").strip()
    inline = env.get("DARK_MONITOR_TARGETS_JSON", "").strip()
    if contract_path and inline and inline != "[]":
        raise ValueError("set only DARK_MONITOR_TARGETS_FILE or DARK_MONITOR_TARGETS_JSON")
    if contract_path:
        path = Path(contract_path)
        if not path.is_absolute():
            path = env_path.parent / path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"monitoring target file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"monitoring target file is not valid JSON: {path}") from exc
        return configured_http_targets(value, str(path))
    if inline:
        try:
            value = json.loads(inline)
        except json.JSONDecodeError as exc:
            raise ValueError("DARK_MONITOR_TARGETS_JSON is not valid JSON") from exc
        if value:
            return configured_http_targets(value, "DARK_MONITOR_TARGETS_JSON")
    return None


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
    parser.add_argument("--env", type=Path, default=DEPLOYER_ROOT / ".env")
    parser.add_argument("--monitoring-env", type=Path, default=MONITORING_ROOT / ".env")
    parser.add_argument("--integration-env", type=Path, default=DEPLOYER_ROOT / ".env.integration")
    parser.add_argument("--topology", type=Path, default=DEPLOYER_ROOT / "storage-topology.json")
    parser.add_argument("--dashboard-env", type=Path, default=DEPLOYER_ROOT / "components/frontend/dashboard-web/.env")
    parser.add_argument("--output-dir", type=Path, default=MONITORING_ROOT / "generated")
    args = parser.parse_args()

    env = read_env(args.env)
    monitoring_env = read_env(args.monitoring_env)
    integration = read_env(args.integration_env)
    dashboard = read_env(args.dashboard_env)
    http_targets: list[dict] = []
    ipfs_targets: list[dict] = []
    rpc_targets: list[dict] = []
    configured_targets = monitoring_http_targets(monitoring_env, args.monitoring_env)
    if configured_targets is not None:
        http_targets.extend(configured_targets)
    else:
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
                if configured_targets is None:
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
