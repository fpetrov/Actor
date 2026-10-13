"""Official login/status flow; ACTOR does not read or store auth credentials."""
from __future__ import annotations

import asyncio
import json
import tempfile
from .adapter import sdk_module, configuration, SDK_VERSION


async def command(action: str) -> int:
    sdk = sdk_module()
    with tempfile.TemporaryDirectory(prefix="actor-codex-auth-") as cwd:
        async with sdk.AsyncCodex(configuration(sdk, cwd)) as client:
            account = await client.account()
            if action == "login" and account.account is None:
                login = await client.login_chatgpt()
                print("Open this official ChatGPT sign-in URL:\n" + login.auth_url, flush=True)
                try:
                    completed = await login.wait()
                    if not completed.success:
                        print("ChatGPT sign-in did not complete.")
                        return 1
                except asyncio.CancelledError:
                    await login.cancel()
                    raise
                account = await client.account()
            authenticated = account.account is not None
            print(json.dumps({"sdk_version": SDK_VERSION, "authenticated": authenticated,
                "authentication_mode": account.account.root.type if authenticated else None,
                "requires_openai_auth": account.requires_openai_auth,
                "next_step": None if authenticated else "actor-runtime codex login"}, indent=2))
            return 0 if authenticated or not account.requires_openai_auth else 1
