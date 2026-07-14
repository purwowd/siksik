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
    norm = _normalize(text)
    if not norm:
        return []
    findings: list[dict] = []
    for kw in keywords:
        if kw in norm:
            boost = 0.08 if any(x in norm for x in ("grup", "rahasia", "rencana", "segera", "malam ini")) else 0.0
            conf = min(0.99, 0.72 + len(kw) * 0.01 + boost)
            category = (
                "perilaku_menyimpang"
                if kw in ("narkoba", "judi online", "pornografi anak")
                else "anti_pemerintah"
            )
            findings.append(
                {
                    "category": category,
                    "label": f"Indikasi: {kw}",
                    "confidence": round(conf, 3),
                    "layer_origin": Layer.L2.value if boost else Layer.L1.value,
                    "evidence": text[:320],
                }
            )
    return findings


def analyze_path_signals(path: str, keywords: list[str]) -> list[dict]:
    """Filename / path keyword scan — useful for media without OCR."""
    norm = re.sub(r"[^a-z0-9]+", " ", path.lower()).strip()
    findings: list[dict] = []
    seen: set[str] = set()
    for kw in keywords:
        k = kw.lower().strip()
        if not k or k in seen:
            continue
        if k in norm:
            seen.add(k)
            findings.append(
                {
                    "category": "anti_pemerintah",
                    "label": f"Nama file/path: {kw}",
                    "confidence": 0.7,
                    "layer_origin": Layer.L1.value,
                    "evidence": path[:320],
                }
            )
            continue
        # token ≥4 chars dari frasa keyword (mis. "senjata" dari "senjata ilegal")
        for tok in re.findall(r"[a-z0-9]{4,}", k):
            if tok in seen:
                continue
            if tok in norm:
                seen.add(tok)
                findings.append(
                    {
                        "category": "anti_pemerintah",
                        "label": f"Nama file/path: {tok}",
                        "confidence": 0.68,
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
    ext = path.suffix.lower()
    if ext in {".imgmeta", ".vidmeta"} or _is_probably_text(path, mime):
        def _read() -> str:
            try:
                return path.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
            except OSError:
                return ""

        return await asyncio.to_thread(_read)

    # For binary docs/images: try utf-8 decode of head (may catch embedded strings)
    def _head() -> str:
        try:
            data = path.read_bytes()[: min(max_bytes, 64_000)]
            return data.decode("utf-8", errors="ignore")
        except OSError:
            return ""

    return await asyncio.to_thread(_head)


async def get_cached(sha256: str) -> list[dict] | None:
    row = await db.fetchone("SELECT result_json FROM hash_cache WHERE sha256 = ?", (sha256,))
    if not row:
        return None
    return json.loads(row["result_json"])


async def set_cached(sha256: str, results: list[dict]) -> None:
    await db.execute(
        """
        INSERT INTO hash_cache (sha256, result_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(sha256) DO UPDATE SET result_json=excluded.result_json, updated_at=excluded.updated_at
        """,
        (sha256, json.dumps(results), utcnow()),
    )


def analyze_content(path: Path, mime: str, source: str, text: str, keywords: list[str]) -> list[dict]:
    ext = path.suffix.lower()
    findings: list[dict] = []
    findings.extend(analyze_path_signals(str(path), keywords))

    if ext == ".imgmeta":
        findings.extend(analyze_image_meta_l3(text))
    elif ext in IMG_EXT or (source == "gallery" and mime.startswith("image/")):
        findings.extend(analyze_text_l1_l2(text, keywords))
        findings.extend(vis.analyze_image_file(path))
    elif ext == ".vidmeta":
        findings.extend(analyze_video_meta_l4(text))
    elif ext in VID_EXT or source == "video" or mime.startswith("video/"):
        findings.extend(analyze_text_l1_l2(text, keywords))
        findings.extend(vis.analyze_video_file(path))
    else:
        findings.extend(analyze_text_l1_l2(text, keywords))

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
    rows = await db.fetchall(
        "SELECT id, source, path, sha256, mime FROM files WHERE session_id = ?",
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
            if mode == AcquisitionMode.QUICK and video_seen > 80:
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

    async def process(row) -> list[tuple]:
        async with sem:
            cached = await get_cached(row["sha256"]) if row["sha256"] else None
            path = staging / row["path"]
            if cached is not None:
                results = cached
            else:
                text = await read_preview(path, row["mime"] or "")
                results = analyze_content(path, row["mime"] or "", row["source"], text, keywords)
                if row["sha256"]:
                    await set_cached(row["sha256"], results)

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
        await on_progress(
            SessionStatus.ANALYZING,
            pct,
            f"Analisis AI bertingkat ({analyzed}/{total})",
            files_listed=total,
            files_pulled=total,
            files_indexed=total,
            files_analyzed=analyzed,
            findings_count=findings_count,
            throughput_files_per_sec=round(fps, 1),
        )

    if finding_rows:
        await db.executemany(
            """
            INSERT INTO findings (
                id, session_id, file_id, source, path, category, label,
                confidence, layer_origin, evidence, review_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            finding_rows,
        )

    stats = {
        "layer_counts": layer_counts,
        "category_counts": category_counts,
        "source_counts": source_counts,
        "files_selected": total,
    }
    return len(analyzed_ids), findings_count, (time.perf_counter() - t0) * 1000, stats
