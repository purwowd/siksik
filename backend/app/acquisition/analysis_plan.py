"""Operator analysis focus: device sources vs social apps vs both."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.models.enums import AnalysisScope

DEVICE_SOURCE_IDS = (
    "gallery",
    "documents",
    "contacts",
    "sms",
    "notifications",
    "recovery",
)
SOCIAL_TARGET_IDS = ("instagram", "facebook", "x")
SOCIAL_TARGET_PACKAGES = {
    "instagram": "com.instagram.android",
    "facebook": "com.facebook.katana",
    "x": "com.twitter.android",
}
PACKAGE_TO_SOCIAL_TARGET = {package: key for key, package in SOCIAL_TARGET_PACKAGES.items()}

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
    "media_image": "gallery",
    "media_video": "gallery",
    "media_audio": "gallery",
    "whatsapp": "gallery",
    "telegram": "gallery",
    "document": "documents",
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
}


def _unique(values: Iterable[str], allowed: Sequence[str]) -> tuple[str, ...]:
    allowed_set = set(allowed)
    seen: list[str] = []
    for raw in values:
        key = str(raw or "").strip().casefold()
        if key not in allowed_set or key in seen:
            continue
        seen.append(key)
    return tuple(seen)


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
            SOCIAL_TARGET_PACKAGES[item]
            for item in self.social_targets
            if item in SOCIAL_TARGET_PACKAGES
        )

    @property
    def includes_social(self) -> bool:
        return self.scope != AnalysisScope.DEVICE and bool(self.social_packages)

    @property
    def includes_recovery(self) -> bool:
        return self.scope != AnalysisScope.SOCIAL and "recovery" in self.device_sources

    @property
    def includes_gallery(self) -> bool:
        return self.scope != AnalysisScope.SOCIAL and "gallery" in self.device_sources

    @property
    def includes_documents(self) -> bool:
        return self.scope != AnalysisScope.SOCIAL and "documents" in self.device_sources

    @property
    def includes_contacts(self) -> bool:
        return self.scope != AnalysisScope.SOCIAL and "contacts" in self.device_sources

    @property
    def includes_sms(self) -> bool:
        return self.scope != AnalysisScope.SOCIAL and "sms" in self.device_sources

    def inventory_adapters(self) -> frozenset[str]:
        adapters: set[str] = set()
        if self.scope != AnalysisScope.SOCIAL:
            for source in self.device_sources:
                adapters.update(DEVICE_SOURCE_ADAPTERS.get(source, ()))
        if self.includes_social:
            adapters.update(SOCIAL_ADAPTERS)
        return frozenset(adapters)

    def allows_file_source(self, source: str | None) -> bool:
        key = str(source or "").casefold()
        if key in {"visible_ui", "accessibility_visible_ui", "social"}:
            return self.includes_social
        if key in {"email", "gmail"}:
            return self.scope != AnalysisScope.SOCIAL
        device_id = _FILE_SOURCE_TO_DEVICE.get(key)
        if device_id is None:
            return True
        if self.scope == AnalysisScope.SOCIAL:
            return False
        return device_id in self.device_sources

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
    if isinstance(scope, AnalysisScope):
        parsed_scope = scope
    else:
        parsed_scope = AnalysisScope(
            str(scope or AnalysisScope.COMBINED.value).strip().casefold()
        )
    devices = _unique(device_sources or (), DEVICE_SOURCE_IDS)
    socials = _unique(social_targets or (), SOCIAL_TARGET_IDS)
    if parsed_scope == AnalysisScope.DEVICE:
        socials = ()
        if not devices:
            devices = DEVICE_SOURCE_IDS
        if not devices:
            raise ValueError("Pilih minimal satu sumber HP.")
    elif parsed_scope == AnalysisScope.SOCIAL:
        devices = ()
        if not socials:
            socials = SOCIAL_TARGET_IDS
        if not socials:
            raise ValueError("Pilih minimal satu akun sosmed.")
    else:
        if not devices:
            devices = DEVICE_SOURCE_IDS
        if not socials:
            socials = SOCIAL_TARGET_IDS
        if not devices:
            raise ValueError("Gabungan membutuhkan minimal satu sumber HP.")
        if not socials:
            raise ValueError("Gabungan membutuhkan minimal satu akun sosmed.")
    return AnalysisPlan(scope=parsed_scope, device_sources=devices, social_targets=socials)


def analysis_plan_from_progress(progress: Mapping[str, Any] | None) -> AnalysisPlan:
    if not isinstance(progress, Mapping):
        return default_analysis_plan()
    try:
        return build_analysis_plan(
            scope=progress.get("analysis_scope"),
            device_sources=progress.get("device_sources")
            if isinstance(progress.get("device_sources"), list)
            else None,
            social_targets=progress.get("social_targets")
            if isinstance(progress.get("social_targets"), list)
            else None,
        )
    except (TypeError, ValueError):
        return default_analysis_plan()
