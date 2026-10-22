"""Private controller bootstrap, launched only by the installed guardian CLI."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path


async def controller(args):
    # Consume the inherited descriptor before importing Native/provider code.
    # No environment-only option enables group inheritance in other commands.
    value = os.environ.pop("ACTOR_BROADCAST_GUARDIAN_FD", "")
    if not value.isascii() or not value.isdecimal(): raise RuntimeError("guardian_private_socket_required")
    from .guardian_client import GuardianClient
    client = await GuardianClient.from_inherited_fd(int(value))
    try:
        from .installation import BroadcastInstallation
        from .cli import _serve
        config = BroadcastInstallation.load(args.config)
        return await _serve(args, config, guardian=client)
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--action", dest="broadcast_action", choices=("ui", "preview"), required=True)
    parser.add_argument("--room")
    args = parser.parse_args()
    if args.broadcast_action == "preview" and not args.room: return 1
    try: return asyncio.run(controller(args))
    except BaseException: return 1  # Never print descriptors, URLs, prompts or secrets.


if __name__ == "__main__": raise SystemExit(main())
