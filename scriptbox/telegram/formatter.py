"""Format data into Telegram-friendly HTML messages.

All public functions return strings.  Telegram messages have a 4096-character
limit; any output exceeding that is truncated with a note.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from scriptbox.executor import ExecutionResult
from scriptbox.loader import ScriptInfo

MAX_MESSAGE_LEN = 4096
_TRUNCATION_NOTE = "\n\n… <i>truncated — message too long</i>"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate(text: str) -> str:
    if len(text) <= MAX_MESSAGE_LEN:
        return text
    cut = MAX_MESSAGE_LEN - len(_TRUNCATION_NOTE)
    return text[:cut] + _TRUNCATION_NOTE


def _cron_to_text(expr: str) -> str:
    """Best-effort conversion of a 5-field cron expression to readable text."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts

    # every N minutes
    if minute.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
        n = minute[2:]
        return f"every {n} min"

    # every minute
    if minute == "*" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "every minute"

    # every hour at :MM
    if minute.isdigit() and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return f"every hour at :{minute.zfill(2)}"

    # specific hour
    if minute.isdigit() and hour.isdigit():
        time_str = f"{int(hour)}:{minute.zfill(2)}"

        dow_map = {
            "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
            "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun",
        }

        if dom == "*" and month == "*":
            if dow == "*":
                return f"every day {time_str}"
            if dow == "1-5":
                return f"weekdays {time_str}"
            if dow == "0,6":
                return f"weekends {time_str}"
            if dow in dow_map:
                return f"every {dow_map[dow]} {time_str}"
            return f"cron dow={dow} {time_str}"

        if month == "*" and dow == "*" and dom.isdigit():
            return f"monthly day {dom} {time_str}"

    return expr


def _fmt_duration(ms: int) -> str:
    """Format milliseconds as a comma-separated string."""
    return f"{ms:,}ms"


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------


def format_script_list(
    scripts: list[ScriptInfo],
    paused: set[str] | None = None,
) -> str:
    """Numbered list of scripts with status indicators."""
    if not scripts:
        return "No scripts loaded."

    paused = paused or set()
    lines: list[str] = []
    for i, s in enumerate(scripts, 1):
        schedule = s.meta.get("schedule")
        if s.id in paused:
            info = "paused"
            icon = "⏸"
        elif schedule:
            info = _cron_to_text(schedule)
            icon = "✅"
        else:
            info = "manual only"
            icon = "✅"
        lines.append(f"{i}. {icon} {s.name} ({info}) <code>[{s.id}]</code>")
    return _truncate("\n".join(lines))


def format_execution_results(results: list[ExecutionResult]) -> str:
    """One line per executed script showing status and timing."""
    if not results:
        return "No results."

    lines: list[str] = []
    for r in results:
        if r.status == "success":
            lines.append(f"✅ {r.script_id} — {_fmt_duration(r.duration_ms)}")
        elif r.status == "skipped":
            reason = r.error or "upstream failed"
            lines.append(f"⏭ {r.script_id} — skipped ({reason})")
        else:
            err = r.error or "unknown error"
            lines.append(f"❌ {r.script_id} — {err}")
    return _truncate("\n".join(lines))


def format_logs(runs: list[dict[str, Any]], script_id: str) -> str:
    """Recent runs for a script."""
    if not runs:
        return f"📋 No logs for <code>{script_id}</code>."

    header = f"📋 Logs for <code>{script_id}</code> (last {len(runs)}):"
    lines: list[str] = [header]
    for run in runs:
        started = run.get("started_at", "")
        try:
            dt = datetime.fromisoformat(started)
            date_str = dt.strftime("%b %d %H:%M")
        except (ValueError, TypeError):
            date_str = started or "?"

        status = run.get("status", "?")
        if status == "success":
            detail = f"✅ {_fmt_duration(run.get('duration_ms', 0))}"
        elif status == "failed":
            err = run.get("error") or "error"
            detail = f"❌ {err}"
        else:
            detail = f"⏭ {status}"

        lines.append(f"  {date_str} — {detail}")
    return _truncate("\n".join(lines))


