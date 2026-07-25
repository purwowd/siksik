from __future__ import annotations

import json
import logging
import re
import shutil
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.acquisition.agent_client import InventoryRecordV1, VisibleUiMetadataV1
from app.core.config import settings

logger = logging.getLogger("siksik.acquisition.social_ocr")

INSTAGRAM_PACKAGE = "com.instagram.android"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
ACCOUNT_MARKER = re.compile(r"^[A-Za-z0-9._]{2,30}$")
NUMERIC_MARKER = re.compile(r"^[0-9._]+$")
PROFILE_NOISE = {
    "add",
    "archive",
    "arsip",
    "create",
    "curious",
    "curious_",
    "followers",
    "following",
    "for",
    "inspo",
    "instagram",
    "just",
    "needed",
    "open",
    "posts",
    "profile",
    "ready",
    "reels",
    "spotify",
    "today",
    "todays",
    "vibe",
    "vibe_",
}
# EasyOCR often reads avatar prompts ("Just curious…", "Today's vibe…") as usernames.
IG_AVATAR_PROMPT_FRAGMENTS = (
    "curious",
    "inspo",
    "needed",
    "ready for",
    "today",
    "vibe",
)
DOMAINISH_USERNAME_SUFFIXES = {
    "app",
    "blog",
    "com",
    "id",
    "instagram",
    "io",
    "link",
    "me",
    "net",
    "org",
    "spotify",
    "twitter",
}
USERNAME_RESOURCES = (
    "action_bar_title",
    "profile_header_username",
    "profile_header_user_name",
    "action_bar_large_title_auto_size",
)
METRIC_RESOURCES = {
    "posts": (
        "profile_header_familiar_post_count_value",
        "profile_header_post_count_front_familiar",
        "profile_header_post_count",
    ),
    "followers": (
        "profile_header_familiar_followers_value",
        "profile_header_followers_stacked_familiar",
        "profile_header_followers_value",
    ),
    "following": (
        "profile_header_familiar_following_value",
        "profile_header_following_stacked_familiar",
        "profile_header_following_value",
    ),
}
METRIC_LABELS = {
    "posts": ("posts", "postingan", "kiriman", "tweets", "tweet"),
    "followers": ("followers", "pengikut"),
    "following": ("following", "mengikuti", "diikuti"),
}
PROFILE_LINK = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>{}\[\]\"']+|"
    r"(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\.)"
    r"(?:com|net|org|id|co|me|io|app|link|bio|blog)(?:/[^\s<>{}\[\]\"']*)?"
)


class ScreenshotArtifact(Protocol):
    artifact_id: str
    record_id: str
    role: str
    attachment_id: str | None


@dataclass(frozen=True)
class SocialSnapshotEnrichment:
    record_id: str
    source_app: str
    social_scope: str
    artifact_ids: tuple[str, ...]
    debug_paths: tuple[str, ...]
    ocr_text: str | None
    ocr_backend: str | None
    ocr_confidence: float | None
    metadata: dict[str, object]


