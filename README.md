# ScriptBox — Your AI Agent Is Just a Scripts Folder (In Progress)

**A composable, sandboxed, observable script runner for personal automation — controlled from Telegram, authored with Claude Code.**

Most people don't need an autonomous AI agent for their automation. They need a script that runs on a schedule, maybe makes an LLM call, and tells them the result. ScriptBox is that tool — but with real engineering under the hood: DAG-based composition, Docker sandboxing, per-run observability, persistent per-script state, and a shared personality layer for LLM calls. Drop a Python file in a folder, give it a cron schedule, and control it from your phone.

## Why not just use cron + a shell script?

Each piece is simple. The combination is not.

- **Scripts compose into pipelines.** Declare `depends_on` and ScriptBox resolves the DAG, passes upstream outputs into downstream `ctx.inputs`, and propagates failures as skips. You don't wire this by hand.
- **Every script runs in its own Docker container.** Memory limits, CPU caps, network restrictions, read-only root filesystem. Safe to run scripts you didn't write.
- **Every run is tracked.** Execution logs with duration, status, and error messages. LLM token/cost observability and drift detection are built into the schema.
- **Per-script persistent state.** `ctx.store` is an async key-value store backed by SQLite, namespaced per script. Scripts remember what they've already seen, sent, or processed — across runs, across restarts.
- **SOUL.md gives all LLM calls a shared voice.** One file in the project root becomes the system prompt prefix for every `ctx.llm.complete()` call. Your scripts don't just run — they speak with a consistent personality.
- **Telegram as the control plane.** Trigger scripts, check status, view logs, pause schedules, get failure alerts — from your phone.
- **Claude Code as the authoring interface.** No setup UI, no YAML config language, no drag-and-drop workflow builder. You write Python. Claude Code writes Python with you.

## Why not use an autonomous agent framework?

Autonomous agents — where the LLM decides what to do on every interaction — are powerful but expensive ($0.50-2.00+ per interaction) and non-deterministic. You can't audit what they'll do next because they don't know either.

ScriptBox is the opposite: **you write the logic, the LLM is a tool inside your script** for specific tasks (summarizing, parsing, deciding). Deterministic. Auditable. $0.001-0.01 per run.

The intelligence is in the authoring (one-time, with Claude Code), not in the execution (repeated, on cron).

## How It Works

A script is a Python file with a `META` dict and an `async def run(ctx)`:

```python
# scripts/hn_k8s_monitor.py

META = {
    "name": "HN Kubernetes Monitor",
    "schedule": "0 8 * * *",
}

async def run(ctx):
    resp = await ctx.http.get(
        "https://hn.algolia.com/api/v1/search?query=kubernetes&tags=story"
    )
    stories = resp.json()["hits"][:10]

    seen = await ctx.store.get("seen_ids", set())
    new_stories = [s for s in stories if s["objectID"] not in seen]
    if not new_stories:
        return

    summary = await ctx.llm.complete(
        prompt=f"Summarize these HN posts in 3-4 bullet points:\n{new_stories}",
    )

    await ctx.telegram.send(summary)
    await ctx.store.set("seen_ids", seen | {s["objectID"] for s in new_stories})
```

`ctx.store` persists across runs — the script only notifies you about posts it hasn't seen before. `ctx.llm` summarizes them. `ctx.telegram` sends the result to your phone. This runs every morning at 8am for effectively zero cost.

### Composition

Scripts form pipelines through `depends_on`. Upstream outputs flow into downstream `ctx.inputs`:

```python
# scripts/fetch_prices.py
META = {"name": "Fetch Prices", "schedule": "0 9 * * *"}

async def run(ctx):
    resp = await ctx.http.get("https://api.example.com/prices")
    return {"prices": resp.json()}

# scripts/analyze_prices.py
META = {"name": "Analyze Prices", "depends_on": ["fetch_prices"]}

async def run(ctx):
    prices = ctx.inputs["fetch_prices"]["prices"]
    # ... analysis logic ...
    return {"summary": summary}

# scripts/notify_prices.py
META = {"name": "Notify Prices", "depends_on": ["analyze_prices"]}

async def run(ctx):
    summary = ctx.inputs["analyze_prices"]["summary"]
    await ctx.telegram.send(summary)
```

ScriptBox resolves the DAG, runs them in order, and if `fetch_prices` fails, the downstream scripts are marked `skipped` — not crashed.

### Sandboxing

Any script can opt into Docker isolation:

```python
META = {
    "name": "Untrusted Analysis",
    "sandbox": {
        "memory": "512m",
        "cpu": 1.0,
        "timeout": 60,
        "network": "none",
    },
}
```

The script runs in a container with a read-only root filesystem, the specified resource limits, and its own mounted store directory. Network can be `bridge`, `none`, or `restricted`.

## SOUL.md — Shared Personality

A `SOUL.md` file in the project root is injected as a system prompt prefix into every `ctx.llm.complete()` call:

```markdown
# SOUL.md

You are a concise technical assistant. Summarize in bullet points.
Never use marketing language. Prefer specifics over generalities.
When uncertain, say so — don't hedge with weasel words.
```

Every script that calls `ctx.llm` inherits this voice. A price monitor and a news summarizer and a code reviewer all speak the same way — your way.

