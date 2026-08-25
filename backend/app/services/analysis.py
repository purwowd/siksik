from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, Layer, ReviewStatus, SessionStatus
from app.services.acquisition import IMG_EXT, TEXT_EXT, VID_EXT
from app.services import vision as vis

CANONICAL_CRAWL_RECORD_MIME = "application/vnd.siksik.crawl-record+json"


@dataclass(frozen=True)
class ContentAnalysisResult:
    findings: tuple[dict[str, Any], ...]
    cacheable: bool


def _skip_heavy_ocr_for_gallery(
    path: Path,
    source: str,
    origin_hint: str | None = None,
) -> bool:
    """QUICK camera-roll: skip EasyOCR unless screenshot/chat/dokumen/edge."""
    if source not in {"gallery", "media_image", "media_video"}:
        return False
    from app.services.hash_cache import get_analysis_mode
    from app.services import media_text

    if get_analysis_mode() != AcquisitionMode.QUICK:
        return False
    return not media_text.should_try_ocr(path, origin_hint=origin_hint)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def analyze_text_l1_l2(text: str, keywords: list[str]) -> list[dict]:
    from app.services.lexicon import category_for_keyword, match_keywords, normalize_text

    norm = normalize_text(text)
    if not norm:
        return []
    findings: list[dict] = []
    for kw in match_keywords(text, keywords):
        boost = (
            0.08
            if any(x in norm for x in ("grup", "rahasia", "rencana", "segera", "malam ini"))
            else 0.0
        )
        conf = min(0.99, 0.72 + len(kw) * 0.01 + boost)
        findings.append(
            {
                "category": category_for_keyword(kw),
                "label": f"Indikasi: {kw}",
                "confidence": round(conf, 3),
                "layer_origin": Layer.L2.value if boost else Layer.L1.value,
                "evidence": text[:320],
            }
        )
    return findings


def analyze_path_signals(path: str, keywords: list[str]) -> list[dict]:
    """Filename / path keyword scan — useful for media without OCR."""
    from app.services.lexicon import category_for_keyword, match_keywords

    norm_path = re.sub(r"[^a-z0-9]+", " ", path.lower()).strip()
    findings: list[dict] = []
    for matched in match_keywords(norm_path, keywords):
        findings.append(
            {
                "category": category_for_keyword(matched),
                "label": f"Nama file/path: {matched}",
                "confidence": 0.7 if matched in keywords else 0.68,
                "layer_origin": Layer.L1.value,
                "evidence": path[:320],
            }
        )
    return findings


def analyze_image_meta_l3(raw: str) -> list[dict]:
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not meta.get("risk"):
        return []
    tags = meta.get("tags") or []
    tag = tags[0] if tags else "simbol_mencurigakan"
    return [
        {
            "category": "konten_visual",
            "label": f"CV flag: {tag}",
            "confidence": 0.81,
            "layer_origin": Layer.L3.value,
            "evidence": json.dumps(meta)[:320],
        }
    ]


def analyze_video_meta_l4(raw: str) -> list[dict]:
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not meta.get("risk"):
        return []
    tags = meta.get("tags") or []
    tag = tags[0] if tags else "keyframe_berisiko"
    return [
        {
            "category": "konten_visual",
            "label": f"Video keyframe: {tag}",
            "confidence": 0.78,
            "layer_origin": Layer.L4.value,
            "evidence": json.dumps(meta)[:320],
        }
    ]


def _is_probably_text(path: Path, mime: str) -> bool:
    ext = path.suffix.lower()
    if ext in TEXT_EXT or ext in {".txt", ".log", ".json", ".xml", ".html", ".csv"}:
        return True
    if mime.startswith("text/"):
        return True
    return False


async def read_preview(path: Path, mime: str, max_bytes: int = 200_000) -> str:
    """Baca cuplikan teks. Binary media (gambar/video/pdf) → kosong (hindari FP keyword di noise byte)."""
    ext = path.suffix.lower()
    if mime == CANONICAL_CRAWL_RECORD_MIME:

        def _read_crawl_record() -> str:
            from app.acquisition.agent_client import InventoryRecordV1

            try:
                record = InventoryRecordV1.model_validate_json(path.read_bytes())
            except (OSError, ValueError):
                return ""
            return (record.normalized_text or "")[:max_bytes]

        return await asyncio.to_thread(_read_crawl_record)
    if ext in {".imgmeta", ".vidmeta"}:

        def _read_meta() -> str:
            try:
                return path.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
            except OSError:
                return ""

        return await asyncio.to_thread(_read_meta)
    if ext in IMG_EXT or ext in VID_EXT or mime.startswith("image/") or mime.startswith("video/"):
        return ""
    if ext in {".pdf", ".doc", ".docx", ".rtf", ".odt", ".zip", ".rar", ".7z"}:
        return ""
    if _is_probably_text(path, mime):

        def _read() -> str:
            try:
                return path.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
            except OSError:
                return ""

        return await asyncio.to_thread(_read)

    # Binary lain (jarang): jangan decode head — noise sering match token pendek (bom, dll.)
    return ""


