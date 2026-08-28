"""Operator-selected focus for device sources, social apps, or both."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.models.schemas import AnalysisScope

DEVICE_SOURCE_IDS = (
    "gallery",
    "documents",
    "contacts",
    "sms",
    "notifications",
    "recovery",
    "email",
    "browser",
    "whatsapp",
    "notes",
)
SOCIAL_TARGET_IDS = ("instagram", "facebook", "x")
SOCIAL_TARGET_PACKAGES = {
    "instagram": "com.instagram.android",
    "facebook": "com.facebook.katana",
    "x": "com.twitter.android",
}
PACKAGE_TO_SOCIAL_TARGET = {
    package: target for target, package in SOCIAL_TARGET_PACKAGES.items()
}

DEVICE_SOURCE_ADAPTERS: dict[str, frozenset[str]] = {
    "gallery": frozenset(
        {
            "public_whatsapp",
            "public_telegram",
            "media_store_image",
            "media_store_video",
            "media_store_audio",
        }
    ),
    "documents": frozenset({"shared_storage_document", "document_tree"}),
    "contacts": frozenset({"contacts_content_provider"}),
    "sms": frozenset({"sms_content_provider"}),
    "notifications": frozenset({"notification_listener"}),
}
SOCIAL_ADAPTERS = frozenset({"accessibility_visible_ui"})

_FILE_SOURCE_TO_DEVICE = {
    "gallery": "gallery",
    "dcim": "gallery",
    "download": "gallery",
    "image": "gallery",
    "video": "gallery",
    "audio": "gallery",
    "media_image": "gallery",
    "media_video": "gallery",
    "media_audio": "gallery",
    "telegram": "gallery",
    "document": "documents",
    "documents": "documents",
    "contact": "contacts",
    "contacts": "contacts",
    "sms": "sms",
    "notification": "notifications",
    "notification_listener": "notifications",
    "recovered_trash": "recovery",
    "recovered_cache": "recovery",
    "ios_hidden": "recovery",
    "ios_recently_deleted": "recovery",
    "ios_recovered_cache": "recovery",
    "ios_deleted_metadata": "recovery",
    "email": "email",
    "gmail": "email",
    "browser_history_full": "browser",
    "browser_history_partial": "browser",
    "whatsapp": "whatsapp",
    "notes": "notes",
}


def _unique(values: Iterable[str], allowed: Sequence[str]) -> tuple[str, ...]:
    allowed_set = set(allowed)
    result: list[str] = []
    for raw in values:
        key = str(raw or "").strip().casefold()
        if key not in allowed_set or key in result:
            continue
        result.append(key)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    scope: AnalysisScope
    device_sources: tuple[str, ...]
    social_targets: tuple[str, ...]

    @property
    def social_packages(self) -> tuple[str, ...]:
        if self.scope == AnalysisScope.DEVICE:
            return ()
        return tuple(
            SOCIAL_TARGET_PACKAGES[target]
            for target in self.social_targets
            if target in SOCIAL_TARGET_PACKAGES
        )

    @property
    def includes_social(self) -> bool:
        return self.scope != AnalysisScope.DEVICE and bool(self.social_packages)

    def includes_device_source(self, source: str) -> bool:
        return self.scope != AnalysisScope.SOCIAL and source in self.device_sources

    @property
    def includes_recovery(self) -> bool:
        return self.includes_device_source("recovery")

    @property
    def includes_gallery(self) -> bool:
        return self.includes_device_source("gallery")

    @property
    def includes_documents(self) -> bool:
        return self.includes_device_source("documents")

    @property
    def includes_contacts(self) -> bool:
        return self.includes_device_source("contacts")

    @property
    def includes_sms(self) -> bool:
        return self.includes_device_source("sms")

    @property
    def includes_notifications(self) -> bool:
        return self.includes_device_source("notifications")

    @property
    def includes_email(self) -> bool:
        return self.includes_device_source("email")

    @property
    def includes_browser(self) -> bool:
        return self.includes_device_source("browser")

    @property
    def includes_whatsapp(self) -> bool:
        return self.includes_device_source("whatsapp")

    @property
    def includes_notes(self) -> bool:
        return self.includes_device_source("notes")

    def inventory_adapters(self) -> frozenset[str]:
        adapters: set[str] = set()
        if self.scope != AnalysisScope.SOCIAL:
            for source in self.device_sources:
                adapters.update(DEVICE_SOURCE_ADAPTERS.get(source, ()))
        if self.includes_social:
            adapters.update(SOCIAL_ADAPTERS)
        return frozenset(adapters)

    def allows_file_source(self, source: str | None) -> bool:
        key = str(source or "").strip().casefold()
        if key in {"visible_ui", "accessibility_visible_ui", "social"}:
            return self.includes_social
        device_source = _FILE_SOURCE_TO_DEVICE.get(key)
        if device_source is None:
            return True
        return self.includes_device_source(device_source)

    def to_progress(self) -> dict[str, Any]:
        return {
            "analysis_scope": self.scope.value,
            "device_sources": list(self.device_sources),
            "social_targets": list(self.social_targets),
        }


def default_analysis_plan() -> AnalysisPlan:
    return AnalysisPlan(
        scope=AnalysisScope.COMBINED,
        device_sources=DEVICE_SOURCE_IDS,
        social_targets=SOCIAL_TARGET_IDS,
    )


def build_analysis_plan(
    *,
    scope: AnalysisScope | str | None = None,
    device_sources: Sequence[str] | None = None,
    social_targets: Sequence[str] | None = None,
) -> AnalysisPlan:
    parsed_scope = (
        scope
        if isinstance(scope, AnalysisScope)
        else AnalysisScope(str(scope or AnalysisScope.COMBINED.value).strip().casefold())
    )
    devices = _unique(device_sources or (), DEVICE_SOURCE_IDS)
    socials = _unique(social_targets or (), SOCIAL_TARGET_IDS)
    if parsed_scope == AnalysisScope.DEVICE:
        devices = devices or DEVICE_SOURCE_IDS
        socials = ()
    elif parsed_scope == AnalysisScope.SOCIAL:
        devices = ()
        socials = socials or SOCIAL_TARGET_IDS
    else:
        devices = devices or DEVICE_SOURCE_IDS
        socials = socials or SOCIAL_TARGET_IDS
    return AnalysisPlan(
        scope=parsed_scope,
        device_sources=tuple(devices),
        social_targets=tuple(socials),
    )


def analysis_plan_from_progress(progress: Mapping[str, Any] | None) -> AnalysisPlan:
    if not isinstance(progress, Mapping):
        return default_analysis_plan()
    try:
        return build_analysis_plan(
            scope=progress.get("analysis_scope"),
            device_sources=(
                progress.get("device_sources")
                if isinstance(progress.get("device_sources"), list)
                else None
            ),
            social_targets=(
                progress.get("social_targets")
                if isinstance(progress.get("social_targets"), list)
                else None
            ),
        )
    except (TypeError, ValueError):
        return default_analysis_plan()
