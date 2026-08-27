#!/usr/bin/env python3
"""Stop every dARK monitoring service while preserving its runtime data."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> int:
    command = ["docker", "compose", "stop"]
    print(f"[RUN] {' '.join(command)}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode:
        print("[ERROR] Monitoring stack could not be stopped.", file=sys.stderr)
        return result.returncode
    print(
        "[OK] All dark-monitoring services were stopped "
        "(blackbox-exporter, Prometheus and Grafana)."
    )
    print("[OK] Containers and data volumes were preserved for the next install.py run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
