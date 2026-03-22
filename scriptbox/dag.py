from __future__ import annotations

from scriptbox.loader import ScriptInfo


class CycleError(Exception):
    """Raised when the dependency graph contains a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Dependency cycle detected: {' -> '.join(cycle)}")


class MissingDependencyError(Exception):
    """Raised when a script depends on a script that doesn't exist."""

    def __init__(self, script_id: str, missing: str) -> None:
        self.script_id = script_id
        self.missing = missing
        super().__init__(
            f"Script {script_id!r} depends on {missing!r}, which does not exist"
        )


def build_graph(scripts: list[ScriptInfo]) -> dict[str, list[str]]:
    """Return an adjacency dict mapping each script id to its dependency ids."""
    return {
        s.id: list(s.meta.get("depends_on", []))
        for s in scripts
    }


def _validate_graph(graph: dict[str, list[str]]) -> None:
    """Raise `MissingDependencyError` for any dependency not present in *graph*."""
    for script_id, deps in graph.items():
        for dep in deps:
            if dep not in graph:
                raise MissingDependencyError(script_id, dep)


def _detect_cycle(graph: dict[str, list[str]]) -> None:
    """Raise `CycleError` if *graph* contains a cycle (DFS-based)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {node: WHITE for node in graph}
    path: list[str] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        path.append(node)
        for dep in graph[node]:
            if color[dep] == GRAY:
                # Extract the cycle portion from path.
                cycle_start = path.index(dep)
                cycle = path[cycle_start:] + [dep]
                raise CycleError(cycle)
            if color[dep] == WHITE:
                dfs(dep)
        path.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            dfs(node)


def topo_sort(scripts: list[ScriptInfo]) -> list[str]:
    """Return a full topological ordering of all *scripts*.

    Dependencies come before dependents.  Raises `MissingDependencyError` or
    `CycleError` on invalid graphs.
    """
    graph = build_graph(scripts)
    _validate_graph(graph)
    _detect_cycle(graph)

    # Kahn's algorithm — in_degree counts each node's own dependencies.
    in_degree: dict[str, int] = {node: len(deps) for node, deps in graph.items()}

    queue = sorted(node for node, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    while queue:
        node = queue.pop(0)
        order.append(node)
        # Find nodes that list `node` as a dependency.
        for candidate, deps in graph.items():
            if node in deps:
                in_degree[candidate] -= 1
                if in_degree[candidate] == 0:
                    # Insert in sorted position to keep output deterministic.
                    _insort(queue, candidate)

    return order


def subgraph(scripts: list[ScriptInfo], target: str) -> list[str]:
    """Return the minimal topologically-sorted execution order to run *target*.

    Includes *target* and all of its transitive dependencies.
    """
    graph = build_graph(scripts)
    _validate_graph(graph)

    if target not in graph:
        raise MissingDependencyError(target, target)

    # Collect transitive deps via BFS.
    needed: set[str] = set()
    stack = [target]
    while stack:
        node = stack.pop()
        if node in needed:
            continue
        needed.add(node)
        stack.extend(graph[node])

    # Build the sub-graph and sort it.
    sub_graph = {node: [d for d in graph[node] if d in needed] for node in needed}

    # Check for cycles within the subgraph.
    _detect_cycle(sub_graph)

    in_degree: dict[str, int] = {node: len(deps) for node, deps in sub_graph.items()}

    queue = sorted(node for node, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    while queue:
        node = queue.pop(0)
        order.append(node)
        for candidate, deps in sub_graph.items():
            if node in deps:
                in_degree[candidate] -= 1
                if in_degree[candidate] == 0:
                    _insort(queue, candidate)

    return order


def _insort(lst: list[str], val: str) -> None:
    """Insert *val* into the sorted list *lst* in order."""
    lo, hi = 0, len(lst)
    while lo < hi:
        mid = (lo + hi) // 2
        if lst[mid] < val:
            lo = mid + 1
        else:
            hi = mid
    lst.insert(lo, val)
