#!/usr/bin/env python3
"""Remove all dARK monitoring runtime resources and generated target files."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
GENERATED = PROJECT_ROOT / "generated"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Skip the destructive confirmation prompt")
    parser.add_argument(
        "--keep-images",
        action="store_true",
        help="Keep downloaded Prometheus, Grafana and Blackbox images",
    )
    args = parser.parse_args()
    if not args.yes:
        try:
            answer = input(
                "This removes dark-monitoring containers, network, volumes, images, "
                "monitoring history and Grafana settings. Continue? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Cleanup cancelled.")
            return 0
        if answer not in {"y", "yes"}:
            print("[INFO] Cleanup cancelled.")
            return 0
    command = ["docker", "compose", "down", "--volumes", "--remove-orphans"]
    if not args.keep_images:
        command.extend(["--rmi", "all"])
    print(f"[RUN] {' '.join(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode:
        print("[ERROR] Monitoring stack could not be cleaned.", file=sys.stderr)
        return result.returncode
    for target in GENERATED.glob("*.json"):
        target.unlink()
        print(f"[OK] Removed {target.name}")
    removed = "containers, network, volumes and generated targets"
    if not args.keep_images:
        removed += ", including downloaded Compose images"
    print(f"[OK] Removed dark-monitoring {removed}.")
    print("[OK] Source files and .env were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
