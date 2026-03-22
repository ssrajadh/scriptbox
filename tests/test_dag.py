from __future__ import annotations

from pathlib import Path

import pytest

from scriptbox.dag import (
    CycleError,
    MissingDependencyError,
    build_graph,
    subgraph,
    topo_sort,
)
from scriptbox.loader import ScriptInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_PATH = Path("/dev/null")


async def _noop(ctx):
    pass


def _make(id: str, depends_on: list[str] | None = None) -> ScriptInfo:
    meta = {"name": id}
    if depends_on is not None:
        meta["depends_on"] = depends_on
    return ScriptInfo(
        id=id,
        path=_DUMMY_PATH,
        name=id,
        meta=meta,
        run_fn=_noop,
    )


# ---------------------------------------------------------------------------
# Linear chain: A -> B -> C  (A depends on B, B depends on C)
# ---------------------------------------------------------------------------


class TestLinearChain:
    @pytest.fixture()
    def scripts(self) -> list[ScriptInfo]:
        return [
            _make("a", ["b"]),
            _make("b", ["c"]),
            _make("c"),
        ]

    def test_topo_order(self, scripts: list[ScriptInfo]):
        order = topo_sort(scripts)
        assert order == ["c", "b", "a"]

    def test_subgraph_for_a(self, scripts: list[ScriptInfo]):
        order = subgraph(scripts, "a")
        assert order == ["c", "b", "a"]

    def test_subgraph_for_b(self, scripts: list[ScriptInfo]):
        """Subgraph for B should NOT include A."""
        order = subgraph(scripts, "b")
        assert order == ["c", "b"]

    def test_subgraph_for_c(self, scripts: list[ScriptInfo]):
        order = subgraph(scripts, "c")
        assert order == ["c"]


# ---------------------------------------------------------------------------
# Diamond: D -> {B, C}, B -> A, C -> A
# ---------------------------------------------------------------------------


class TestDiamond:
    @pytest.fixture()
    def scripts(self) -> list[ScriptInfo]:
        return [
            _make("d", ["b", "c"]),
            _make("b", ["a"]),
            _make("c", ["a"]),
            _make("a"),
        ]

    def test_a_before_b_and_c(self, scripts: list[ScriptInfo]):
        order = topo_sort(scripts)
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")

    def test_b_and_c_before_d(self, scripts: list[ScriptInfo]):
        order = topo_sort(scripts)
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_d_is_last(self, scripts: list[ScriptInfo]):
        order = topo_sort(scripts)
        assert order[-1] == "d"

    def test_all_four_present(self, scripts: list[ScriptInfo]):
        order = topo_sort(scripts)
        assert set(order) == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_simple_cycle(self):
        scripts = [
            _make("a", ["b"]),
            _make("b", ["c"]),
            _make("c", ["a"]),
        ]
        with pytest.raises(CycleError) as exc_info:
            topo_sort(scripts)
        # The cycle path should contain all three nodes.
        assert set(exc_info.value.cycle[:-1]) == {"a", "b", "c"}
        # The first and last element should be the same (cycle closure).
        assert exc_info.value.cycle[0] == exc_info.value.cycle[-1]

    def test_self_cycle(self):
        scripts = [_make("a", ["a"])]
        with pytest.raises(CycleError):
            topo_sort(scripts)

    def test_cycle_error_message(self):
        scripts = [
            _make("a", ["b"]),
            _make("b", ["a"]),
        ]
        with pytest.raises(CycleError, match="Dependency cycle detected"):
            topo_sort(scripts)


# ---------------------------------------------------------------------------
# Missing dependency
# ---------------------------------------------------------------------------


class TestMissingDependency:
    def test_missing_dep_raises(self):
        scripts = [_make("a", ["nonexistent"])]
        with pytest.raises(MissingDependencyError) as exc_info:
            topo_sort(scripts)
        assert exc_info.value.script_id == "a"
        assert exc_info.value.missing == "nonexistent"

    def test_missing_dep_message(self):
        scripts = [_make("a", ["nonexistent"])]
        with pytest.raises(MissingDependencyError, match="nonexistent"):
            topo_sort(scripts)


# ---------------------------------------------------------------------------
# Independent scripts (no deps)
# ---------------------------------------------------------------------------


class TestIndependentScripts:
    def test_both_returned(self):
        scripts = [_make("x"), _make("y")]
        order = topo_sort(scripts)
        assert set(order) == {"x", "y"}

    def test_length(self):
        scripts = [_make("x"), _make("y")]
        order = topo_sort(scripts)
        assert len(order) == 2


# ---------------------------------------------------------------------------
# Single script with no deps
# ---------------------------------------------------------------------------


class TestSingleScript:
    def test_single(self):
        scripts = [_make("solo")]
        assert topo_sort(scripts) == ["solo"]

    def test_single_subgraph(self):
        scripts = [_make("solo")]
        assert subgraph(scripts, "solo") == ["solo"]


# ---------------------------------------------------------------------------
# build_graph visualization helper
# ---------------------------------------------------------------------------


class TestBuildGraph:
    def test_graph_dict(self):
        scripts = [
            _make("a", ["b"]),
            _make("b"),
        ]
        g = build_graph(scripts)
        assert g == {"a": ["b"], "b": []}

    def test_empty(self):
        assert build_graph([]) == {}
