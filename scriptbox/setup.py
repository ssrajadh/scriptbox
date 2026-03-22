"""Interactive setup for ScriptBox Telegram integration.

Run via ``python -m scriptbox setup``.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx

_CONFIG_PATH = Path("config.json")
_ENV_PATH = Path(".env")
_ENV_EXAMPLE_PATH = Path(".env.example")
_API_BASE = "https://api.telegram.org/bot{token}"


def run_setup() -> None:
    """Interactive setup wizard — prompts for bot token and chat ID."""
    print("=" * 50)
    print("  ScriptBox Setup")
    print("=" * 50)
    print()

    # Check for existing config.
    if _CONFIG_PATH.exists():
        answer = input("config.json already exists. Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Setup cancelled.")
            return

    # ---- Bot token ---------------------------------------------------
    print("Step 1: Telegram Bot Token")
    print("  Get one from @BotFather on Telegram")
    print()

    while True:
        token = input("Bot token: ").strip()
        if not token:
            print("  Token cannot be empty.")
            continue

        print("  Validating token...", end=" ", flush=True)
        bot_name = _validate_token(token)
        if bot_name is not None:
            print(f"OK (@{bot_name})")
            break

        retry = input("  Try again? [Y/n] ").strip().lower()
        if retry == "n":
            print("Setup cancelled.")
            return

    print()

    # ---- Chat ID -----------------------------------------------------
    print("Step 2: Telegram Chat ID")
    print("  Get yours from @userinfobot on Telegram")
    print()

    while True:
        raw_id = input("Chat ID: ").strip()
        try:
            chat_id = int(raw_id)
        except ValueError:
            print("  Chat ID must be a number.")
            continue

        print("  Sending test message...", end=" ", flush=True)
        if _send_test_message(token, chat_id):
            print("OK")
            break

        retry = input("  Try again? [Y/n] ").strip().lower()
        if retry == "n":
            print("Setup cancelled.")
            return

    print()

    # ---- Write config ------------------------------------------------
    config = {
        "bot_token": token,
        "chat_ids": [chat_id],
        "scripts_dir": "./scripts",
        "db_path": "./scriptbox.db",
        "use_sandbox": True,
    }
    _CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Wrote {_CONFIG_PATH}")

    # ---- Create .env from .env.example if missing --------------------
    if not _ENV_PATH.exists():
        if _ENV_EXAMPLE_PATH.exists():
            shutil.copy2(_ENV_EXAMPLE_PATH, _ENV_PATH)
            print(f"Created {_ENV_PATH} from {_ENV_EXAMPLE_PATH}")
        else:
            _ENV_PATH.write_text("# Add your API keys here\n")
            print(f"Created empty {_ENV_PATH}")
    else:
        print(f"{_ENV_PATH} already exists, skipping.")

    print()
    print("Setup complete! You can now run scripts with:")
    print("  python -m scriptbox trigger <script_id>")


def _validate_token(token: str) -> str | None:
    """Call ``getMe`` and return the bot username, or None on failure."""
    url = f"{_API_BASE.format(token=token)}/getMe"
    try:
        resp = httpx.get(url, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["username"]
        print(f"FAILED ({data.get('description', 'unknown error')})")
    except httpx.HTTPError as exc:
        print(f"FAILED (network error: {exc})")
    except Exception as exc:
        print(f"FAILED ({exc})")
    return None


def _send_test_message(token: str, chat_id: int) -> bool:
    """Send a test message and return True on success."""
    url = f"{_API_BASE.format(token=token)}/sendMessage"
    try:
        resp = httpx.post(
            url,
            data={
                "chat_id": chat_id,
                "text": "\u2705 ScriptBox connected successfully!",
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            return True
        print(f"FAILED ({data.get('description', 'unknown error')})")
    except httpx.HTTPError as exc:
        print(f"FAILED (network error: {exc})")
    except Exception as exc:
        print(f"FAILED ({exc})")
    return False