from app.services.hash_cache import (
    get_cached,
    reset_analysis_mode,
    set_analysis_mode,
    set_cached,
)
from app.services.inference_guard import run_guarded


def analyze_content_result(
    path: Path,
    mime: str,
    source: str,
    text: str,
    keywords: list[str],
    *,
    precomputed_ocr_text: str | None = None,
    precomputed_ocr_backend: str | None = None,
    origin_hint: str | None = None,
) -> ContentAnalysisResult:
    ext = path.suffix.lower()
    if mime == CANONICAL_CRAWL_RECORD_MIME:
        from app.services import content_policy

        findings = analyze_text_l1_l2(text, keywords) if text.strip() else []
        if settings.content_detection_enabled and text.strip():
            findings.extend(
                content_policy.findings_from_text(
                    text,
                    backend="canonical_text",
                    layer=Layer.L2.value,
                    image_context=False,
                )
            )
        if precomputed_ocr_text and precomputed_ocr_text.strip():
            from app.services import ocr as ocr_mod

            findings.extend(
                ocr_mod.ocr_findings_from_text(
                    precomputed_ocr_text,
                    backend=precomputed_ocr_backend or "host_ocr",
                )
            )
            if precomputed_ocr_text.strip() not in (text or ""):
                findings.extend(
                    analyze_text_l1_l2(precomputed_ocr_text, keywords)
                )
        combined_content_text = "\n".join(
            value
            for value in (text.strip(), (precomputed_ocr_text or "").strip())
            if value
        )
        if (
            settings.content_detection_enabled
            and settings.gpu_stack_enabled
            and settings.gpu_qwen_enabled
            and content_policy.should_adjudicate_text(combined_content_text)
        ):
            from app.services.gpu_stack import reason_qwen

            findings.extend(
                hit.as_finding()
                for hit in reason_qwen.moderate_text(combined_content_text)
            )
        findings = content_policy.merge_content_findings(findings)
        seen: set[str] = set()
        uniq: list[dict] = []
        for f in findings:
            key = f"{f['label']}|{f['evidence'][:80]}"
            if key not in seen:
                seen.add(key)
                uniq.append(f)
        return ContentAnalysisResult(tuple(uniq), True)
    findings: list[dict] = []
    cacheable = True
    findings.extend(analyze_path_signals(str(path), keywords))

    is_image = ext in IMG_EXT or mime.startswith("image/") or (
        source == "gallery" and mime.startswith("image/")
    )
    is_video = ext in VID_EXT or source == "video" or mime.startswith("video/")

    # Independent visual flag: run before the OCR/vision branches so QUICK gallery
    # and social screenshots (which intentionally bypass heavy OCR) are covered too.
    if is_image and ext != ".imgmeta":
        from app.services import nudity

        outcome = nudity.analyze_image_result(path)
        findings.extend(outcome.findings)
        cacheable = cacheable and outcome.cacheable
    elif is_video and ext != ".vidmeta":
        from app.services import nudity

        outcome = nudity.analyze_video_result(path)
        findings.extend(outcome.findings)
        cacheable = cacheable and outcome.cacheable

    if ext == ".imgmeta":
        findings.extend(analyze_image_meta_l3(text))
    elif is_image:
        # Social UI screenshots already have structured inventory records.
        # Running EasyOCR/media_text here hangs the pipeline (looks stuck at INDEXING).
        if source in {"visible_ui", "accessibility_visible_ui"}:
            findings.extend(
                vis.analyze_lightweight_image_file(
                    path,
                    precomputed_ocr_text=precomputed_ocr_text,
                    precomputed_ocr_backend=precomputed_ocr_backend,
                    include_reasoning=True,
                )
            )
        elif _skip_heavy_ocr_for_gallery(path, source, origin_hint):
            # iOS AFC dumps raw camera HEIC; Android already samples a few media.
            # QUICK: PIL/path signals only — EasyOCR on every HEIC is why iOS lags.
            findings.extend(
                vis.analyze_lightweight_image_file(
                    path,
                    precomputed_ocr_text=precomputed_ocr_text,
                    precomputed_ocr_backend=precomputed_ocr_backend,
                )
            )
        else:
            findings.extend(
                vis.analyze_image_file(
                    path,
                    precomputed_ocr_text=precomputed_ocr_text,
                    precomputed_ocr_backend=precomputed_ocr_backend,
                    origin_hint=origin_hint,
                )
            )
    elif ext == ".vidmeta":
        findings.extend(analyze_video_meta_l4(text))
    elif is_video:
        findings.extend(vis.analyze_video_file(path))
    elif _is_probably_text(path, mime) and text.strip():
        findings.extend(analyze_text_l1_l2(text, keywords))
        if settings.content_detection_enabled:
            from app.services import content_policy

            findings.extend(
                content_policy.findings_from_text(
                    text,
                    backend="text",
                    layer=Layer.L2.value,
                    image_context=False,
                )
            )
            if (
                settings.gpu_stack_enabled
                and settings.gpu_qwen_enabled
                and content_policy.should_adjudicate_text(text)
            ):
                from app.services.gpu_stack import reason_qwen

                findings.extend(
                    hit.as_finding() for hit in reason_qwen.moderate_text(text)
                )
    # pdf/docx/binaries lain: path signals saja sampai ada extractor khusus

    from app.services import content_policy

    findings = content_policy.merge_content_findings(findings)
    # de-dupe by label+evidence prefix
    seen: set[str] = set()
    uniq: list[dict] = []
    for f in findings:
        key = f"{f['label']}|{f['evidence'][:80]}"
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return ContentAnalysisResult(tuple(uniq), cacheable)


