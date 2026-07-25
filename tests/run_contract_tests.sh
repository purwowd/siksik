#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=python3
if [ -x "$project_root/backend/.venv/bin/python" ]; then
  python_bin="$project_root/backend/.venv/bin/python"
fi

"$python_bin" "$project_root/tests/android_adb_automation_contract.py"
"$python_bin" "$project_root/tests/sqlite_concurrency_check.py"
"$python_bin" "$project_root/tests/live_ingestion_progress_check.py"
cd "$project_root/backend"
"$python_bin" -m pytest \
  tests/test_communication_agent.py \
  tests/test_inventory_agent.py \
  tests/test_selection.py \
  tests/test_direct_transfer_contract.py
