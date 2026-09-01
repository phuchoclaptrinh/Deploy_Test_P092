"""There is exactly one migration head, and it is the newest revision.

Owned by its own module rather than by whichever revision happens to be last.
Two heads means somebody's database silently stops at the older one, and that is
a property of the whole `alembic/versions` directory — pinning it inside a
revision's own test file means every later revision has to remember to come back
and edit a test about a different one, which is how the assertion ends up
deleted instead of updated.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _revisions() -> dict[str, str | None]:
    """Every revision id mapped to the one it follows."""
    graph: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = "([^"]+)"', text, re.M)
        down = re.search(r"^down_revision: str \| Sequence\[str\] \| None = (.+)$", text, re.M)
        if revision:
            parent = down.group(1).strip().strip('"') if down else None
            graph[revision.group(1)] = None if parent == "None" else parent
    return graph


def test_there_is_exactly_one_head():
    graph = _revisions()
    parents = {parent for parent in graph.values() if parent is not None}
    heads = sorted(set(graph) - parents)
    assert heads == ["d4e5f6a7b9ca"], f"expected one head, found {heads}"


def test_every_revision_points_at_one_that_exists():
    """A dangling down_revision makes `alembic upgrade head` fail at runtime."""
    graph = _revisions()
    for revision, parent in graph.items():
        if parent is not None:
            assert parent in graph, f"{revision} follows {parent}, which does not exist"


def test_the_chain_has_no_cycle():
    graph = _revisions()
    for revision in graph:
        seen, current = set(), revision
        while current is not None:
            assert current not in seen, f"cycle reached through {current}"
            seen.add(current)
            current = graph.get(current)