def analyze_content(
    path: Path,
    mime: str,
    source: str,
    text: str,
    keywords: list[str],
    *,
    precomputed_ocr_text: str | None = None,
    precomputed_ocr_backend: str | None = None,
    origin_hint: str | None = None,
) -> list[dict]:
    """Compatibility wrapper for callers that only need findings."""
    return list(
        analyze_content_result(
            path,
            mime,
            source,
            text,
            keywords,
            precomputed_ocr_text=precomputed_ocr_text,
            precomputed_ocr_backend=precomputed_ocr_backend,
            origin_hint=origin_hint,
        ).findings
    )


async def analyze_session(
    session_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress,
    *,
    progress_status: SessionStatus = SessionStatus.ANALYZING,
    progress_start: float = 60.0,
    progress_end: float = 98.0,
    progress_label: str = "Analisis",
) -> tuple[int, int, float, dict]:
    t0 = time.perf_counter()
    mode_token = set_analysis_mode(mode)
    try:
        return await _analyze_session_body(
            session_id,
            staging,
            mode,
            on_progress,
            t0,
            progress_status,
            progress_start,
            progress_end,
            progress_label,
        )
    finally:
        reset_analysis_mode(mode_token)


async def _analyze_session_body(
    session_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress,
    t0: float,
    progress_status: SessionStatus,
    progress_start: float,
    progress_end: float,
    progress_label: str,
) -> tuple[int, int, float, dict]:
    rows = await db.fetchall(
        "SELECT id, source, path, sha256, mime, meta_json FROM files "
        "WHERE session_id = ? AND analyzed = 0",
        (session_id,),
    )
    file_count_row = await db.fetchone(
        "SELECT COUNT(*) AS total, COALESCE(SUM(analyzed), 0) AS analyzed "
        "FROM files WHERE session_id = ?",
        (session_id,),
    )
    file_count = int(file_count_row["total"]) if file_count_row else 0
    previously_analyzed = int(file_count_row["analyzed"]) if file_count_row else 0
    finding_count_row = await db.fetchone(
        "SELECT COUNT(*) AS total FROM findings WHERE session_id = ?",
        (session_id,),
    )
    findings_count = int(finding_count_row["total"]) if finding_count_row else 0

    # Light inventory/text first (SMS/contacts/JSON), then images, then video.
    # iOS used to OCR all HEIC gallery first → UI stuck at "8/106" for minutes.
    def _weight(row) -> tuple[int, str]:
        source = str(row["source"] or "")
        path = Path(str(row["path"] or ""))
        mime = str(row["mime"] or "")
        ext = path.suffix.lower()
        if (
            mime == CANONICAL_CRAWL_RECORD_MIME
            or path.name.endswith(".siksik-record.json")
            or (ext == ".json" and source in {"sms", "contacts", "contact", "visible_ui"})
            or source in {"sms", "contacts", "contact"}
        ):
            return (0, str(row["path"]))
        if source == "video" or ext in VID_EXT or mime.startswith("video/"):
            return (3, str(row["path"]))
        if (
            source in {"gallery", "media_image", "visible_ui", "accessibility_visible_ui"}
            or ext in IMG_EXT
            or mime.startswith("image/")
        ):
            return (2, str(row["path"]))
        return (1, str(row["path"]))

    ordered = sorted(rows, key=_weight)

    image_cap = settings.image_cap_quick if mode == AcquisitionMode.QUICK else settings.image_cap_full
    gallery_seen = 0
    video_seen = 0
    selected = []
    for r in ordered:
        if r["source"] == "gallery":
            gallery_seen += 1
            if image_cap > 0 and gallery_seen > image_cap:
                continue
        if r["source"] == "video" or Path(r["path"]).suffix.lower() in VID_EXT:
            video_seen += 1
            video_cap = (
                settings.video_cap_quick
                if mode == AcquisitionMode.QUICK
                else settings.video_cap_full
            )
            if video_cap > 0 and video_seen > video_cap:
                continue
        selected.append(r)

    total = len(selected)
    sem = asyncio.Semaphore(settings.worker_concurrency)
    keywords = settings.risk_keywords
    layer_counts = {"L1": 0, "L2": 0, "L3": 0, "L4": 0}
    category_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for row in await db.fetchall(
        "SELECT layer_origin, COUNT(*) AS total FROM findings "
        "WHERE session_id = ? GROUP BY layer_origin",
        (session_id,),
    ):
        layer_counts[str(row["layer_origin"])] = int(row["total"])
    for row in await db.fetchall(
        "SELECT category, COUNT(*) AS total FROM findings "
        "WHERE session_id = ? GROUP BY category",
        (session_id,),
    ):
        category_counts[str(row["category"])] = int(row["total"])
    for row in await db.fetchall(
        "SELECT source, COUNT(*) AS total FROM findings "
        "WHERE session_id = ? GROUP BY source",
        (session_id,),
    ):
        source_counts[str(row["source"])] = int(row["total"])
    existing_labels = await db.fetchall(
        "SELECT label FROM findings WHERE session_id = ?",
        (session_id,),
    )
    seen_finding_keys: set[tuple[str, str]] = {
        (str(row["ident"]), str(row["label"]))
        for row in await db.fetchall(
            """
            SELECT f.label AS label,
                   COALESCE(NULLIF(fi.sha256, ''), f.file_id) AS ident
            FROM findings f
            JOIN files fi ON fi.id = f.file_id
            WHERE f.session_id = ?
            """,
            (session_id,),
        )
    }
    hits_ocr = sum(
        1
        for row in existing_labels
        if "ocr" in str(row["label"]).lower() or "on-screen" in str(row["label"]).lower()
    )
    hits_asr = sum(
        1
        for row in existing_labels
        if any(
            value in str(row["label"]).lower()
            for value in ("audio", "lirik", "whisper")
        )
    )
    social_ocr_rows = await db.fetchall(
        "SELECT record_id, ocr_text, ocr_backend FROM social_snapshot_enrichments "
        "WHERE session_id = ? AND ocr_text IS NOT NULL",
        (session_id,),
    )
    social_ocr = {
        str(row["record_id"]): (str(row["ocr_text"]), str(row["ocr_backend"] or "host_ocr"))
        for row in social_ocr_rows
        if str(row["ocr_text"] or "").strip()
    }

    await on_progress(
        progress_status,
        progress_start,
        f"{progress_label} dimulai ({previously_analyzed}/{file_count})…",
        files_listed=file_count,
        files_pulled=file_count,
        files_indexed=file_count,
        files_analyzed=previously_analyzed,
        findings_count=findings_count,
    )

    def _count_media_kinds(label: str) -> None:
        nonlocal hits_ocr, hits_asr
        low = label.lower()
        if "ocr" in low or "on-screen" in low:
            hits_ocr += 1
        if "audio" in low or "lirik" in low or "whisper" in low:
            hits_asr += 1

    async def process(row) -> list[tuple]:
        async with sem:
            path = staging / row["path"]
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                meta = {}
            canonical_text = str(meta.get("canonical_normalized_text") or "").strip()
            cache_key = str(row["sha256"] or "")
            if (
                cache_key
                and meta.get("crawl_artifact_role") == "source_binary"
                and canonical_text
            ):
                # The same bytes can carry different MediaStore/source
                # metadata. Cache the actual analysis input, not only the
                # binary hash, or Qwen/OCR findings from canonical context can
                # be silently reused or omitted across records/sessions.
                cache_key = hashlib.sha256(
                    f"{cache_key}\0{canonical_text}".encode("utf-8")
                ).hexdigest()
            cached = await get_cached(cache_key) if cache_key else None
            precomputed = None
            record_id_for_ocr = None
            if meta.get("crawl_artifact_role") == "screenshot":
                record_id_for_ocr = str(meta.get("crawl_record_id") or "")
                precomputed = social_ocr.get(record_id_for_ocr) if record_id_for_ocr else None
            elif row["source"] in {"visible_ui", "accessibility_visible_ui"}:
                record_id_for_ocr = str(
                    meta.get("crawl_record_id")
                    or meta.get("record_id")
                    or Path(str(row["path"])).stem.split("__")[0]
                    or ""
                )
                if record_id_for_ocr.startswith("record_"):
                    precomputed = social_ocr.get(record_id_for_ocr)
                else:
                    path_name = Path(str(row["path"])).name
                    for rid, payload in social_ocr.items():
                        if rid and rid in path_name:
                            record_id_for_ocr = rid
                            precomputed = payload
                            break
            if cached is not None:
                results = cached
            else:
                text = await read_preview(path, row["mime"] or "")
                if (
                    meta.get("crawl_artifact_role") == "source_binary"
                    and canonical_text
                ):
                    source_text = text.strip() if isinstance(text, str) else ""
                    if canonical_text not in source_text:
                        text = "\n".join(
                            value for value in (source_text, canonical_text) if value
                        )[:200_000]
                if (
                    precomputed
                    and precomputed[0].strip()
                    and row["source"] in {"visible_ui", "accessibility_visible_ui"}
                ):
                    source_text = text.strip() if isinstance(text, str) else ""
                    ocr_text = precomputed[0].strip()
                    if ocr_text and ocr_text not in source_text:
                        text = "\n".join(
                            value
                            for value in (source_text, ocr_text)
                            if value
                        )[:200_000]
                ext = Path(row["path"]).suffix.lower()
                origin_hint = " ".join(
                    str(meta.get(key) or "")
                    for key in ("directory_hint", "display_name", "album")
                ).strip() or None
                is_heavy = (
                    ext in VID_EXT
                    or ext in IMG_EXT
                    or row["source"] == "video"
                    or (row["mime"] or "").startswith(("video/", "image/"))
                )
                if not is_heavy and settings.content_detection_enabled and text.strip():
                    from app.services import content_policy

                    is_heavy = bool(
                        settings.content_text_model
                        or (
                            settings.gpu_stack_enabled
                            and settings.gpu_qwen_enabled
                            and content_policy.should_adjudicate_text(text)
                        )
                    )
                if is_heavy:
                    outcome = await asyncio.to_thread(
                        run_guarded,
                        analyze_content_result,
                        path,
                        row["mime"] or "",
                        row["source"],
                        text,
                        keywords,
                        precomputed_ocr_text=precomputed[0] if precomputed else None,
                        precomputed_ocr_backend=precomputed[1] if precomputed else None,
                        origin_hint=origin_hint,
                    )
                else:
                    outcome = analyze_content_result(
                        path,
                        row["mime"] or "",
                        row["source"],
                        text,
                        keywords,
                        precomputed_ocr_text=precomputed[0] if precomputed else None,
                        precomputed_ocr_backend=precomputed[1] if precomputed else None,
                        origin_hint=origin_hint,
                    )
                results = list(outcome.findings)
                if cache_key and outcome.cacheable:
                    await set_cached(cache_key, results)

            media_year = None
            media_captured_at = None
            try:
                media_year = meta.get("captured_year")
                media_captured_at = meta.get("captured_at")
            except AttributeError:
                pass
            if media_year is None and path.is_file():
                from app.services.media_dates import capture_meta

                cm = capture_meta(path)
                media_year = cm.get("captured_year")
                media_captured_at = cm.get("captured_at")

            out: list[tuple] = []
            for f in results:
                out.append(
                    (
                        str(uuid.uuid4()),
                        session_id,
                        row["id"],
                        row["source"],
                        row["path"],
                        f["category"],
                        f["label"],
                        f["confidence"],
                        f["layer_origin"],
                        f["evidence"],
                        ReviewStatus.PENDING.value,
                        utcnow(),
                        media_year,
                        media_captured_at,
                    )
                )
            return out

    analyzed_ids: list[str] = []

    async def publish_progress() -> None:
        analyzed = min(previously_analyzed + len(analyzed_ids), file_count)
        elapsed = max(time.perf_counter() - t0, 1e-6)
        fps = len(analyzed_ids) / elapsed
        batch_fraction = min(len(analyzed_ids) / max(total, 1), 1.0)
        pct = progress_start + batch_fraction * (progress_end - progress_start)
        msg = (
            f"{progress_label} ({analyzed}/{file_count}) · "
            f"L3:{layer_counts.get('L3', 0)} L4:{layer_counts.get('L4', 0)} · "
            f"OCR:{hits_ocr} ASR:{hits_asr}"
        )
        await on_progress(
            progress_status,
            pct,
            msg,
            files_listed=file_count,
            files_pulled=file_count,
            files_indexed=file_count,
            files_analyzed=analyzed,
            findings_count=findings_count,
            throughput_files_per_sec=round(fps, 1),
            hits_l1=layer_counts.get("L1", 0),
            hits_l2=layer_counts.get("L2", 0),
            hits_l3=layer_counts.get("L3", 0),
            hits_l4=layer_counts.get("L4", 0),
            hits_ocr=hits_ocr,
            hits_asr=hits_asr,
        )

    async def commit_results(results: list[tuple[Any, list[tuple]]]) -> None:
        nonlocal findings_count
        filtered: list[tuple[Any, list[tuple]]] = []
        for row, items in results:
            kept: list[tuple] = []
            ident = str(row["sha256"] or row["id"])
            for item in items:
                key = (ident, str(item[6]))
                if key in seen_finding_keys:
                    continue
                seen_finding_keys.add(key)
                kept.append(item)
            filtered.append((row, kept))
        results = filtered
        rows_to_insert = [item for _, items in results for item in items]
        async with db.transaction() as conn:
            await conn.executemany(
                "UPDATE files SET analyzed = 1 WHERE id = ?",
                [(row["id"],) for row, _ in results],
            )
            if rows_to_insert:
                await conn.executemany(
                    """
                    INSERT INTO findings (
                        id, session_id, file_id, source, path, category, label,
                        confidence, layer_origin, evidence, review_status, created_at,
                        media_year, media_captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )

        for row, items in results:
            analyzed_ids.append(row["id"])
            findings_count += len(items)
            for item in items:
                layer_counts[item[8]] = layer_counts.get(item[8], 0) + 1
                category_counts[item[5]] = category_counts.get(item[5], 0) + 1
                source_counts[item[3]] = source_counts.get(item[3], 0) + 1
                _count_media_kinds(str(item[6]))
        await publish_progress()

    async def process_with_row(row) -> tuple[Any, list[tuple]]:
        return row, await process(row)

    wave = max(settings.cv_batch_size, 16)
    clean_commit_batch = 8
    for start in range(0, total, wave):
        batch = selected[start : start + wave]
        tasks = [asyncio.create_task(process_with_row(row)) for row in batch]
        pending_results: list[tuple[Any, list[tuple]]] = []
        try:
            for completed in asyncio.as_completed(tasks):
                result = await completed
                pending_results.append(result)
                if result[1] or len(pending_results) >= clean_commit_batch:
                    await commit_results(pending_results)
                    pending_results = []
            if pending_results:
                await commit_results(pending_results)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    final_file_row = await db.fetchone(
        "SELECT COUNT(*) AS total, COALESCE(SUM(analyzed), 0) AS analyzed "
        "FROM files WHERE session_id = ?",
        (session_id,),
    )
    final_finding_row = await db.fetchone(
        "SELECT COUNT(*) AS total FROM findings WHERE session_id = ?",
        (session_id,),
    )
    final_analyzed = int(final_file_row["analyzed"]) if final_file_row else 0
    final_findings = int(final_finding_row["total"]) if final_finding_row else 0
    stats = {
        "layer_counts": layer_counts,
        "category_counts": category_counts,
        "source_counts": source_counts,
        "files_selected": file_count,
        "hits_ocr": hits_ocr,
        "hits_asr": hits_asr,
    }
    return (
        final_analyzed,
        final_findings,
        (time.perf_counter() - t0) * 1000,
        stats,
    )
