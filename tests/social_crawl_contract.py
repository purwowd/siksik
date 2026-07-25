#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.acquisition.agent_client import VisibleUiMetadataV1


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def require(source: str, value: str, message: str) -> None:
    if value not in source:
        raise SystemExit(message)


def main() -> None:
    driver = read(
        "android-agent/automation/src/main/java/com/siksik/agent/automation/"
        "UiAutomatorDriver.kt"
    )
    contract = read(
        "android-agent/automation/src/main/java/com/siksik/agent/automation/"
        "AutomationContract.kt"
    )
    instrumentation = read(
        "android-agent/automation/src/main/java/com/siksik/agent/automation/"
        "SocialCrawlInstrumentation.kt"
    )
    debug_mapper = read(
        "android-agent/automation/src/main/java/com/siksik/agent/automation/"
        "AutomationDebugMapper.kt"
    )
    inventory_routes = read(
        "android-agent/app/src/main/java/com/siksik/agent/api/InventoryRoutes.kt"
    )
    capture_store = read(
        "android-agent/app/src/main/java/com/siksik/agent/source/communication/shared/"
        "CommunicationCaptureStore.kt"
    )
    inventory_json = read(
        "android-agent/app/src/main/java/com/siksik/agent/source/inventory/"
        "InventoryRecordJson.kt"
    )
    agent_client = read("backend/app/acquisition/agent_client.py")
    automation = read("backend/app/acquisition/automation.py")
    adb = read("backend/app/acquisition/adb.py")
    config = read("backend/app/core/config.py")
    reports = read("backend/app/services/reports.py")
    frontend_api = read("frontend/src/api.ts")
    report_page = read("frontend/src/pages/ReportPage.tsx")

    require(driver, "private const val MAX_ARCHIVE_SCROLLS = 3", "archive must stop after three scrolls")
    require(driver, "if (postCount <= VISIBLE_GRID_POSTS) return 0", "small Instagram grids must not scroll")
    require(driver, "swipeInstagramGrid()", "Instagram must use an overlapping grid gesture")
    require(driver, "activeWindowBounds()", "coordinate fallback must use active-window bounds")
    require(driver, 'systemBarInset("navigation_bar_height")', "navigation bar must be excluded")
    require(driver, "instagramOptionsMenuEntry(ARCHIVE_LABELS, nodes)", "Archive menu proof is missing")
    require(driver, "INSTAGRAM_OPTIONS_MENU_COMPANION_LABELS", "Instagram menu state proof is weak")
    require(driver, "INSTAGRAM_PROFILE_MENU_EXCLUDED_LABELS", "unsafe profile-menu targets are not blocked")
    require(driver, "INSTAGRAM_NON_STORY_ARCHIVE_LABELS", "archive mode switching is missing")
    require(driver, "PROFILE_NAVIGATION_BUDGET_MS = 8_000L", "Instagram profile fast-path budget is missing")
    require(driver, "PROFILE_PROOF_ATTEMPTS = 24", "Instagram profile render wait is too short")
    require(driver, "INSTAGRAM_OWN_PROFILE_LABELS", "strong own-profile proof is missing")
    require(driver, "editVisible ||", "Edit Profile must be accepted as own-account proof")
    require(driver, "shareVisible ||", "Share Profile must be accepted as own-account proof")
    require(driver, "performAccessibilityClick", "Instagram accessibility click path is missing")
    require(driver, "clickInstagramProfileTab()", "Instagram profile node click is missing")
    require(driver, "performAccessibilityScrollForward", "Instagram accessibility scroll is missing")
    require(driver, "private fun instagramProbeNodes()", "bounded Instagram probe is missing")
    require(driver, "setWaitForIdleTimeout(UI_AUTOMATOR_IDLE_TIMEOUT_MS)", "animated UI idle bound is missing")
    require(driver, "foregroundPackageName()", "non-blocking foreground probe is missing")
    if "device.findObjects(By.pkg(INSTAGRAM_PACKAGE))" in driver:
        raise SystemExit("Instagram must not enumerate the complete UiAutomator package tree")
    if "instagramSurfaceSignature" in driver:
        raise SystemExit("Instagram scrolling must not take redundant comparison screenshots")
    require(driver, "event=instagram_profile_navigation", "Instagram profile-stage telemetry is missing")
    require(driver, "val hadActiveScope = activePackage != null || activeScope != null", "duplicate scope-ledger writes were not removed")
    if "dismissInstagramBlockingDialogs()" in driver:
        raise SystemExit("Instagram still scans every dialog label before the first profile tap")
    open_profile = driver.split("private fun openInstagramOwnProfile", 1)[1].split(
        "private fun clickInstagramProfileTabCoordinate", 1
    )[0]
    home_fast_path = open_profile.split("val initialClicked =", 1)[1]
    if home_fast_path.index("clickInstagramProfileTab()") > home_fast_path.index(
        "repeat(PROFILE_PROOF_ATTEMPTS)"
    ):
        raise SystemExit("Instagram profile proof still runs before the first profile tap")
    require(driver, "clickInstagramProfileMenuCoordinate()", "bounded hamburger tap is missing")
    require(driver, "waitForInstagramOptionsMenuNodes()", "bounded Archive-menu proof is missing")
    require(driver, "INSTAGRAM_ARCHIVE_PROBE_ATTEMPTS = 16", "Archive render wait is too short")
    require(debug_mapper, "device.takeScreenshot(screenshot)", "full-screen debug mapping is missing")
    require(debug_mapper, 'MANIFEST_FILE_NAME = "mapping.json"', "debug mapping manifest is missing")
    require(instrumentation, 'arguments.getString("debug_snapshots")', "debug mapping flag is missing")
    require(automation, "_pull_debug_mapping", "host debug mapping pull is missing")
    require(adb, "pull_social_debug_mapping", "ADB debug mapping pull is missing")
    require(adb, '"mapping" / target_package', "temp_crawl mapping destination is missing")
    if '"action_bar_button_action"' in driver or '"more_options"' in driver:
        raise SystemExit("Instagram Archive must not use generic action-bar resources")
    if "ARCHIVE_LABELS + YOUR_ACTIVITY_LABELS + INSTAGRAM_OPTIONS_LABELS" in driver:
        raise SystemExit("profile Options label must not verify the opened menu")
    require(driver, "captureXTimelineScope", "X viewport text extraction is missing")
    require(driver, "xOwnedTimelineRows", "X tweet/reply row extraction is missing")
    require(driver, "xVisitedViewportSignatures", "X end-of-list deduplication is missing")
    if "openXOwnItem" in driver or "advanceXOwnItem" in driver:
        raise SystemExit("X must not open tweet detail for timeline capture")
    x_start = contract.find("class XOwnAccountStrategy")
    if x_start < 0:
        raise SystemExit("XOwnAccountStrategy is missing")
    x_end = contract.find("\nclass ", x_start + 1)
    x_block = contract[x_start : x_end if x_end > 0 else None]
    if "SocialCaptureMode.TEXT_ONLY" not in x_block:
        raise SystemExit("X must remain text-only")
    if "SocialCaptureMode.VISUAL" in x_block:
        raise SystemExit("XOwnAccountStrategy must not use VISUAL")
    require(contract, "SocialScope.OWN_STORY_ARCHIVE -> 3", "archive strategy limit is invalid")
    require(contract, "require(maxScreenshots in 0..48)", "Instagram viewport capture budget is too small")
    require(instrumentation, 'boundedInt(arguments, "max_screenshots", 0, 48)', "instrumentation screenshot limit is inconsistent")
    require(inventory_routes, "screenshotValues.length() > 48", "agent API screenshot limit is inconsistent")
    require(capture_store, "result.screenshotIds.size <= 48", "capture-store screenshot limit is inconsistent")
    require(agent_client, "screenshot_ids: list[str] = Field(max_length=48)", "backend result limit is inconsistent")
    require(automation, "range(0, 49)", "backend automation screenshot range is inconsistent")
    require(config, "android_agent_social_quick_screenshots: int = 24", "quick Instagram viewport budget is invalid")
    require(config, "android_agent_social_full_screenshots: int = 46", "full Instagram viewport budget is invalid")
    require(contract, "SocialScope.OWN_TWEETS,", "X Posts scrolling contract is missing")
    require(contract, "SocialScope.OWN_REPLIES,", "X Replies scrolling contract is missing")

    for field in ("profile_display_name", "profile_bio", "profile_metrics"):
        require(inventory_json, f'"{field}"', f"Android profile field {field} is missing")
        require(agent_client, field, f"backend profile field {field} is missing")
    for metric in ("posts", "followers", "following"):
        require(inventory_json, f'"{metric}"', f"Android metric {metric} is missing")
        require(reports, f'"{metric}"', f"report metric {metric} is missing")
        require(frontend_api, metric, f"frontend metric {metric} is missing")
    require(report_page, "PROFILE_METRIC_LABEL", "existing report layout does not render profile metrics")

    metadata = VisibleUiMetadataV1.model_validate(
        {
            "package_name": "com.twitter.android",
            "social_scope": "own_profile",
            "window_id": -1,
            "activity_context": "android.view.View",
            "event_type": 2048,
            "screen_sequence": 1,
            "nodes": [],
            "screenshot_ids": [],
            "profile_links": ["https://example.test/profile"],
            "profile_username": "contract_user",
            "profile_display_name": "Contract User",
            "profile_bio": "Contract bio",
            "profile_metrics": {"posts": 12, "followers": 34, "following": 56},
            "warning_codes": [],
        }
    )
    if metadata.profile_metrics is None or metadata.profile_metrics.followers != 34:
        raise SystemExit("structured profile metrics contract is invalid")

    print("social crawl contract: ok")


if __name__ == "__main__":
    main()