def format_status(
    scripts: list[ScriptInfo],
    runner_info: dict[str, Any],
) -> str:
    """System status overview."""
    total = len(scripts)
    scheduled = sum(1 for s in scripts if s.meta.get("schedule"))
    manual = total - scheduled

    lines = [
        "📊 <b>ScriptBox Status</b>",
        f"Scripts: {total} loaded, {scheduled} scheduled, {manual} manual-only",
    ]

    last_run = runner_info.get("last_run")
    if last_run:
        lines.append(f"Last run: {last_run}")

    uptime = runner_info.get("uptime")
    if uptime:
        lines.append(f"Uptime: {uptime}")

    docker = runner_info.get("docker")
    if docker is not None:
        docker_str = "available (sandbox enabled)" if docker else "not available"
        lines.append(f"Docker: {docker_str}")

    return _truncate("\n".join(lines))


def format_stats(
    stats: dict[str, Any],
    script_id: str | None = None,
) -> str:
    """Per-script or aggregate statistics."""
    total = stats.get("total_runs", 0)
    success = stats.get("success_count", 0)
    failure = stats.get("failure_count", 0)
    rate = stats.get("success_rate", 0.0) * 100
    avg_ms = stats.get("avg_duration_ms", 0)
    cost = stats.get("total_cost_usd", 0.0)

    if script_id:
        lines = [f"📈 <b>Stats for {script_id}</b>"]
        lines.append(f"Runs: {total} total ({success} ✅, {failure} ❌)")
        lines.append(f"Success rate: {rate:.1f}%")
        lines.append(f"Avg duration: {_fmt_duration(round(avg_ms))}")
        if total > 0 and cost > 0:
            per_run = cost / total
            lines.append(f"LLM cost: ${cost:.2f} total (${per_run:.3f}/run)")
        elif cost > 0:
            lines.append(f"LLM cost: ${cost:.2f} total")
    else:
        lines = ["📈 <b>ScriptBox Stats</b>"]
        lines.append(f"Total runs: {total}")
        lines.append(f"Success rate: {rate:.1f}%")
        if cost > 0:
            lines.append(f"Total LLM cost: ${cost:.2f}")

    return _truncate("\n".join(lines))


def format_dag(
    dag: dict[str, list[str]],
    target: str | None = None,
) -> str:
    """ASCII visualization of the dependency graph."""
    if not dag:
        return "No scripts in graph."

    if target is not None:
        return _format_dag_for_target(dag, target)

    # Full graph: show all chains
    lines = ["🔗 <b>Dependency Graph</b>"]
    for chain in _linearize(dag):
        lines.append("  " + " → ".join(chain))
    return _truncate("\n".join(lines))


def _format_dag_for_target(
    dag: dict[str, list[str]], target: str
) -> str:
    """Render the sub-DAG needed to reach *target*."""
    needed = _collect_ancestors(dag, target)
    if len(needed) == 1:
        return f"🔗 Pipeline for <code>{target}</code>:\n  {target} (no dependencies)"

    sub = {n: [d for d in dag.get(n, []) if d in needed] for n in needed}
    lines = [f"🔗 Pipeline for <code>{target}</code>:"]
    for chain in _linearize(sub):
        lines.append("  " + " → ".join(chain))
    return _truncate("\n".join(lines))


def _collect_ancestors(
    dag: dict[str, list[str]], node: str
) -> set[str]:
    """BFS to collect *node* and all transitive dependencies."""
    visited: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        stack.extend(dag.get(n, []))
    return visited


def _linearize(dag: dict[str, list[str]]) -> list[list[str]]:
    """Produce display chains from a DAG.

    Each root-to-leaf path becomes one chain.  For diamond shapes
    this may show nodes on multiple lines, which is clearer than
    trying to do full ASCII-art in a Telegram message.

    ``dag[n]`` lists the *dependencies* (upstream) of ``n``.  Roots
    are therefore nodes with an empty dependency list.
    """
    roots = sorted(n for n, deps in dag.items() if not deps)
    if not roots:
        roots = sorted(dag)

    chains: list[list[str]] = []
    seen_chains: set[tuple[str, ...]] = set()

    def walk(node: str, path: list[str]) -> None:
        path = [*path, node]
        # Find dependents (nodes that list `node` as a dep)
        dependents = sorted(n for n, deps in dag.items() if node in deps)
        if not dependents:
            key = tuple(path)
            if key not in seen_chains:
                seen_chains.add(key)
                chains.append(path)
            return
        for dep in dependents:
            walk(dep, path)

    for root in roots:
        walk(root, [])

    # If nothing was produced (isolated nodes), list them individually
    if not chains:
        for node in sorted(dag):
            chains.append([node])

    return chains
