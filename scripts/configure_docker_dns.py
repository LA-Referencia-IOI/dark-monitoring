#!/usr/bin/env python3
"""Remove a static Docker DNS override and restore host-managed DNS.

Docker daemon DNS belongs to the host, not to a Compose project. Hard-coding a
router or public resolver makes deployments fail when the host changes network
or uses split DNS (for example Tailscale). This helper only removes the `dns`
key; all unrelated daemon settings are retained and a backup is made first.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def remove_static_dns(path: Path, apply: bool) -> bool:
    config = load_config(path)
    if "dns" not in config:
        print(f"No static Docker DNS override in {path}; no change needed.")
        return False
    print(f"Static Docker DNS currently configured: {config['dns']}")
    if not apply:
        print("Dry run only. Run again with --apply to remove this setting.")
        return False

    if os.geteuid() != 0:
        raise PermissionError("--apply must be run as root (for example with sudo).")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    shutil.copy2(path, backup)
    del config["dns"]
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)
    print(f"Removed static DNS override. Backup: {backup}")
    print("Restart Docker to apply: sudo systemctl restart docker")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/docker/daemon.json"))
    parser.add_argument("--apply", action="store_true", help="write the change (root required)")
    args = parser.parse_args()
    try:
        remove_static_dns(args.config, args.apply)
    except (OSError, ValueError, PermissionError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
