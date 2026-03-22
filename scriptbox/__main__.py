"""CLI entry point: python -m scriptbox <command>."""
from __future__ import annotations

import argparse
import asyncio
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scriptbox", description="ScriptBox CLI")

    # Common options shared by subcommands that need scripts/db.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--scripts-dir", default="./scripts", help="Path to scripts directory"
    )
    common.add_argument("--db", default="./scriptbox.db", help="Path to SQLite database")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("build-image", help="Build the sandbox Docker image")
    sub.add_parser("cleanup", parents=[common], help="Remove sandbox containers, image, and orphaned stores")
    sub.add_parser("check", help="Check if Docker is available and show image info")
    sub.add_parser("setup", help="Interactive setup for Telegram bot configuration")

    trigger_p = sub.add_parser("trigger", parents=[common], help="Manually trigger a script by ID")
    trigger_p.add_argument("script_id", help="Script ID to trigger")

    return parser


def _cmd_build_image() -> None:
    from scriptbox.sandbox.image_builder import ImageBuilder

    tag = ImageBuilder().build(force=True)
    print(f"Image built: {tag}")


def _cmd_check() -> None:
    from scriptbox.sandbox.image_builder import ImageBuilder

    builder = ImageBuilder()
    available = builder.image_exists()
    info = builder.get_image_info()
    if info:
        print(f"Image:   {info['tag']}")
        print(f"Size:    {info['size_mb']} MB")
        print(f"Created: {info['created']}")
        print(f"Hash:    {info['source_hash'][:12]}")
    else:
        print("Image not found.")

    try:
        import docker

        docker.from_env().ping()
        print("Docker:  available")
    except Exception:
        print("Docker:  not available")


async def _cmd_cleanup(args: argparse.Namespace) -> None:
    from scriptbox.runner import Runner

    runner = Runner(
        args.scripts_dir,
        args.db,
        use_sandbox=True,
    )
    await runner.setup()
    info = await runner.cleanup()
    print(f"Containers removed: {info['containers_removed']}")
    print(f"Image removed:      {info['image_removed']}")
    print(f"Orphaned stores:    {info['orphaned_stores_removed']}")
    runner.scheduler.shutdown(wait=False)


async def _cmd_trigger(args: argparse.Namespace) -> None:
    from scriptbox.runner import Runner

    runner = Runner(
        args.scripts_dir,
        args.db,
        use_sandbox=True,
    )
    await runner.setup()

    script_ids = {s.id for s in runner.get_scripts()}
    if args.script_id not in script_ids:
        print(f"Error: script '{args.script_id}' not found", file=sys.stderr)
        runner.scheduler.shutdown(wait=False)
        sys.exit(1)

    results = await runner.trigger(args.script_id)
    for r in results:
        status_icon = {"success": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(
            r.status, r.status
        )
        sandbox_tag = " [sandboxed]" if r.sandboxed else ""
        print(f"  {status_icon}  {r.script_id} ({r.duration_ms}ms){sandbox_tag}")
        if r.error:
            print(f"       error: {r.error}")
    runner.scheduler.shutdown(wait=False)

    if any(r.status == "failed" for r in results):
        sys.exit(1)


def _cmd_setup() -> None:
    from scriptbox.setup import run_setup

    run_setup()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "build-image":
        _cmd_build_image()
    elif args.command == "check":
        _cmd_check()
    elif args.command == "cleanup":
        asyncio.run(_cmd_cleanup(args))
    elif args.command == "trigger":
        asyncio.run(_cmd_trigger(args))
    elif args.command == "setup":
        _cmd_setup()


if __name__ == "__main__":
    main()
