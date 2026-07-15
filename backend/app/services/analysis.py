from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, Layer, ReviewStatus, SessionStatus
from app.services.acquisition import IMG_EXT, TEXT_EXT, VID_EXT
from app.services import vision as vis


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
    if ext in IMG_EXT or ext in VID_EXT or mime.startswith("image/") or mime.startswith("video/"):
        return ""
    if ext in {".pdf", ".doc", ".docx", ".rtf", ".odt", ".zip", ".rar", ".7z"}:
        return ""
    if ext in {".imgmeta", ".vidmeta"} or _is_probably_text(path, mime):

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


def analyze_content(path: Path, mime: str, source: str, text: str, keywords: list[str]) -> list[dict]:
    ext = path.suffix.lower()
    findings: list[dict] = []
    findings.extend(analyze_path_signals(str(path), keywords))

    is_image = ext in IMG_EXT or mime.startswith("image/") or (
        source == "gallery" and mime.startswith("image/")
    )
    is_video = ext in VID_EXT or source == "video" or mime.startswith("video/")

    if ext == ".imgmeta":
        findings.extend(analyze_image_meta_l3(text))
    elif is_image:
        # Teks dari gambar hanya lewat OCR / vision — jangan L1 pada byte JPEG
        findings.extend(vis.analyze_image_file(path))
    elif ext == ".vidmeta":
        findings.extend(analyze_video_meta_l4(text))
    elif is_video:
        findings.extend(vis.analyze_video_file(path))
    elif _is_probably_text(path, mime) and text.strip():
        findings.extend(analyze_text_l1_l2(text, keywords))
    # pdf/docx/binaries lain: path signals saja sampai ada extractor khusus

    # de-dupe by label+evidence prefix
    seen: set[str] = set()
    uniq: list[dict] = []
    for f in findings:
        key = f"{f['label']}|{f['evidence'][:80]}"
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


async def analyze_session(
    session_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress,
) -> tuple[int, int, float, dict]:
    t0 = time.perf_counter()
    mode_token = set_analysis_mode(mode)
    try:
        return await _analyze_session_body(session_id, staging, mode, on_progress, t0)
    finally:
        reset_analysis_mode(mode_token)


async def _analyze_session_body(
    session_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress,
    t0: float,
) -> tuple[int, int, float, dict]:
    rows = await db.fetchall(
        "SELECT id, source, path, sha256, mime, meta_json FROM files WHERE session_id = ?",
        (session_id,),
    )

    # Di mode gallery-focus: prioritas analisis sumber gallery, baru sisanya
    gallery_rows = [r for r in rows if r["source"] == "gallery"]
    other_rows = [r for r in rows if r["source"] != "gallery"]
    ordered = gallery_rows + other_rows

    image_cap = settings.image_cap_quick if mode == AcquisitionMode.QUICK else settings.image_cap_full
    gallery_seen = 0
    video_seen = 0
    selected = []
    for r in ordered:
        if r["source"] == "gallery":
            gallery_seen += 1
            if gallery_seen > image_cap:
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
    findings_count = 0
    finding_rows: list[tuple] = []
    sem = asyncio.Semaphore(settings.worker_concurrency)
    keywords = settings.risk_keywords
    layer_counts = {"L1": 0, "L2": 0, "L3": 0, "L4": 0}
    category_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    hits_ocr = 0
    hits_asr = 0

    def _count_media_kinds(label: str) -> None:
        nonlocal hits_ocr, hits_asr
        low = label.lower()
        if "ocr" in low or "on-screen" in low:
            hits_ocr += 1
        if "audio" in low or "lirik" in low or "whisper" in low:
            hits_asr += 1

    async def process(row) -> list[tuple]:
        async with sem:
            cached = await get_cached(row["sha256"]) if row["sha256"] else None
            path = staging / row["path"]
            if cached is not None:
                results = cached
            else:
                text = await read_preview(path, row["mime"] or "")
                ext = Path(row["path"]).suffix.lower()
                is_heavy = (
                    ext in VID_EXT
                    or ext in IMG_EXT
                    or row["source"] == "video"
                    or (row["mime"] or "").startswith(("video/", "image/"))
                )
                if is_heavy:
                    results = await asyncio.to_thread(
                        analyze_content,
                        path,
                        row["mime"] or "",
                        row["source"],
                        text,
                        keywords,
                    )
                else:
                    results = analyze_content(
                        path, row["mime"] or "", row["source"], text, keywords
                    )
                if row["sha256"]:
                    await set_cached(row["sha256"], results)

            media_year = None
            media_captured_at = None
            try:
                meta = json.loads(row["meta_json"] or "{}")
                media_year = meta.get("captured_year")
                media_captured_at = meta.get("captured_at")
            except (TypeError, json.JSONDecodeError):
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
    wave = max(settings.cv_batch_size, 16)
    for start in range(0, total, wave):
        batch = selected[start : start + wave]
        batch_findings = await asyncio.gather(*(process(r) for r in batch))
        for row, items in zip(batch, batch_findings):
            finding_rows.extend(items)
            findings_count += len(items)
            analyzed_ids.append(row["id"])
            for it in items:
                layer_counts[it[8]] = layer_counts.get(it[8], 0) + 1
                category_counts[it[5]] = category_counts.get(it[5], 0) + 1
                source_counts[it[3]] = source_counts.get(it[3], 0) + 1
                _count_media_kinds(str(it[6]))

        if batch:
            placeholders = ",".join("?" * len(batch))
            await db.execute(
                f"UPDATE files SET analyzed = 1 WHERE id IN ({placeholders})",
                tuple(r["id"] for r in batch),
            )

        analyzed = len(analyzed_ids)
        elapsed = max(time.perf_counter() - t0, 1e-6)
        fps = analyzed / elapsed
        pct = 60 + (analyzed / max(total, 1)) * 38
        msg = (
            f"Analisis AI ({analyzed}/{total}) · "
            f"L3:{layer_counts.get('L3', 0)} L4:{layer_counts.get('L4', 0)} · "
            f"OCR:{hits_ocr} ASR:{hits_asr}"
        )
        await on_progress(
            SessionStatus.ANALYZING,
            pct,
            msg,
            files_listed=total,
            files_pulled=total,
            files_indexed=total,
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

    if finding_rows:
        await db.executemany(
            """
            INSERT INTO findings (
                id, session_id, file_id, source, path, category, label,
                confidence, layer_origin, evidence, review_status, created_at,
                media_year, media_captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            finding_rows,
        )

    stats = {
        "layer_counts": layer_counts,
        "category_counts": category_counts,
        "source_counts": source_counts,
        "files_selected": total,
        "hits_ocr": hits_ocr,
        "hits_asr": hits_asr,
    }
    return len(analyzed_ids), findings_count, (time.perf_counter() - t0) * 1000, stats
