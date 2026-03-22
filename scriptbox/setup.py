"""Interactive setup for ScriptBox Telegram integration.

Run via ``python -m scriptbox setup``.
"""
from __future__ import annotations

from pathlib import Path

import httpx

_ENV_PATH = Path(".env")
_ENV_EXAMPLE_PATH = Path(".env.example")
_API_BASE = "https://api.telegram.org/bot{token}"


def run_setup() -> None:
    """Interactive setup wizard — prompts for bot token and chat ID."""
    print("=" * 50)
    print("  ScriptBox Setup")
    print("=" * 50)
    print()

    # Check for existing .env with credentials.
    if _ENV_PATH.exists():
        existing = _ENV_PATH.read_text()
        has_token = any(
            line.strip().startswith("SCRIPTBOX_BOT_TOKEN=") and
            line.strip() != "SCRIPTBOX_BOT_TOKEN="
            for line in existing.splitlines()
        )
        if has_token:
            answer = input(".env already has a bot token. Overwrite? [y/N] ").strip().lower()
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

    # ---- Write .env --------------------------------------------------
    _write_env(token, chat_id)
    print(f"Wrote {_ENV_PATH}")

    print()
    print("Setup complete! You can now run scripts with:")
    print("  python -m scriptbox trigger <script_id>")


def _write_env(token: str, chat_id: int) -> None:
    """Write or update the ``.env`` file with Telegram credentials.

    Preserves existing non-SCRIPTBOX_ keys (API keys, etc.).
    """
    existing: dict[str, str] = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            existing[key.strip()] = value.strip()

    # Set SCRIPTBOX_ values.
    existing["SCRIPTBOX_BOT_TOKEN"] = token
    existing["SCRIPTBOX_CHAT_ID"] = str(chat_id)
    existing.setdefault("SCRIPTBOX_SCRIPTS_DIR", "./scripts")
    existing.setdefault("SCRIPTBOX_DB_PATH", "./scriptbox.db")
    existing.setdefault("SCRIPTBOX_USE_SANDBOX", "true")

    # Ensure API key placeholders exist.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        existing.setdefault(key, "")

    # Write in a stable order: SCRIPTBOX_ first, then the rest.
    lines: list[str] = []
    sb_keys = sorted(k for k in existing if k.startswith("SCRIPTBOX_"))
    other_keys = sorted(k for k in existing if not k.startswith("SCRIPTBOX_"))
    for key in sb_keys:
        lines.append(f"{key}={existing[key]}")
    if sb_keys and other_keys:
        lines.append("")
    for key in other_keys:
        lines.append(f"{key}={existing[key]}")

    _ENV_PATH.write_text("\n".join(lines) + "\n")


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
