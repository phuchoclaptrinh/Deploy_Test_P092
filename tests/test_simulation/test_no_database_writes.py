"""The read-only guarantee, asserted structurally rather than promised.

"The simulation does not write to the database, and does not change production
dispatch" is the claim the whole feature rests on. A test that ran a simulation
and then checked a table is empty would only prove it for the path that test
happened to take. These prove it for every path:

* the simulator's **import graph** contains no database, repository, service or
  ORM module, so there is nothing in scope that *could* write;
* it imports from `src/dispatch/` only the **pure, read-only** modules, and
  never the worker or the dispatch service that persists;
* the endpoint's **signature** takes no session, so the request has no
  connection to write through either;
* and a run still produces three full results, so the guarantee is not being
  kept by the feature quietly doing nothing.

`src.dispatch.shift` and `src.dispatch.durations` *are* imported: a calendar
and a table of estimates, both pure functions over plain values. Nothing else
from `src/dispatch/` is, and in particular the simulator does not call
production's scheduler -- `NEW_APP` is a hypothetical policy, not a copy of what
the deployed dispatcher does, and importing the dispatcher would invite exactly
the confusion the naming is there to prevent.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.simulation.engine import run_comparison
from src.simulation.models import Outcome
from tests.test_simulation.conftest import scenario, technician, ticket

SIMULATION_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "simulation"
SLA_CLOCK = Path(__file__).resolve().parents[2] / "src" / "domain" / "sla_clock.py"

#: Anything that can reach a connection. `sqlalchemy` is on the list even though
#: it cannot write on its own: a `Session` import here would mean somebody was
#: heading somewhere this package is not allowed to go.
FORBIDDEN_PREFIXES = (
    "src.database",
    "src.repositories",
    "src.services",
    "src.agents",
    "src.workers",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "supabase",
)

#: The only `src.dispatch` modules the simulator may import: a working-hours
#: calendar and a table of P80 estimates. Both are pure functions over plain
#: values, neither holds a session, and neither decides anything. `service`,
#: `scheduler`, `eligibility` and the worker are all absent -- the simulator
#: neither writes through them nor borrows decisions from them.
ALLOWED_DISPATCH_MODULES = {
    "src.dispatch.shift",
    "src.dispatch.durations",
}


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def forbidden(names: set[str]) -> set[str]:
    return {
        name
        for name in names
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in FORBIDDEN_PREFIXES)
    }


@pytest.mark.parametrize("path", sorted(SIMULATION_PACKAGE.glob("*.py")), ids=lambda path: path.name)
def test_the_simulator_imports_nothing_that_can_write(path: Path):
    offenders = forbidden(module_imports(path))
    assert not offenders, f"{path.name} imports {sorted(offenders)}; the simulator must stay read-only"


def test_the_sla_clock_imports_nothing_that_can_write():
    """It is shared with production, so it must stay a pure calendar function --
    the day `recalculate_sla` calls it, it will be running inside a session that
    is not its to use."""
    assert not forbidden(module_imports(SLA_CLOCK))


@pytest.mark.parametrize("path", sorted(SIMULATION_PACKAGE.glob("*.py")), ids=lambda path: path.name)
def test_the_simulator_touches_only_the_pure_dispatch_modules(path: Path):
    """`src.dispatch.service` and `src.workers.dispatch_worker` persist. They are
    the modules this feature promised not to change, and it does not import
    them, let alone modify them."""
    dispatch_imports = {name for name in module_imports(path) if name.startswith("src.dispatch")}
    assert dispatch_imports <= ALLOWED_DISPATCH_MODULES, (
        f"{path.name} imports {sorted(dispatch_imports - ALLOWED_DISPATCH_MODULES)}"
    )


def test_production_dispatch_modules_are_never_imported_anywhere_in_the_feature():
    for path in [*SIMULATION_PACKAGE.glob("*.py"), SLA_CLOCK]:
        names = module_imports(path)
        assert "src.dispatch.service" not in names
        assert not any(name.startswith("src.workers") for name in names)


def test_the_endpoint_takes_no_database_session():
    """Not "does not commit" -- there is no session in scope at all. The only
    query this request makes is the one `require_coordinator` makes to prove who
    is calling."""
    from src.api.routes.coordinator import simulation

    parameters = inspect.signature(simulation.run_simulation).parameters
    assert "db" not in parameters
    dependencies = {
        getattr(parameter.default, "dependency", None).__name__
        for parameter in parameters.values()
        if getattr(parameter.default, "dependency", None) is not None
    }
    assert dependencies == {"require_coordinator"}


def test_the_route_module_imports_nothing_that_can_write():
    from src.api.routes.coordinator import simulation

    # `src.api.dependencies.auth` is allowed: proving who is calling is a read,
    # and it is the same guard every Coordinator route uses.
    assert not forbidden(module_imports(Path(inspect.getsourcefile(simulation))))


def test_a_run_still_produces_two_full_results():
    """The guarantee above must not be kept by the feature doing nothing."""
    comparison = run_comparison(
        scenario(
            [ticket("T001"), ticket("T002", floor=20, required_skill="hvac")],
            [technician("KTV_01", skills=("plumbing",)), technician("KTV_02", skills=("hvac",), start_floor=18)],
        )
    )
    for result in (comparison.old_app, comparison.new_app):
        assert result.summary.assigned_tickets == 2
        assert all(outcome.outcome is Outcome.ASSIGNED for outcome in result.tickets)
        assert all(outcome.work_started_at is not None for outcome in result.tickets)
        assert all(outcome.completed_at is not None for outcome in result.tickets)
    assert comparison.comparison is not None


def test_no_result_field_is_named_like_a_production_column():
    """The result row must not read as a production ticket update.

    `completed_at` is the one name that overlaps, and it earns its place: the
    screen genuinely reports when simulated work finished. What matters is that
    nothing here carries a ticket id shaped like a database key, a status field
    named like `TicketStatus`, or an assignment id -- a row one careless spread
    away from an update.
    """
    from src.simulation.models import TicketOutcome

    fields = set(TicketOutcome.__dataclass_fields__)
    # No database identity, no production status, no assignment row.
    assert not fields & {"id", "ticket", "status", "assignment_id", "technician", "user_id", "category_id"}
    # And the three execution timestamps the screen is built on really are there,
    # under the names the whole feature reasons in.
    assert {"departed_at", "work_started_at", "completed_at"} <= fields
