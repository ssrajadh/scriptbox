"""Main entrypoint — starts the Telegram bot and the scheduler concurrently.

Usage::

    python -m scriptbox start
    python -m scriptbox start --env /path/to/.env
    python -m scriptbox.main --env .env
"""
from __future__ import annotations

import asyncio
import logging
import signal

from scriptbox.config import load_config
from scriptbox.runner import Runner
from scriptbox.telegram.auth import TelegramConfig
from scriptbox.telegram.bot import ScriptBoxBot
from scriptbox.telegram.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


async def run(
    env_path: str = ".env",
    *,
    _stop_event: asyncio.Event | None = None,
) -> None:
    """Start the bot and scheduler, then block until interrupted.

    Parameters
    ----------
    env_path:
        Path to the ``.env`` configuration file.
    _stop_event:
        **Testing only** — pass an :class:`asyncio.Event` to trigger
        shutdown programmatically instead of via OS signals.
    """
    cfg = load_config(env_path)
    tg_config = TelegramConfig(bot_token=cfg.bot_token, chat_ids=[cfg.chat_id])

    runner = Runner(
        cfg.scripts_dir,
        cfg.db_path,
        cfg.use_sandbox,
        daily_digest=cfg.daily_digest,
        daily_digest_time=cfg.daily_digest_time,
    )
    notifier = TelegramNotifier(cfg.bot_token, cfg.chat_id)
    runner.set_notifier(notifier)

    bot = ScriptBoxBot(runner, tg_config)

    await runner.setup()
    await bot.start()

    # --- Wait for shutdown signal ----------------------------------------
    stop_event = _stop_event or asyncio.Event()
    if _stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

    logger.info("ScriptBox is running. Press Ctrl+C to stop.")
    await stop_event.wait()

    # --- Graceful teardown -----------------------------------------------
    logger.info("Shutting down…")
    await bot.stop()
    if runner.scheduler.running:
        runner.scheduler.shutdown(wait=False)


def main(env_path: str = ".env") -> None:
    """Blocking entry point suitable for CLI use."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        asyncio.run(run(env_path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Start ScriptBox")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    args = parser.parse_args()
    main(args.env)
