#!/bin/bash


# Parse command line arguments
RUN_INTEGRATION=false
RUN_EVAL=false
for arg in "$@"; do
    if [ "$arg" = "--int" ]; then
        RUN_INTEGRATION=true
    fi
    if [ "$arg" = "--eval" ]; then
        RUN_EVAL=true
    fi
done

# ensure dependencies are installed and are always up to date
echo '============================================================='
echo "Running 'uv sync --extra dev'"
echo '============================================================='
uv sync --extra dev
echo '============================================================='
echo "Running 'uv run ruff format .'"
echo '============================================================='
uv run ruff format .
echo '============================================================='
echo "Running 'uv run ruff check --fix .'"
echo '============================================================='
uv run ruff check --fix .

# type check
echo '============================================================='
echo "Running 'uv run ty check'"
echo '============================================================='
uv run ty check

# run tests
echo '============================================================='
echo "Running 'uv run pytest --disable-warnings'"
echo '============================================================='
uv run pytest --disable-warnings

# Offline bow-scout vs production-like eval gates (no API keys).
# Pytest already includes src/tests/test_bow_scout_regression_gates.py;
# --eval re-runs the harness CLI against the frozen snapshot for a readable log.
if [ "$RUN_EVAL" = true ]; then
    echo '============================================================='
    echo "Running offline experiment eval gates (--eval, no API keys)..."
    echo '============================================================='
    PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py --check-baseline --skip-artifacts
    echo '============================================================='
    echo "Running daily hypothesis loop --check (offline, no ledger writes)..."
    echo '============================================================='
    PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --check \
        --runs-dir "${TMPDIR:-/tmp}/podly-daily-loop-check"
fi

# Run integration tests only if --int flag is provided
if [ "$RUN_INTEGRATION" = true ]; then
    echo '============================================================='
    echo "Running integration workflow checks..."
    echo '============================================================='
    uv run python scripts/check_integration_workflow.py
fi
