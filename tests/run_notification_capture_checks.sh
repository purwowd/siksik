#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=python3
if [ -x "$project_root/backend/.venv/bin/python" ]; then
  python_bin="$project_root/backend/.venv/bin/python"
fi

PYTHONPYCACHEPREFIX=${PYTHONPYCACHEPREFIX:-/tmp/siksik-pycache} \
  "$python_bin" "$project_root/tests/notification_capture_contract.py"

cd "$project_root/android-agent"
JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17} \
ANDROID_HOME=${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools} \
ANDROID_SDK_ROOT=${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}} \
PATH="/opt/homebrew/bin:${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  ./gradlew \
    :app:testDebugUnitTest \
    --tests com.siksik.agent.CommunicationPolicyTest \
    :app:assembleDebug \
    :automation:assembleDebug
