#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def require(source: str, value: str, message: str) -> None:
    if value not in source:
        raise SystemExit(message)


def main() -> None:
    store = read(
        "android-agent/app/src/main/java/com/siksik/agent/source/communication/"
        "shared/CommunicationCaptureStore.kt"
    )
    policy = read(
        "android-agent/app/src/main/java/com/siksik/agent/source/communication/"
        "shared/CommunicationPolicy.kt"
    )
    listener = read(
        "android-agent/app/src/main/java/com/siksik/agent/notification/"
        "SessionNotificationListener.kt"
    )

    require(policy, "fun scopedRecordId(", "crawl-scoped record ID policy is missing")
    require(store, "active.crawlId,\n            identity", "notification record ID is not crawl-scoped")
    require(store, "SQLiteDatabase.CONFLICT_IGNORE", "duplicate-safe notification insert is missing")
    if 'insertOrThrow("notifications"' in store:
        raise SystemExit("notification callback still uses a process-fatal insert")
    require(listener, "captureSafely(\"notification_capture_failed\")", "posted callback guard is missing")
    require(listener, "captureSafely(\"notification_remove_failed\")", "removed callback guard is missing")
    require(listener, "event=notification_callback_failed", "safe callback logging is missing")

    print("notification capture contract: ok")


if __name__ == "__main__":
    main()
