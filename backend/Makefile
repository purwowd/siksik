# SADT PoC — perintah cepat

.PHONY: test test-acceptance test-perf install run run-gpu run-lab

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -r requirements.txt -r requirements-dev.txt

test:
	. .venv/bin/activate && pytest -q

test-acceptance:
	. .venv/bin/activate && python scripts/run_acceptance.py --perf

test-gpu-server:
	. .venv/bin/activate && SADT_REQUIRE_GPU=1 python scripts/run_acceptance.py --perf --require-gpu

run:
	. .venv/bin/activate && uvicorn app.main:app --host $${SADT_API_HOST:-127.0.0.1} --port $${SADT_API_PORT:-8000} --workers 1

run-gpu:
	. .venv/bin/activate && python run.py --reload --host $${SADT_API_HOST:-127.0.0.1} --port $${SADT_API_PORT:-8000} --gpu

run-lab:
	cd .. && bash scripts/start_poc.sh
