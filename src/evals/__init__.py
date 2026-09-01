"""Evaluation datasets for the risk rubric.

Nothing here runs in the application. These modules read a hand-authored test
set and compare it against the live implementation, so that a rubric change and
the dataset that measures it cannot drift apart without somebody being told.
"""

from src.evals.rubric_dataset import (
    BLOCKER_CODE_BY_LOCAL_NAME,
    CRITERION_BY_LOCAL_NAME,
    RubricCase,
    load_rubric_cases,
    load_workbook_rubric,
)

__all__ = [
    "BLOCKER_CODE_BY_LOCAL_NAME",
    "CRITERION_BY_LOCAL_NAME",
    "RubricCase",
    "load_rubric_cases",
    "load_workbook_rubric",
]
