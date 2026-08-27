#!/usr/bin/env python3
"""Install and start the dARK monitoring stack safely."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PASSWORD = "change-this-before-starting"


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


def run(command: list[str]) -> None:
    print(f"[RUN] {' '.join(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}")


def ensure_env(path: Path) -> dict[str, str]:
    if not path.exists():
        example = PROJECT_ROOT / ".env.example"
        shutil.copyfile(example, path)
        path.chmod(0o600)
        print(f"[INFO] Created {path}. Set GRAFANA_ADMIN_PASSWORD and run this command again.")
        raise RuntimeError("Grafana password requires configuration")
    values = read_env(path)
    password = values.get("GRAFANA_ADMIN_PASSWORD", "")
    if not password or password == DEFAULT_PASSWORD:
        raise RuntimeError("Set a strong GRAFANA_ADMIN_PASSWORD in .env before installation")
    return values


def wait_for(url: str, name: str, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    last_error = "not ready"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if 200 <= response.status < 400:
                    print(f"[OK] {name}: {url}")
                    return
                last_error = f"HTTP {response.status}"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            last_error = str(error)
        time.sleep(2)
    raise RuntimeError(f"{name} did not become ready: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-start", action="store_true", help="Generate and validate configuration only")
    args = parser.parse_args()
    try:
        env = ensure_env(PROJECT_ROOT / ".env")
        run([sys.executable, "scripts/generate_targets.py"])
        run(["docker", "compose", "config", "-q"])
        if args.no_start:
            print("[OK] Monitoring configuration is valid.")
            return 0
        run(["docker", "compose", "up", "-d"])
        prometheus_port = env.get("PROMETHEUS_PORT", "9090")
        grafana_port = env.get("GRAFANA_PORT", "3000")
        wait_for(f"http://127.0.0.1:{prometheus_port}/-/ready", "Prometheus")
        wait_for(f"http://127.0.0.1:{grafana_port}/api/health", "Grafana")
    except (OSError, RuntimeError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print("[OK] dARK monitoring is running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
