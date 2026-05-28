# Momentum Intelligence — Developer Makefile
# Gebruik: make <target>
# Windows: gebruik scripts/run_backend.py ipv make run

.PHONY: run test smoke lint clean help

help:
	@echo ""
	@echo "Momentum Intelligence — beschikbare commands:"
	@echo ""
	@echo "  make run     Start backend (localhost:8000, auto-reload)"
	@echo "  make test    Voer alle pytest tests uit"
	@echo "  make smoke   Voer smoke test uit (server moet draaien)"
	@echo "  make lint    Check imports en syntax"
	@echo "  make clean   Verwijder __pycache__ en .pytest_cache"
	@echo ""

run:
	uvicorn backend.app:app --reload --port 8000 --log-level info

test:
	pytest tests/ -v --tb=short

smoke:
	python3 scripts/smoke_test.py

lint:
	python3 -m py_compile \
		scoring/scoring_v1_2.py \
		backend/app.py \
		backend/logging_config.py \
		data/yahoo_client.py \
		data/assembler.py \
		data/news_client.py \
		cache/market_cache.py \
		schemas/ticker_snapshot.py \
		schemas/scoring_response.py \
		schemas/api_error.py
	@echo "Syntax OK"

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned"
