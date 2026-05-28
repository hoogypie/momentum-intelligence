#!/usr/bin/env python3
"""
scripts/run_backend.py
Development runner — v2.3

Start de Momentum Intelligence backend lokaal.
Werkt op Windows, Mac en Linux zonder make.

Gebruik:
    python3 scripts/run_backend.py              # standaard (reload aan)
    python3 scripts/run_backend.py --port 8080
    python3 scripts/run_backend.py --no-reload  # productie-stijl
    python3 scripts/run_backend.py --log-level debug
"""

import sys
import argparse
import subprocess
import os

# Zorg dat de project root in het Python path zit
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start Momentum Intelligence backend"
    )
    parser.add_argument("--host",      default="127.0.0.1")
    parser.add_argument("--port",      default=8000, type=int)
    parser.add_argument("--no-reload", action="store_true")
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    cmd = [
        sys.executable, "-m", "uvicorn",
        "backend.app:app",
        "--host",      args.host,
        "--port",      str(args.port),
        "--log-level", args.log_level,
    ]
    if not args.no_reload:
        cmd.append("--reload")

    print(f"Starting Momentum Intelligence API v2.3")
    print(f"  URL:    http://{args.host}:{args.port}")
    print(f"  Docs:   http://{args.host}:{args.port}/docs")
    print(f"  Reload: {'disabled' if args.no_reload else 'enabled'}")
    print()

    try:
        result = subprocess.run(cmd, cwd=_ROOT)
        return result.returncode
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except FileNotFoundError:
        print("ERROR: uvicorn niet gevonden. Voer uit: pip install uvicorn")
        return 1


if __name__ == "__main__":
    sys.exit(main())
