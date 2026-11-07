"""Loopback-only ASGI server entry point for Yuki Desktop and local clients."""
from __future__ import annotations

import argparse
import ipaddress
import os

from api.bootstrap import build_yuki_runtime


def _loopback_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Yuki local server host must be an IP loopback address.") from exc
    if not address.is_loopback:
        raise argparse.ArgumentTypeError("Yuki local server refuses non-loopback bind addresses.")
    return str(address)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Yuki's authenticated local API.")
    parser.add_argument("--host", type=_loopback_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("YUKI_PORT", "8765")))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")

    try:
        import uvicorn
    except ImportError:
        parser.error("Install the declared runtime dependencies with: pip install -r requirements.txt")

    runtime = build_yuki_runtime()
    try:
        uvicorn.run(runtime, host=args.host, port=args.port, access_log=False)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
