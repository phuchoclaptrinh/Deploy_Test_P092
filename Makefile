.PHONY: run worker test lint format typecheck check clean braintrust-smoke

run:
	python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# The durable assignment worker (contract §5). Tracing initializes in its
# main(), same as the API does in its lifespan.
worker:
	python -m src.workers.assignment_worker

# One identifiable Braintrust trace, no model request. Not for production.
braintrust-smoke:
	python scripts/braintrust_smoke_test.py

test:
	python -m pytest tests -v

lint:
	python -m ruff check src tests scripts alembic

format:
	python -m ruff format src tests scripts alembic

typecheck:
	mypy src/

scan:
	python scripts/scan_secrets.py

check: lint scan test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
