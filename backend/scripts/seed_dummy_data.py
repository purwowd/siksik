#!/usr/bin/env python3
"""Isi database lab dengan sesi simulasi + temuan (tanpa HP).

Model demo: 1 calon ASN = 1 perangkat = 1 sesi akuisisi = banyak temuan.
Riwayat di picker = daftar sesi peserta.

Tidak mengubah SADT_LAB_DEMO_MODE di .env — simulasi hanya saat script jalan.

Usage:
  cd backend && python scripts/seed_dummy_data.py --purge --rich
  cd backend && python scripts/seed_dummy_data.py --purge --quick
  cd backend && python scripts/seed_dummy_data.py --purge --full --review-one
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import config
from app.core.config import ensure_dirs
from app.core.db import db
from app.models.enums import AcquisitionMode, DeviceType, Scenario
from app.models.session import ParticipantInput, StartSessionRequest
from app.services.auth import ensure_auth_schema
from app.services.recommendation import apply_recommendation
from app.services.sessions import sessions

TERMINAL = {"completed", "failed", "cancelled"}

# Setiap kasus = satu calon + satu device unik (masih sim-*).
CASES = (
    {
        "key": "lulus",
        "device_id": "sim-android-ahmad",
        "device_type": DeviceType.ANDROID,
        "scenario": Scenario.LULUS,
        "mode": AcquisitionMode.QUICK,
        "participant": {
            "full_name": "Ahmad Fauzi",
            "registration_no": "ASN-2026-0101",
            "nik": "3201010101900001",
            "organization": "Pemda Demo · CPNS",
        },
    },
    {
        "key": "review",
        "device_id": "sim-android-siti",
        "device_type": DeviceType.ANDROID,
        "scenario": Scenario.TIDAK_LULUS,
        "mode": AcquisitionMode.QUICK,
        "participant": {
            "full_name": "Siti Rahmawati",
            "registration_no": "ASN-2026-0102",
            "nik": "3201010202900002",
            "organization": "Kementerian Demo · PPPK",
        },
    },
    {
        "key": "tidak_lulus",
        "device_id": "sim-iphone-budi",
        "device_type": DeviceType.IOS,
        "scenario": Scenario.TIDAK_LULUS,
        "mode": AcquisitionMode.QUICK,
        "review_all": True,
        "participant": {
            "full_name": "Budi Santoso",
            "registration_no": "ASN-2026-0103",
            "nik": "3201010303900003",
            "organization": "BUMN Demo · CPNS",
        },
    },
)

PENDING_PURE_CASE = {
    "key": "pending_pure",
    "device_id": "sim-android-dewi",
    "device_type": DeviceType.ANDROID,
    "scenario": Scenario.TIDAK_LULUS,
    "mode": AcquisitionMode.QUICK,
    "participant": {
        "full_name": "Dewi Lestari",
        "registration_no": "ASN-2026-0201",
        "nik": "3201020101900004",
        "organization": "Pemda Demo · CPNS",
    },
}

RICH_CASES = (
    *CASES,
    PENDING_PURE_CASE,
    {
        "key": "partial",
        "device_id": "sim-android-hana",
        "device_type": DeviceType.ANDROID,
        "scenario": Scenario.TIDAK_LULUS,
        "mode": AcquisitionMode.FULL,
        "partial_review": True,
        "participant": {
            "full_name": "Hana Wijaya",
            "registration_no": "ASN-2026-0302",
            "nik": "3201010404900005",
            "organization": "Pemda Demo · CPNS",
        },
    },
    {
        "key": "lulus_ios",
        "device_id": "sim-iphone-galih",
        "device_type": DeviceType.IOS,
        "scenario": Scenario.LULUS,
        "mode": AcquisitionMode.QUICK,
        "participant": {
            "full_name": "Galih Nugroho",
            "registration_no": "ASN-2026-0301",
            "nik": "3201010505900006",
            "organization": "Instansi Demo · CPNS",
        },
    },
)

_SESSION_CHILD_TABLES = (
    "media_tickets",
    "social_snapshot_enrichments",
    "selection_candidates",
    "crawl_transfers",
    "crawl_artifacts",
    "crawl_events",
    "crawl_records",
    "crawl_runs",
    "agent_bootstrap_events",
    "agent_runtimes",
    "findings",
    "files",
)


def case_label(case: dict) -> str:
    p = case["participant"]
    return f"Dummy · {p['full_name']} · {p['registration_no']}"


async def cancel_active_sessions() -> int:
    rows = await db.fetchall(
        """
        SELECT id, status FROM sessions
        WHERE status NOT IN ('completed', 'failed', 'cancelled')
        """
    )
    n = 0
    for row in rows:
        try:
            await sessions.cancel(str(row["id"]))
            n += 1
        except Exception as exc:
            print(f"  skip cancel {row['id']}: {exc}", file=sys.stderr)
    return n


async def purge_lab_sessions() -> int:
    """Hapus sesi lab lama (Dummy / ASN-2026-*) + artefak terkait."""
    rows = await db.fetchall("SELECT id, label, participant_json FROM sessions")
    victims: list[str] = []
    for row in rows:
        label = str(row["label"] or "")
        try:
            participant = json.loads(row["participant_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            participant = {}
        reg = str((participant or {}).get("registration_no") or "")
        if (
            label.startswith("Dummy")
            or label.startswith("E2E ")
            or reg.upper().startswith("ASN-2026-")
            or reg.upper().startswith("TEST-")
        ):
            victims.append(str(row["id"]))

    if not victims:
        return 0

    for sid in victims:
        for table in _SESSION_CHILD_TABLES:
            try:
                await db.execute(f"DELETE FROM {table} WHERE session_id = ?", (sid,))
            except Exception:
                pass
        await db.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        for folder in (
            config.settings.staging_dir / sid,
            config.settings.data_dir / "reports" / sid,
        ):
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
        report_json = config.settings.data_dir / "reports" / f"{sid}.json"
        report_html = config.settings.data_dir / "reports" / f"{sid}.html"
        for path in (report_json, report_html):
            if path.exists():
                path.unlink(missing_ok=True)
    return len(victims)


async def wait_session(session_id: str, *, timeout_s: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        row = await db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not row:
            raise RuntimeError(f"session missing: {session_id}")
        from app.core.db import row_to_session

        last = row_to_session(row)
        if last["status"] in TERMINAL:
            return last
        await asyncio.sleep(0.25)
    raise TimeoutError(f"session {session_id} timeout (last={last.get('status')})")


async def operator_id() -> str:
    row = await db.fetchone("SELECT id FROM users WHERE username = 'operator' AND active = 1")
    if not row:
        raise RuntimeError("user operator tidak ditemukan — jalankan setup_lab_panitia.py dulu")
    return str(row["id"])


async def create_simulated_case(
    case: dict,
    *,
    file_count: int,
    operator: str,
) -> dict:
    participant = ParticipantInput.model_validate(case["participant"])
    label = case_label(case)
    req = StartSessionRequest(
        device_id=case["device_id"],
        device_type=case["device_type"],
        mode=case["mode"],
        scenario=case["scenario"],
        file_count=file_count,
        label=label,
        participant=participant,
        force_simulated=True,
    )
    started = await sessions.create_and_run(req, operator_id=operator)
    sid = started["id"]
    print(f"  … {label} · {case['device_id']} ({sid[:8]}…)", flush=True)
    final = await wait_session(sid)
    findings = final.get("progress", {}).get("findings_count", 0)
    print(
        f"  ✓ {participant.full_name}: {final['status']} · "
        f"{final.get('recommendation')} · {findings} temuan",
        flush=True,
    )
    return final


async def review_first_pending(session_id: str, decision: str = "confirmed") -> int:
    rows = await db.fetchall(
        """
        SELECT id FROM findings
        WHERE session_id = ? AND review_status = 'pending'
        ORDER BY confidence DESC
        LIMIT 1
        """,
        (session_id,),
    )
    if not rows:
        return 0
    fid = str(rows[0]["id"])
    await db.execute(
        "UPDATE findings SET review_status = ? WHERE id = ?",
        (decision, fid),
    )
    await apply_recommendation(session_id)
    return 1


async def review_fraction(session_id: str, fraction: float = 0.5, decision: str = "confirmed") -> int:
    rows = await db.fetchall(
        """
        SELECT id FROM findings
        WHERE session_id = ? AND review_status = 'pending'
        ORDER BY confidence DESC
        """,
        (session_id,),
    )
    if not rows:
        return 0
    take = max(1, int(len(rows) * fraction))
    for row in rows[:take]:
        await db.execute(
            "UPDATE findings SET review_status = ? WHERE id = ?",
            (decision, str(row["id"])),
        )
    await apply_recommendation(session_id)
    return take


async def review_all(session_id: str, decision: str = "confirmed") -> int:
    rows = await db.fetchall(
        "SELECT id FROM findings WHERE session_id = ? AND review_status = 'pending'",
        (session_id,),
    )
    for row in rows:
        await db.execute(
            "UPDATE findings SET review_status = ? WHERE id = ?",
            (decision, str(row["id"])),
        )
    if rows:
        await apply_recommendation(session_id)
    return len(rows)


async def main_async(args: argparse.Namespace) -> int:
    ensure_dirs()
    await db.connect()
    await ensure_auth_schema()

    config.settings.lab_demo_mode = True
    config.settings.e2e_simulation = True

    if args.cancel_active or args.purge:
        n = await cancel_active_sessions()
        if n:
            print(f"Dibatalkan {n} sesi aktif")

    if args.purge:
        removed = await purge_lab_sessions()
        print(f"Dihapus {removed} sesi lab lama (Dummy / ASN demo)")

    active = await db.fetchone(
        """
        SELECT id FROM sessions
        WHERE status NOT IN ('completed', 'failed', 'cancelled')
        LIMIT 1
        """
    )
    if active:
        print(
            "Masih ada sesi aktif. Batalkan di UI atau: --purge / --cancel-active",
            file=sys.stderr,
        )
        return 1

    op = await operator_id()
    file_count = args.file_count
    if args.pending_pure:
        cases = [PENDING_PURE_CASE]
    elif args.rich:
        cases = list(RICH_CASES)
    else:
        cases = list(CASES if args.full else CASES[:2])

    print(f"Membuat {len(cases)} riwayat calon (file_count={file_count})…")
    print("Model: 1 peserta · 1 perangkat · 1 sesi · N temuan\n")
    created: list[dict] = []
    for case in cases:
        try:
            created.append(await create_simulated_case(case, file_count=file_count, operator=op))
            sid = created[-1]["id"]
            if case.get("partial_review"):
                n = await review_fraction(sid, 0.5, "confirmed")
                created[-1] = await sessions.get(sid)
                print(
                    f"  ✓ Review sebagian ({n} temuan) → {created[-1].get('recommendation')}"
                )
            elif case.get("review_all"):
                n = await review_all(sid, "confirmed")
                created[-1] = await sessions.get(sid)
                print(f"  ✓ Review semua ({n}) → {created[-1].get('recommendation')}")
        except RuntimeError as exc:
            if "Sesi lain masih berjalan" in str(exc) or "No. peserta" in str(exc):
                print(str(exc), file=sys.stderr)
                return 1
            raise

    if args.review_one and len(created) >= 2:
        sid = created[1]["id"]
        n = await review_first_pending(sid, "confirmed")
        if n:
            row = await db.fetchone("SELECT recommendation FROM sessions WHERE id = ?", (sid,))
            print(f"  ✓ Konfirmasi 1 temuan pada sesi review → {row['recommendation'] if row else '?'}")

    print("\nRiwayat calon siap. Login analis → Temuan; pimpinan → Laporan.")
    print("Sesi:")
    for item in created:
        p = item.get("participant") or {}
        name = p.get("full_name") or item["label"]
        reg = p.get("registration_no") or "—"
        findings = item.get("progress", {}).get("findings_count", 0)
        print(
            f"  · {name} · {reg} · {item['device_id']} · "
            f"{findings} temuan · {item.get('recommendation')}  id={item['id']}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed riwayat calon ASN (1 peserta = 1 device)")
    parser.add_argument("--quick", action="store_true", help="file_count=25 (default 45)")
    parser.add_argument("--full", action="store_true", help="3 sesi inti")
    parser.add_argument("--cancel-active", action="store_true", help="Batalkan sesi aktif dulu")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Hapus sesi Dummy/ASN demo lama sebelum seed baru",
    )
    parser.add_argument("--review-one", action="store_true", help="Konfirmasi 1 temuan di sesi review")
    parser.add_argument("--rich", action="store_true", help="6 calon + file_count lebih besar (default 100)")
    parser.add_argument("--pending-pure", action="store_true", help="Satu sesi tidak_lulus, semua pending")
    parser.add_argument("--file-count", type=int, default=0, help="Override jumlah file sintetis")
    args = parser.parse_args()
    if args.file_count <= 0:
        if args.rich:
            args.file_count = 100
        else:
            args.file_count = 25 if args.quick else 45
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
