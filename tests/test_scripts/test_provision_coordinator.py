from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_trusted_coordinator_provisioning_uses_supabase_auth_and_user_profiles():
    text = (ROOT / "scripts" / "provision_coordinator.py").read_text(encoding="utf-8")
    assert "build_supabase_admin_headers" in text
    assert "user_profiles" in text
    assert "COORDINATOR" in text
    assert "password" not in text[text.find("INSERT INTO user_profiles"):text.find("INSERT INTO user_profiles") + 500].lower()
