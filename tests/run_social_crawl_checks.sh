#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=python3
if [ -x "$project_root/backend/.venv/bin/python" ]; then
  python_bin="$project_root/backend/.venv/bin/python"
fi

"$python_bin" "$project_root/tests/social_crawl_contract.py"
"$python_bin" "$project_root/tests/social_host_ocr_contract.py"
"$python_bin" -m py_compile \
  "$project_root/backend/app/acquisition/agent_client.py" \
  "$project_root/backend/app/acquisition/adb.py" \
  "$project_root/backend/app/acquisition/automation.py" \
  "$project_root/backend/app/acquisition/bootstrap.py" \
  "$project_root/backend/app/acquisition/direct_transfer.py" \
  "$project_root/backend/app/acquisition/social_ocr.py" \
  "$project_root/backend/app/services/analysis.py" \
  "$project_root/backend/app/services/reports.py"

cd "$project_root/android-agent"
JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17} \
ANDROID_HOME=${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools} \
ANDROID_SDK_ROOT=${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}} \
PATH="/opt/homebrew/bin:${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  ./gradlew :app:assembleDebug :automation:assembleDebug

cd "$project_root/frontend"
npm run build