State gives scripts memory. Composition gives scripts coordination. SOUL.md gives scripts personality. The agent experience without the agent cost.

## Telegram Control Plane

ScriptBox runs a Telegram bot that serves as the primary interface:

| Command | Description |
|---|---|
| `/scripts` | List all loaded scripts with schedule and status |
| `/run <script_id>` | Trigger a script immediately |
| `/logs <script_id> [n]` | Show the last *n* runs (default 5) |
| `/status` | System overview — script count, last run, Docker availability |
| `/stats [script_id]` | Execution stats — total runs, success rate, avg duration |
| `/graph [script_id]` | Show the dependency graph |
| `/pause <script_id>` | Pause a script's cron schedule |
| `/resume <script_id>` | Resume a paused script |
| `/help` | List all commands |

The bot also sends proactive notifications: failure alerts when a cron-triggered script errors, and an optional daily digest summarizing all runs from the past 24 hours.

Authentication is per-chat-ID — only configured chat IDs can issue commands.

## Getting Started

### Requirements

- Python 3.10+
- Docker (optional, only for sandboxed execution)

### Install

```bash
git clone https://github.com/ssrajadh/scriptbox.git
cd scriptbox
pip install -e ".[dev]"
```

### Setup

```bash
python -m scriptbox setup
```

This walks you through getting a Telegram bot token from @BotFather and your chat ID from @userinfobot. It validates both, sends a test message, and writes your `.env` file.

### Write a script

```bash
mkdir -p scripts
```

```python
# scripts/hello.py
META = {
    "name": "Hello World",
    "description": "Sends a greeting to Telegram",
}

async def run(ctx):
    await ctx.telegram.send("Hello from ScriptBox!")
    return {"status": "sent"}
```

Or copy one of the examples:

```bash
cp scripts/examples/hello_telegram.py scripts/
```

### Start

```bash
python -m scriptbox start
```

This starts the Telegram bot and the cron scheduler. Open Telegram, send `/scripts` to see your loaded scripts, then `/run hello` to trigger one.

### Trigger from CLI

```bash
python -m scriptbox trigger hello
```

## Script Contract

Every script in `scripts/` must have:

- A `META` dict with at least a `"name"` string
- An `async def run(ctx)` accepting one positional argument

### META fields

```python
META = {
    "name": "Human-readable name",                     # required
    "schedule": "*/5 * * * *",                          # optional — 5-field cron
    "depends_on": ["other_script_stem"],                # optional — list of script IDs
    "outputs": ["key1"],                                # optional — documents return keys
    "description": "What it does",                      # optional
    "sandbox": {"memory": "512m", "timeout": 60},       # optional — Docker isolation
}
```

### ctx reference

| Attribute | Type | Description |
|---|---|---|
| `ctx.script_id` | `str` | The script's filename stem |
| `ctx.store` | `ScriptStore` | Async key-value store (SQLite), namespaced per script |
| `ctx.inputs` | `dict` | Outputs from upstream dependencies |
| `ctx.http` | `httpx.AsyncClient` | HTTP client |
| `ctx.llm` | `LLMClient` | LLM client (system prompt from SOUL.md) |
| `ctx.telegram` | `TelegramClient` | Send messages, ask yes/no questions |
| `ctx.secrets` | `dict[str, str]` | Non-`SCRIPTBOX_` keys from `.env` |

### Return value

`run(ctx)` can return a dict. The keys become available to downstream scripts via `ctx.inputs["script_id"]`. Return `None` or nothing if the script has no outputs.

## CLI

```bash
python -m scriptbox setup              # Interactive Telegram setup
python -m scriptbox start              # Start bot + scheduler
python -m scriptbox trigger <id>       # Run a script from the command line
python -m scriptbox build-image        # Build the sandbox Docker image
python -m scriptbox check              # Show Docker and image status
python -m scriptbox cleanup            # Remove containers, image, orphaned stores
```

## Architecture

```
loader -> dag -> executor -> runner
                    |
    store, context, observability, sandbox
```

| Module | Role |
|---|---|
| `loader.py` | Discovers and validates scripts from a directory |
| `dag.py` | Dependency graph, topological sort, subgraph extraction |
| `executor.py` | Runs scripts in order, wires inputs/outputs, handles timeouts and failure propagation |
| `runner.py` | Ties loader, DAG, executor, APScheduler, and observability together |
| `context.py` | `ScriptContext` passed to every `run(ctx)` call |
| `store.py` | Per-script async SQLite key-value store |
| `observability.py` | Execution logging and stats queries (SQLite) |
| `sandbox/` | Docker-based sandboxed execution with resource limits |
| `telegram/` | Telegram bot, auth, notification, and formatting |
| `config.py` | Centralized `.env` + environment variable configuration |
| `main.py` | Starts the bot and scheduler concurrently |

## Testing

```bash
# Run all tests (excludes Docker tests)
python -m pytest tests/ -m "not docker"

# Run Docker tests (requires running Docker daemon)
python -m pytest tests/ -m docker

# Run all tests
python -m pytest tests/

# Specific module or keyword
python -m pytest tests/test_loader.py -v
python -m pytest tests/ -k "test_cycle"
```

---

MIT License
