"""``python -m futures_bot.api`` -- runs the research API with uvicorn.

A thin wrapper rather than telling users to invoke uvicorn directly, so the
one obvious way to start this matches how every other entry point in this
project is invoked (``python -m futures_bot.cli ...``) -- and so the
network-exposure guard below is the one obvious way to hit, too. Nothing
stops someone from running ``uvicorn futures_bot.api.app:app --host
0.0.0.0`` directly instead, which bypasses this guard entirely; there is no
way to prevent that from inside the ASGI app itself (by the time a request
arrives to check anything, the process is already listening on the network).
This wrapper is the recommended entry point specifically because it's the
one place this check can actually run before the socket opens.
"""

from __future__ import annotations

import argparse
import sys

#: Hostnames/addresses this process will bind to without requiring explicit
#: opt-in. Anything else is a network-facing bind.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


def main() -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m futures_bot.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on source changes (development).")
    parser.add_argument(
        "--allow-network-exposure", action="store_true",
        help=(
            "Required alongside any --host other than 127.0.0.1/::1/localhost. There is no "
            "authentication in front of this API (see api/app.py's module docstring) -- "
            "/api/live/*, /api/research-server/*, and every backtest/optimizer job endpoint are "
            "reachable by anyone who can reach the port. Only pass this once something in front "
            "of it (a reverse proxy with auth, a VPN/firewall boundary) actually restricts access."
        ),
    )
    args = parser.parse_args()

    if not _is_loopback(args.host) and not args.allow_network_exposure:
        print(
            f"Refusing to bind {args.host!r}: this API has no authentication in front of it, and "
            f"that host is not loopback-only ({', '.join(sorted(_LOOPBACK_HOSTS))}). Pass "
            f"--allow-network-exposure once you've put something in front of it that restricts "
            f"access (reverse proxy with auth, VPN/firewall boundary) -- see api/app.py's module "
            f"docstring.",
            file=sys.stderr,
        )
        return 2

    uvicorn.run("futures_bot.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