def build_social_snapshot_enrichments(
    *,
    session_id: str,
    crawl_id: str,
    records: dict[str, tuple[InventoryRecordV1, str]],
    artifacts: Sequence[ScreenshotArtifact],
    local_paths: dict[str, Path],
) -> list[SocialSnapshotEnrichment]:
    screenshots: dict[str, list[ScreenshotArtifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.role == "screenshot":
            screenshots[artifact.record_id].append(artifact)

    backend = _host_ocr_backend() if settings.android_social_host_ocr_enabled else None
    output: list[SocialSnapshotEnrichment] = []
    for record_id, values in screenshots.items():
        record_pair = records.get(record_id)
        if record_pair is None:
            continue
        record = record_pair[0]
        metadata = record.metadata
        if (
            record.source_kind != "visible_ui"
            or record.source_app != INSTAGRAM_PACKAGE
            or not isinstance(metadata, VisibleUiMetadataV1)
        ):
            continue

        debug_paths: list[str] = []
        ocr_texts: list[str] = []
        confidences: list[float] = []
        backend_names: list[str] = []
        ocr_regions: list[dict[str, object]] = []
        artifact_ids: list[str] = []
        for index, artifact in enumerate(values, start=1):
            source = local_paths.get(artifact.artifact_id)
            if source is None or not source.is_file():
                continue
            artifact_ids.append(artifact.artifact_id)
            debug_path = _mirror_debug_snapshot(
                source,
                session_id=session_id,
                crawl_id=crawl_id,
                social_scope=metadata.social_scope,
                record_id=record_id,
                artifact_id=artifact.artifact_id,
                index=index,
            )
            if debug_path is not None:
                debug_paths.append(str(debug_path))
            if backend is None:
                continue
            try:
                result = run_social_snapshot_ocr(source, backend)
            except Exception as exc:
                logger.warning(
                    "social_snapshot_ocr_failed",
                    extra={
                        "session_id": session_id,
                        "crawl_id": crawl_id,
                        "record_id": record_id,
                        "error_category": type(exc).__name__,
                    },
                )
                continue
            if result is None:
                continue
            if result.text.strip():
                ocr_texts.append(result.text.strip())
            ocr_regions.extend(
                {
                    "text": region.text,
                    "left": region.left,
                    "top": region.top,
                    "right": region.right,
                    "bottom": region.bottom,
                    "confidence": region.confidence,
                }
                for region in result.regions
            )
            backend_names.append(result.backend)
            if result.confidence is not None:
                confidences.append(float(result.confidence))

        combined_ocr = _merge_text(ocr_texts)
        profile_metadata = _profile_metadata(metadata, combined_ocr, ocr_regions)
        output.append(
            SocialSnapshotEnrichment(
                record_id=record_id,
                source_app=record.source_app,
                social_scope=metadata.social_scope,
                artifact_ids=tuple(artifact_ids),
                debug_paths=tuple(debug_paths),
                ocr_text=combined_ocr or None,
                ocr_backend=",".join(dict.fromkeys(backend_names)) or None,
                ocr_confidence=(
                    sum(confidences) / len(confidences) if confidences else None
                ),
                metadata=profile_metadata,
            )
        )
        logger.info(
            "social_snapshot_enriched",
            extra={
                "session_id": session_id,
                "crawl_id": crawl_id,
                "record_id": record_id,
                "social_scope": metadata.social_scope,
                "screenshot_count": len(artifact_ids),
                "debug_snapshot_count": len(debug_paths),
                "ocr_backend": ",".join(dict.fromkeys(backend_names)) or None,
                "ocr_characters": len(combined_ocr),
            },
        )
    return output


def enrichment_row(
    session_id: str,
    crawl_id: str,
    value: SocialSnapshotEnrichment,
    created_at: str,
) -> tuple[object, ...]:
    return (
        crawl_id,
        value.record_id,
        session_id,
        value.source_app,
        value.social_scope,
        json.dumps(value.artifact_ids, separators=(",", ":")),
        json.dumps(value.debug_paths, separators=(",", ":")),
        value.ocr_text,
        value.ocr_backend,
        value.ocr_confidence,
        json.dumps(value.metadata, ensure_ascii=False, separators=(",", ":")),
        created_at,
    )


def _host_ocr_backend():
    from app.services import ocr as ocr_service

    # Always share one Reader with analysis OCR — constructing a second EasyOCR
    # Reader roughly doubles RAM and was a factor in Mac lab OOMs.
    preferred = (settings.ocr_backend or "easyocr").strip().lower()
    for name in (preferred, "easyocr", "tesseract"):
        backend = ocr_service.get_shared_backend(name)
        if backend is not None:
            if name != preferred:
                logger.info("social_snapshot_ocr_backend_fallback preferred=%s using=%s", preferred, name)
            return backend
    logger.warning("social_snapshot_ocr_backend_unavailable")
    return None

def run_social_snapshot_ocr(image_path: Path, backend):
    from app.services.ocr import run_ocr

    return run_ocr(
        image_path,
        backend=backend,
        max_edge_px=settings.android_social_ocr_max_edge_px,
        min_edge_px=0,
        sharpen=False,
        mag_ratio=settings.android_social_ocr_mag_ratio,
    )


def _mirror_debug_snapshot(
    source: Path,
    *,
    session_id: str,
    crawl_id: str,
    social_scope: str,
    record_id: str,
    artifact_id: str,
    index: int,
) -> Path | None:
    if not settings.android_social_debug_snapshots:
        return None
    if not all(SAFE_ID.fullmatch(value) for value in (session_id, crawl_id, record_id, artifact_id)):
        return None
    root = settings.android_social_debug_dir.expanduser().resolve()
    target_dir = root / session_id / crawl_id
    filename = f"instagram__{social_scope}__{index:02d}__{record_id}__{artifact_id}.png"
    target = (target_dir / filename).resolve()
    if not target.is_relative_to(root):
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".png.part")
        shutil.copyfile(source, temporary)
        temporary.replace(target)
        return target
    except OSError as exc:
        logger.warning(
            "social_snapshot_debug_mirror_failed",
            extra={
                "session_id": session_id,
                "crawl_id": crawl_id,
                "record_id": record_id,
                "error_category": type(exc).__name__,
            },
        )
        return None


def _profile_metadata(
    metadata: VisibleUiMetadataV1,
    ocr_text: str,
    ocr_regions: list[dict[str, object]],
) -> dict[str, object]:
    if metadata.social_scope != "own_profile":
        return {}
    nodes = [value.model_dump(mode="json") for value in metadata.nodes]
    username = (
        _username_from_nodes(nodes)
        or _username_from_ocr(ocr_text)
        or _username_from_regions(ocr_regions)
    )
    if username is None:
        username = _valid_username(metadata.profile_username)

    existing_metrics = (
        metadata.profile_metrics.model_dump(mode="json")
        if metadata.profile_metrics is not None
        else {}
    )
    metrics: dict[str, int | None] = {}
    for name in ("posts", "followers", "following"):
        candidates = (
            _metric_from_nodes(nodes, name),
            _metric_from_regions(ocr_regions, name),
            _metric_from_text(ocr_text, name),
            _valid_count(existing_metrics.get(name)),
        )
        metrics[name] = next((value for value in candidates if value is not None), None)

    # EasyOCR often drops a lone "0" posts digit; empty-state text is authoritative.
    if _instagram_empty_posts_signal(ocr_text, ocr_regions, nodes):
        metrics["posts"] = 0

    links = list(metadata.profile_links)
    repaired = _repair_ocr_link_text(ocr_text)
    links.extend(match.group(0) for match in PROFILE_LINK.finditer(repaired))
    # Also join adjacent OCR region scraps that form a path continuation.
    region_blob = " ".join(
        str(region.get("text") or "")
        for region in ocr_regions
        if isinstance(region, dict)
    )
    if region_blob:
        links.extend(
            match.group(0)
            for match in PROFILE_LINK.finditer(_repair_ocr_link_text(region_blob))
        )
    profile_links: list[str] = []
    seen: set[str] = set()
    for value in links:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().rstrip(".,;)]}")
        cleaned = re.sub(r"[_\u2026.]+$", "", cleaned).strip()
        key = cleaned.casefold()
        if len(cleaned) >= 4 and key not in seen:
            seen.add(key)
            profile_links.append(cleaned[:2048])
        if len(profile_links) >= 16:
            break
    return {
        "profile_username": username,
        "profile_metrics": metrics,
        "profile_links": profile_links,
    }


def _repair_ocr_link_text(value: str) -> str:
    """Repair EasyOCR spacing on profile links (open spotify com/user/31 abcd)."""
    text = value
    text = re.sub(r"(?i)\bopen\s+spotify\s+com/", "open.spotify.com/", text)
    text = re.sub(r"(?i)\bwww\s+([a-z0-9-]+)\s+com/", r"www.\1.com/", text)
    text = re.sub(r"(?i)\b([a-z0-9-]+)\s+com/(user|in|p)/", r"\1.com/\2/", text)
    stop = PROFILE_NOISE | {
        "banners",
        "edit",
        "share",
        "sunting",
        "bagikan",
        "follow",
        "following",
        "followers",
        "posts",
        "postingan",
    }

    def _join_path(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        if right.casefold() in stop or not re.fullmatch(r"[A-Za-z0-9._%-]{4,64}", right):
            return match.group(0)
        return left + right

    for _ in range(4):
        updated = re.sub(
            r"(?i)((?:https?://|www\.)?[a-z0-9.-]+\."
            r"(?:com|net|org|id|co|me|io|app|link|bio|blog)"
            r"/(?:user|in|p|status|reel)/[A-Za-z0-9._%-]*)\s+([A-Za-z0-9._%-]+)",
            _join_path,
            text,
        )
        if updated == text:
            break
        text = updated
    return text


def _username_from_nodes(nodes: list[dict[str, object]]) -> str | None:
    for resource in USERNAME_RESOURCES:
        for node in nodes:
            view_id = str(node.get("view_id") or "").casefold()
            if resource not in view_id:
                continue
            for key in ("text", "content_description"):
                username = _valid_username(node.get(key))
                if username:
                    return username
    return None


def _username_from_ocr(value: str) -> str | None:
    if not value:
        return None
    candidates: list[str] = []
    # Prefer handles that still contain a dot (intel.negara) before prompt noise.
    for match in re.finditer(
        r"(?i)(?<![A-Za-z0-9._])([A-Za-z0-9._]*\.[A-Za-z0-9._]+)(?![A-Za-z0-9._])",
        value,
    ):
        username = _valid_username(match.group(1))
        if username:
            candidates.append(username)
    # EasyOCR often drops the dot: "intel negara" → intel.negara
    for match in re.finditer(
        r"(?i)(?<![A-Za-z0-9._])([A-Za-z0-9_]{2,15})\s+([A-Za-z0-9_]{2,15})(?![A-Za-z0-9._])",
        value,
    ):
        left, right = match.group(1), match.group(2)
        if left.casefold() in PROFILE_NOISE or right.casefold() in PROFILE_NOISE:
            continue
        username = _valid_username(f"{left}.{right}")
        if username:
            candidates.append(username)
    label = r"(?:posts|postingan|kiriman|tweets|tweet)"
    patterns = (
        re.compile(
            rf"(?i)(?<![A-Za-z0-9._])([A-Za-z0-9._]{{2,30}})\s+"
            rf"(?:[0-9][0-9.,]*\s*)?{label}\b"
        ),
        re.compile(r"(?i)(?<![A-Za-z0-9._])@([A-Za-z0-9._]{2,30})"),
    )
    for pattern in patterns:
        for match in pattern.finditer(value):
            username = _valid_username(match.group(1))
            if username:
                candidates.append(username)
    return _pick_best_username(candidates)


def _username_from_regions(regions: list[dict[str, object]]) -> str | None:
    candidates: list[str] = []
    scored: list[tuple[int, int, str]] = []
    for region in regions:
        username = _valid_username(region.get("text"))
        top = region.get("top")
        left = region.get("left")
        if username and isinstance(top, int) and isinstance(left, int):
            candidates.append(username)
            scored.append((top, left, username))
    dotted = _pick_best_username([value for value in candidates if "." in value])
    if dotted:
        return dotted
    if not scored:
        return None
    return min(scored, key=lambda value: (value[0], value[1]))[2]


def _pick_best_username(candidates: list[str]) -> str | None:
    valid = [value for value in candidates if _valid_username(value)]
    if not valid:
        return None
    dotted = [value for value in valid if "." in value]
    pool = dotted or valid
    return max(pool, key=lambda value: (len(value), value.count("."), value))


def _valid_username(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().removeprefix("@").strip()
    key = candidate.casefold().rstrip("_")
    if candidate.casefold() in {"null", "undefined", "none", "nil"}:
        return None
    if (
        not ACCOUNT_MARKER.fullmatch(candidate)
        or NUMERIC_MARKER.fullmatch(candidate)
        or candidate.casefold() in PROFILE_NOISE
        or key in PROFILE_NOISE
        or any(fragment in key for fragment in IG_AVATAR_PROMPT_FRAGMENTS)
        or (
            "." in candidate
            and candidate.rsplit(".", 1)[-1].casefold() in DOMAINISH_USERNAME_SUFFIXES
        )
        or PROFILE_LINK.fullmatch(candidate) is not None
        or candidate.endswith(".")
        or ".." in candidate
    ):
        return None
    return candidate


def _metric_from_nodes(nodes: list[dict[str, object]], name: str) -> int | None:
    for node in nodes:
        view_id = str(node.get("view_id") or "").casefold()
        if not any(resource in view_id for resource in METRIC_RESOURCES[name]):
            continue
        for key in ("text", "content_description"):
            count = _parse_count(node.get(key))
            if count is None:
                count = _metric_from_text(str(node.get(key) or ""), name)
            if count is not None:
                return count
    return None


def _metric_from_text(value: str, name: str) -> int | None:
    for label in METRIC_LABELS[name]:
        escaped = re.escape(label)
        for pattern in (
            re.compile(
                rf"(?i)(?<![A-Za-z0-9])([0-9][0-9.,]*\s*(?:k|m|b|rb|jt)?)\s*{escaped}\b"
            ),
            re.compile(
                rf"(?i)\b{escaped}\s*([0-9][0-9.,]*\s*(?:k|m|b|rb|jt)?)\b"
            ),
        ):
            match = pattern.search(value)
            if match:
                count = _parse_count(match.group(1))
                if count is not None:
                    return count
    return None


def _metric_from_regions(
    regions: list[dict[str, object]],
    name: str,
) -> int | None:
    for region in regions:
        inline = _metric_from_text(str(region.get("text") or ""), name)
        if inline is not None:
            return inline

    labels = {
        value.casefold()
        for value in METRIC_LABELS[name]
    }
    label_regions = [
        region
        for region in regions
        if str(region.get("text") or "").strip().casefold() in labels
    ]
    count_regions = [
        (region, count)
        for region in regions
        if (count := _parse_count(region.get("text"))) is not None
    ]
    for label in label_regions:
        label_center_x = _region_center(label, "left", "right")
        label_center_y = _region_center(label, "top", "bottom")
        if label_center_x is None or label_center_y is None:
            continue
        ranked: list[tuple[int, int]] = []
        for candidate, count in count_regions:
            center_x = _region_center(candidate, "left", "right")
            center_y = _region_center(candidate, "top", "bottom")
            if center_x is None or center_y is None:
                continue
            horizontal = abs(center_x - label_center_x)
            vertical = abs(center_y - label_center_y)
            if horizontal > 180 or vertical > 240:
                continue
            below_penalty = 1_000 if center_y > label_center_y else 0
            ranked.append((horizontal * 3 + vertical + below_penalty, count))
        if ranked:
            return min(ranked, key=lambda value: value[0])[1]
    return None


def _instagram_empty_posts_signal(
    ocr_text: str,
    ocr_regions: list[dict[str, object]],
    nodes: list[dict[str, object]],
) -> bool:
    phrases = (
        "create your first post",
        "buat postingan pertama",
        "share your point of view",
        "share photos and videos",
    )
    haystacks: list[str] = []
    if ocr_text:
        haystacks.append(ocr_text)
    for region in ocr_regions:
        text = region.get("text")
        if isinstance(text, str) and text.strip():
            haystacks.append(text)
    for node in nodes:
        for key in ("text", "content_description"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                haystacks.append(value)
    blob = "\n".join(haystacks).casefold()
    return any(phrase in blob for phrase in phrases)


def _region_center(
    region: dict[str, object],
    start_name: str,
    end_name: str,
) -> int | None:
    start = region.get(start_name)
    end = region.get(end_name)
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return start + (end - start) // 2


def _parse_count(value: object) -> int | None:
    if not isinstance(value, str):
        return _valid_count(value)
    match = re.fullmatch(
        r"\s*([0-9]+(?:[.,][0-9]+)?|[0-9][0-9., ]*)\s*(k|m|b|rb|jt)?\s*",
        value.casefold(),
    )
    if not match:
        return None
    number, suffix = match.groups()
    if not suffix:
        return _valid_count(int(re.sub(r"[.,\s]", "", number)))
    try:
        parsed = float(number.replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    multiplier = {
        "k": 1_000,
        "rb": 1_000,
        "m": 1_000_000,
        "jt": 1_000_000,
        "b": 1_000_000_000,
    }[suffix]
    return _valid_count(int(parsed * multiplier))


def _valid_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _merge_text(values: Sequence[str]) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"[\t\r\n ]+", " ", value).strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            output.append(normalized)
    return "\n".join(output)[:32768]
