"""Tests for scriptbox.telegram.formatter."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scriptbox.executor import ExecutionResult
from scriptbox.loader import ScriptInfo
from scriptbox.telegram.formatter import (
    MAX_MESSAGE_LEN,
    format_dag,
    format_execution_results,
    format_logs,
    format_script_list,
    format_stats,
    format_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop(ctx: Any) -> None:
    pass


def _script(
    sid: str, name: str, schedule: str | None = None, depends_on: list[str] | None = None
) -> ScriptInfo:
    meta: dict[str, Any] = {"name": name}
    if schedule:
        meta["schedule"] = schedule
    if depends_on:
        meta["depends_on"] = depends_on
    return ScriptInfo(id=sid, path=Path(f"/fake/{sid}.py"), name=name, meta=meta, run_fn=_noop)


# ---------------------------------------------------------------------------
# format_script_list
# ---------------------------------------------------------------------------


class TestFormatScriptList:
    def test_three_scripts_one_paused(self):
        scripts = [
            _script("hn_k8s", "HN Kubernetes Monitor", schedule="0 8 * * *"),
            _script("price", "Price Tracker", schedule="*/30 * * * *"),
            _script("digest", "Email Digest", schedule="0 7 * * *"),
        ]
        result = format_script_list(scripts, paused={"price"})
        assert "1. ✅ HN Kubernetes Monitor" in result
        assert "every day 8:00" in result
        assert "2. ⏸ Price Tracker (paused)" in result
        assert "3. ✅ Email Digest" in result
        assert "every day 7:00" in result

    def test_empty_list(self):
        assert format_script_list([]) == "No scripts loaded."

    def test_manual_only_script(self):
        result = format_script_list([_script("m", "Manual Script")])
        assert "manual only" in result

    def test_truncation_with_many_scripts(self):
        scripts = [
            _script(f"script_{i:03d}", f"Script Number {i}", schedule="0 8 * * *")
            for i in range(200)
        ]
        result = format_script_list(scripts)
        assert len(result) <= MAX_MESSAGE_LEN
        assert "truncated" in result


# ---------------------------------------------------------------------------
# format_execution_results
# ---------------------------------------------------------------------------


class TestFormatExecutionResults:
    def test_success_failure_skip(self):
        results = [
            ExecutionResult("fetch_news", "success", 230, None, {"data": 1}),
            ExecutionResult("summarize", "failed", 500, "RuntimeError: connection timeout", None),
            ExecutionResult("send_digest", "skipped", 0, "upstream dependency failed", None),
        ]
        text = format_execution_results(results)
        assert "✅ fetch_news — 230ms" in text
        assert "❌ summarize — RuntimeError: connection timeout" in text
        assert "⏭ send_digest — skipped (upstream dependency failed)" in text

    def test_empty(self):
        assert format_execution_results([]) == "No results."


# ---------------------------------------------------------------------------
# format_logs
# ---------------------------------------------------------------------------


class TestFormatLogs:
    def test_five_runs(self):
        runs = [
            {"started_at": "2026-03-22T08:00:00+00:00", "status": "success", "duration_ms": 1204, "error": None},
            {"started_at": "2026-03-21T08:00:00+00:00", "status": "success", "duration_ms": 980, "error": None},
            {"started_at": "2026-03-20T08:00:00+00:00", "status": "failed", "duration_ms": 30000, "error": "timeout"},
            {"started_at": "2026-03-19T08:00:00+00:00", "status": "success", "duration_ms": 1100, "error": None},
            {"started_at": "2026-03-18T08:00:00+00:00", "status": "success", "duration_ms": 1050, "error": None},
        ]
        text = format_logs(runs, "hn_kubernetes")
        assert "📋 Logs for" in text
        assert "hn_kubernetes" in text
        assert "last 5" in text
        assert "Mar 22 08:00" in text
        assert "✅ 1,204ms" in text
        assert "❌ timeout" in text

    def test_empty_runs(self):
        text = format_logs([], "missing")
        assert "No logs" in text
        assert "missing" in text


# ---------------------------------------------------------------------------
# format_stats
# ---------------------------------------------------------------------------


class TestFormatStats:
    def test_per_script(self):
        stats = {
            "total_runs": 45,
            "success_count": 43,
            "failure_count": 2,
            "success_rate": 0.9556,
            "avg_duration_ms": 1180.0,
            "total_cost_usd": 0.12,
        }
        text = format_stats(stats, script_id="hn_kubernetes")
        assert "Stats for hn_kubernetes" in text
        assert "45 total (43 ✅, 2 ❌)" in text
        assert "95.6%" in text
        assert "1,180ms" in text
        assert "$0.12 total" in text
        assert "$0.003/run" in text

    def test_aggregate(self):
        stats = {
            "total_runs": 384,
            "success_count": 373,
            "failure_count": 11,
            "success_rate": 0.9714,
            "avg_duration_ms": 800.0,
            "total_cost_usd": 1.47,
        }
        text = format_stats(stats)
        assert "ScriptBox Stats" in text
        assert "Total runs: 384" in text
        assert "97.1%" in text
        assert "$1.47" in text

    def test_zero_cost_hidden(self):
        stats = {
            "total_runs": 10,
            "success_count": 10,
            "failure_count": 0,
            "success_rate": 1.0,
            "avg_duration_ms": 50.0,
            "total_cost_usd": 0.0,
        }
        text = format_stats(stats, script_id="cheap")
        assert "LLM cost" not in text


# ---------------------------------------------------------------------------
# format_status
# ---------------------------------------------------------------------------


class TestFormatStatus:
    def test_basic(self):
        scripts = [
            _script("a", "A", schedule="0 8 * * *"),
            _script("b", "B", schedule="0 9 * * *"),
            _script("c", "C"),
        ]
        info = {"last_run": "5 min ago", "uptime": "3d 4h", "docker": True}
        text = format_status(scripts, info)
        assert "3 loaded" in text
        assert "2 scheduled" in text
        assert "1 manual-only" in text
        assert "5 min ago" in text
        assert "3d 4h" in text
        assert "sandbox enabled" in text


# ---------------------------------------------------------------------------
# format_dag
# ---------------------------------------------------------------------------


class TestFormatDag:
    def test_linear_chain(self):
        # fetch -> summarize -> send
        dag = {
            "fetch": [],
            "summarize": ["fetch"],
            "send": ["summarize"],
        }
        text = format_dag(dag, target="send")
        assert "Pipeline for" in text
        assert "send" in text
        assert "fetch → summarize → send" in text

    def test_diamond(self):
        # A -> B, A -> C, B -> D, C -> D
        dag = {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        text = format_dag(dag, target="d")
        assert "Pipeline for" in text
        # Should show both paths through the diamond
        assert "a" in text
        assert "b" in text
        assert "c" in text
        assert "d" in text
        # Two chains: a→b→d and a→c→d
        assert text.count("→") >= 4

    def test_empty_dag(self):
        assert format_dag({}) == "No scripts in graph."

    def test_full_graph_no_target(self):
        dag = {"fetch": [], "process": ["fetch"], "send": ["process"]}
        text = format_dag(dag)
        assert "Dependency Graph" in text
        assert "fetch → process → send" in text
