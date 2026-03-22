# Scriptbox

A dynamic script loader, DAG-based executor, and scheduler. Scripts are plain Python files with a `META` dict and an `async def run(ctx)` entry point.

## Commands

```bash
# Run all tests (excludes docker tests by default)
python -m pytest tests/ -m "not docker"

# Run docker tests (requires running Docker daemon)
python -m pytest tests/ -m docker

# Run all tests including docker
python -m pytest tests/

# Run a specific test module
python -m pytest tests/test_loader.py -v

# Run tests matching a keyword
python -m pytest tests/ -k "test_cycle"
```

There is no build step. The project uses `pyproject.toml` with setuptools.

## Architecture

The pipeline flows: **loader -> dag -> executor -> runner**, with **store**, **context**, **observability**, and **sandbox** as supporting modules.

### Core modules (scriptbox/)

- **loader.py** — Discovers `.py` files in a scripts directory, dynamically imports each, validates `META` (dict with `"name"` str) and `run` (async, 1+ positional arg). Returns `ScriptInfo` frozen dataclasses. Invalid scripts are logged and skipped.
- **dag.py** — Builds dependency graph from `META["depends_on"]`. Provides `topo_sort()` for full ordering and `subgraph(scripts, target)` for minimal execution set. Raises `CycleError` or `MissingDependencyError`.
- **executor.py** — Runs scripts in topological order. Wires upstream outputs into downstream `ctx.inputs`. Handles timeouts and failure propagation (downstream scripts marked `"skipped"`). `use_sandbox=True` delegates to DockerExecutor; `use_sandbox=False` (default) runs in-process. Returns `ExecutionResult` dataclasses.
- **runner.py** — Main entry point. `Runner` ties loader, DAG, executor, APScheduler, and observability together. `setup()` loads scripts and registers cron jobs. `trigger(script_id)` runs with DAG resolution and logs results. `reload()` picks up new/removed scripts.
- **context.py** — `ScriptContext` passed to `run(ctx)`. Provides `ctx.script_id`, `ctx.store`, `ctx.inputs`, `ctx.http`, `ctx.llm`, `ctx.telegram`, `ctx.secrets`.
- **store.py** — `ScriptStore`: per-script key-value store backed by SQLite (aiosqlite). Namespaced by script_id. JSON-serialized values. Table auto-created on first use.
- **observability.py** — `RunLogger`: logs every execution to SQLite `runs` table. `get_runs()`, `get_stats()`, `get_all_script_stats()` for querying. `llm_calls` table exists for future LLM cost tracking.
- **sandbox/config.py** — `SandboxConfig` dataclass parsed from `META["sandbox"]`. Validates memory format, cpu > 0, timeout > 0, network in {bridge, none, restricted}. `SandboxConfig.disabled()` for local execution.
- **sandbox/image_builder.py** — `ImageBuilder` builds and caches a `scriptbox-runner:latest` Docker image. Assembles build context in a temp dir (Dockerfile, requirements.txt, scriptbox package, harness.py). Stores a source-file hash as an image label to skip rebuilds when nothing changed. Uses `docker` Python SDK.
- **sandbox/harness.py** — Container entry-point. Reads `/run/{config,inputs,secrets}.json`, imports `/run/script.py`, builds `ScriptContext`, calls `run(ctx)`, writes `/run/{result,outputs}.json`. Core logic in `run_harness(base_path)` for testability.
- **sandbox/docker_executor.py** — `DockerExecutor` runs scripts in Docker containers. Prepares a temp run dir, mounts it + a per-script store dir, applies resource limits (memory, cpu, network, read-only root) from `SandboxConfig`, enforces timeout by killing the container.

### Script contract

Every script in `scripts/` must have:

```python
META = {
    "name": "Human-readable name",          # required
    "schedule": "*/5 * * * *",              # optional, 5-field cron
    "depends_on": ["other_script_stem"],    # optional
    "outputs": ["key1"],                    # optional, documentation
    "description": "What it does",          # optional
    "sandbox": {"memory": "512m", "timeout": 60},  # optional
}

async def run(ctx):
    # ctx.store, ctx.inputs, ctx.http, ctx.llm, ctx.telegram, ctx.secrets
    return {"key1": "value"}  # optional, passed to downstream via ctx.inputs
```

### Tests

- Tests live in `tests/`. Fixture scripts live in `tests/script_fixtures/`.
- Tests use `tmp_path` for database files and temp script directories — no cleanup needed.
- `test_integration.py` validates the full end-to-end pipeline.
- pytest-asyncio is configured with `asyncio_mode = "auto"` in pyproject.toml.
- Docker tests are marked with `@pytest.mark.docker` and auto-skip if Docker is unavailable.

### Key conventions

- All database access is async via aiosqlite. Each method opens its own connection.
- `ScriptInfo` is a frozen dataclass — immutable after creation.
- The loader skips invalid scripts with warnings rather than crashing.
- The executor never crashes on script failure — it captures errors and propagates skips.
- `StubLLMClient` and `StubTelegramClient` in context.py are placeholders for future phases.
- APScheduler 3.x (`AsyncIOScheduler`, `CronTrigger`) — not v4.
- `sandbox/__init__.py` lazy-imports `ImageBuilder` and `DockerExecutor` to avoid importing the `docker` SDK inside runner containers (where it's not installed).
