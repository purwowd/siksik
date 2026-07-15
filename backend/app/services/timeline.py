"""Timeline risiko galeri — agregat temuan per tahun media + insight tren."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any


CATEGORY_LABELS = {
    "anti_pemerintah": "anti pemerintah / politik",
    "perilaku_menyimpang": "perilaku menyimpang",
    "konten_visual": "konten visual",
    "konten_audio": "konten audio / lirik",
    "konten_teks": "konten teks",
}


def build_risk_timeline(
    rows: list[dict[str, Any]],
    *,
    years_back: int = 5,
    now: datetime | None = None,
) -> dict[str, Any]:
    """rows: media_year, category, review_status (opsional)."""
    now = now or datetime.now()
    current_year = now.year
    year_min = current_year - years_back + 1
    years = list(range(year_min, current_year + 1))

    by_year_cat: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_year_total: dict[int, int] = defaultdict(int)
    unknown = 0

    for r in rows:
        y = r.get("media_year")
        if y is None:
            unknown += 1
            continue
        try:
            yi = int(y)
        except (TypeError, ValueError):
            unknown += 1
            continue
        if yi < year_min:
            # fold older into first bucket edge note via older_count
            by_year_total[year_min] += 0  # ensure key
            continue
        if yi > current_year:
            yi = current_year
        cat = str(r.get("category") or "lainnya")
        by_year_cat[yi][cat] += 1
        by_year_total[yi] += 1

    # recount older than window
    older = 0
    for r in rows:
        y = r.get("media_year")
        try:
            yi = int(y) if y is not None else None
        except (TypeError, ValueError):
            continue
        if yi is not None and yi < year_min:
            older += 1

    series = []
    for y in years:
        cats = dict(by_year_cat.get(y, {}))
        series.append(
            {
                "year": y,
                "total": by_year_total.get(y, 0),
                "by_category": [{"name": k, "count": v} for k, v in sorted(cats.items(), key=lambda x: -x[1])],
            }
        )

    last_year = by_year_total.get(current_year, 0)
    # "1 tahun terakhir" ≈ current calendar year; prior window = previous years in range
    prior_years = [y for y in years if y < current_year]
    prior_total = sum(by_year_total.get(y, 0) for y in prior_years)
    prior_avg = (prior_total / len(prior_years)) if prior_years else 0.0

    peak_year = max(years, key=lambda y: by_year_total.get(y, 0)) if years else current_year
    peak_n = by_year_total.get(peak_year, 0)

    dominant_prior: dict[str, int] = defaultdict(int)
    for y in prior_years:
        for c, n in by_year_cat.get(y, {}).items():
            dominant_prior[c] += n
    top_cat = max(dominant_prior.items(), key=lambda x: x[1])[0] if dominant_prior else None

    if peak_n == 0 and last_year == 0 and older == 0:
        insight = (
            "Belum ada temuan bertanggal media pada jendela 5 tahun. "
            "Pastikan OCR/ASR aktif agar temuan teks/foto/video terisi."
        )
        trend = "unknown"
    elif last_year == 0 and prior_total > 0:
        insight = (
            f"Risiko historis terdeteksi terutama {peak_year} (n={peak_n})"
            + (f", didominasi {CATEGORY_LABELS.get(top_cat, top_cat)}" if top_cat else "")
            + f". Tahun {current_year}: 0 temuan — indikasi penurunan vs riwayat "
            f"(rata-rata ~{prior_avg:.1f}/tahun pada {prior_years[0]}–{prior_years[-1] if prior_years else '—'}). "
            "Tetap verifikasi manual (peserta bisa berubah dalam 12 bulan terakhir)."
        )
        trend = "improved"
    elif last_year > 0 and prior_avg > 0 and last_year < prior_avg * 0.5:
        insight = (
            f"Tahun {current_year} masih ada {last_year} temuan, tetapi lebih rendah dari "
            f"rata-rata historis (~{prior_avg:.1f}/tahun). Puncak historis: {peak_year} (n={peak_n}). "
            "Tren menurun — review seleksi tetap wajib."
        )
        trend = "improving"
    elif last_year >= max(prior_avg, 1) and prior_total > 0:
        insight = (
            f"Tahun {current_year} mencatat {last_year} temuan — setara/lebih tinggi dari "
            f"historis (avg ~{prior_avg:.1f}/tahun). Puncak: {peak_year} (n={peak_n}). "
            "Tidak ada indikasi mereda dalam 12 bulan terakhir."
        )
        trend = "elevated"
    else:
        insight = (
            f"Sebaran temuan {year_min}–{current_year}: puncak {peak_year} (n={peak_n}); "
            f"tahun berjalan {last_year}. Selisih tren terbatas — cek detail kategori per tahun."
        )
        trend = "stable"

    if older:
        insight += f" (+{older} temuan lebih tua dari {year_min}, di luar jendela)."

    return {
        "years_back": years_back,
        "year_from": year_min,
        "year_to": current_year,
        "series": series,
        "older_than_window": older,
        "unknown_date": unknown,
        "trend": trend,
        "insight": insight,
        "peak_year": peak_year,
        "peak_count": peak_n,
        "current_year_count": last_year,
        "prior_avg": round(prior_avg, 2),
    }
