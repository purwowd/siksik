from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.db import db


def _ms(v: float) -> str:
    if not v:
        return "-"
    if v < 1000:
        return f"{v:.0f} ms"
    return f"{v / 1000:.2f} s"


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


async def build_session_report(session_id: str) -> dict:
    row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise KeyError("Session not found")

    findings = await db.fetchall(
        "SELECT * FROM findings WHERE session_id = ? ORDER BY confidence DESC",
        (session_id,),
    )
    files = await db.fetchone(
        "SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes),0) AS bytes FROM files WHERE session_id = ?",
        (session_id,),
    )
    progress = json.loads(row["progress_json"])
    timing = json.loads(row["timing_json"])

    by_cat: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for f in findings:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
        by_layer[f["layer_origin"]] = by_layer.get(f["layer_origin"], 0) + 1
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": {
            "id": row["id"],
            "label": row["label"],
            "device_id": row["device_id"],
            "device_type": row["device_type"],
            "mode": row["mode"],
            "scenario": row["scenario"],
            "status": row["status"],
            "recommendation": row["recommendation"],
            "acquisition_method": progress.get("acquisition_method", "unknown"),
        },
        "metrics": {
            "files": files["c"] if files else 0,
            "bytes": files["bytes"] if files else 0,
            "findings": len(findings),
            "timing": timing,
            "progress": progress,
        },
        "breakdown": {
            "by_category": by_cat,
            "by_layer": by_layer,
            "by_source": by_source,
        },
        "findings": [
            {
                "label": f["label"],
                "category": f["category"],
                "source": f["source"],
                "path": f["path"],
                "confidence": f["confidence"],
                "layer": f["layer_origin"],
                "evidence": f["evidence"],
                "review_status": f["review_status"],
            }
            for f in findings
        ],
    }
    return report


def report_to_html(report: dict) -> str:
    s = report["session"]
    m = report["metrics"]
    b = report["breakdown"]
    rows = "".join(
        "<tr>"
        f"<td>{_esc(f['label'])}</td>"
        f"<td>{_esc(f['category'])}</td>"
        f"<td>{_esc(f['source'])}</td>"
        f"<td>{_esc(f['layer'])}</td>"
        f"<td>{f['confidence']:.0%}</td>"
        f"<td><code>{_esc(f['path'])}</code></td>"
        "</tr>"
        for f in report["findings"][:200]
    )
    cat = (
        "".join(f"<li>{_esc(k)}: <b>{_esc(v)}</b></li>" for k, v in b["by_category"].items())
        or "<li>-</li>"
    )
    rec = s["recommendation"] or "-"
    bad_class = "bad" if s["recommendation"] == "TIDAK LULUS" else ""
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8"/>
<title>SADT Report — {_esc(s['id'][:8])}</title>
<style>
body{{font-family:ui-monospace,Menlo,monospace;background:#061018;color:#d7ece8;padding:24px}}
h1,h2{{color:#00e5c8;letter-spacing:.06em;text-transform:uppercase}}
.box{{border:1px solid rgba(0,229,200,.25);padding:14px;margin:12px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{border-bottom:1px solid rgba(0,229,200,.15);padding:8px;text-align:left;vertical-align:top}}
.badge{{display:inline-block;padding:4px 8px;border:1px solid #00e5c8;color:#00e5c8}}
.bad{{border-color:#ff4d5a;color:#ff4d5a}}
</style></head><body>
<h1>SADT // OPS REPORT</h1>
<div class="box">
  <div>Session: <code>{_esc(s['id'])}</code></div>
  <div>Device: {_esc(s['label'])} / {_esc(s['device_id'])} ({_esc(s['device_type'])})</div>
  <div>Mode: {_esc(s['mode'])} · Method: {_esc(s['acquisition_method'])}</div>
  <div>Recommendation:
    <span class="badge {bad_class}">{_esc(rec)}</span>
  </div>
</div>
<div class="box">
  <h2>Metrics</h2>
  <ul>
    <li>Files: {_esc(m['files'])} ({_esc(m['bytes'])} bytes)</li>
    <li>Findings: {_esc(m['findings'])}</li>
    <li>Acquire: {_esc(_ms(m['timing'].get('t_acquire_ms',0)))}</li>
    <li>Analyze: {_esc(_ms(m['timing'].get('t_analyze_ms',0)))}</li>
    <li>Total: {_esc(_ms(m['timing'].get('t_total_ms',0)))}</li>
  </ul>
  <h2>By category</h2>
  <ul>{cat}</ul>
</div>
<div class="box">
  <h2>Findings</h2>
  <table><thead><tr><th>Label</th><th>Category</th><th>Source</th><th>Layer</th><th>Conf</th><th>Path</th></tr></thead>
  <tbody>{rows or '<tr><td colspan="6">No findings</td></tr>'}</tbody></table>
</div>
<p>Generated {_esc(report['generated_at'])} · {_esc(settings.app_name)}</p>
</body></html>"""


async def save_session_report(session_id: str) -> Path:
    report = await build_session_report(session_id)
    out_dir = settings.data_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{session_id}.json"
    html_path = out_dir / f"{session_id}.html"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(report_to_html(report), encoding="utf-8")
    return html_path
