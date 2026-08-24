from src.models.scoring_schemas import ScoringResult


def test_backend_scoring_contract():
    result = ScoringResult(
        category_base=10,
        location_category_score=25,
        density_score=0,
        severity_score=10,
        score_total=45,
        priority_raw="P2",
        priority_final="P2",
        ceiling_applied=False,
        red_flag_override=False,
    )
    assert result.score_total == 45
