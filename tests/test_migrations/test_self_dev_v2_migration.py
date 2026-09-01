
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "a7b8c9d0e1f2_align_self_dev_v2.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_a7b8c9d0e1f2",
        MIGRATION,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_self_dev_v2_revision_is_forward_only_after_applied_f6():
    text = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "a7b8c9d0e1f2"' in text
    assert 'down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"' in text
    assert "SELF_DEV_V2_CUTOVER_REQUIRES_MANUAL_DATA_MIGRATION" in text


def test_historical_v2_cutover_documented_technician_removal_before_v3_restore():
    text = MIGRATION.read_text(encoding="utf-8")
    for removed in ("technician_profiles", "technician_skills", "ticket_assignments", "assignment_status_enum"):
        assert removed in text
    for required in ("user_profiles", "resident_profiles", "classification_status_enum", "information_requests", "incident_cases"):
        assert required in text


def test_v2_migration_seeds_canonical_categories_and_scoring_rule():
    text = MIGRATION.read_text(encoding="utf-8")
    assert "WATER_LEAK" in text and "NOISE_NEIGHBOR" in text
    assert "self-dev-v2.0.0" in text
    assert "SCORING_RULE_VERSION" in text
    assert "SCORING_CONFIG" in text

def test_v2_migration_seeds_locations_and_one_primary_resident_per_unit():
    text = MIGRATION.read_text(encoding="utf-8")
    assert "uq_resident_profiles_one_primary_per_unit" in text
    for location_type in ("CORRIDOR", "FIRE_EXIT", "BASEMENT_PARKING", "INSIDE_UNIT", "MAIN_DOOR", "SECURITY_DOOR"):
        assert location_type in text
    assert "adjacency_index" in text

def test_seed_v2_catalogs_uses_safe_scoring_bind_params():
    module = _load_migration()

    captured = []

    with patch.object(
        module.op,
        "execute",
        side_effect=captured.append,
    ):
        module._seed_v2_catalogs()

    scoring_statement = None

    for statement in captured:
        statement_text = str(statement)

        if "scoring_rule_versions" not in statement_text:
            continue

        assert not isinstance(statement, str), (
            "scoring_rule_versions INSERT must not be raw SQL containing JSON"
        )

        scoring_statement = statement
        break

    assert scoring_statement is not None

    assert isinstance(
        scoring_statement,
        sa.sql.elements.TextClause,
    )

    bind_params = scoring_statement._bindparams

    assert set(bind_params) == {
        "version",
        "config",
    }

    config_json = bind_params["config"].value
    config = json.loads(config_json)

    assert config["category_base"] == {
        "WATER_LEAK": 10,
        "ELECTRICAL_SHORT": 50,
        "ELEVATOR": 35,
        "SERIOUS_SECURITY_DISORDER": 40,
        "LOCK_DOOR": 25,
        "HVAC": 20,
        "LOCAL_POWER_OUTAGE": 25,
        "STRUCTURAL_ISSUE": 20,
        "COMMON_LIGHT": 10,
        "ODOR_HYGIENE": 10,
        "NOISE_NEIGHBOR": 10,
    }

    assert config["location_bonus"] == {
        "LOCK_DOOR": {
            "MAIN_DOOR": 30,
            "SECURITY_DOOR": 30,
        },
        "COMMON_LIGHT": {
            "FIRE_EXIT": 25,
        },
    }

    assert config["density"] == {
        "1": 0,
        "2-3": 15,
        "4+": 30,
        "categories": [
            "WATER_LEAK",
            "ELECTRICAL_SHORT",
        ],
    }

    assert config["severity"] == {
        "LOW": 0,
        "MEDIUM": 10,
        "HIGH": 20,
    }

    assert config["thresholds"] == {
        "P1": "<30",
        "P2": "30-59",
        "P3": ">=60",
    }

    assert config["sla_minutes"] == {
        "P3": 5,
        "P2": 180,
        "P1": 4320,
    }
