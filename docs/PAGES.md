# SATRIA — Cek per halaman

Audit layout, responsivitas, RBAC, dan perilaku UI per tab konsol (Agustus 2026).

**Setup & menjalankan:** [`SETUP.md`](./SETUP.md) · **Running:** [`RUNNING.md`](./RUNNING.md)

**Breakpoint CSS utama:** 1200 · 1100 · 960 · 900 · 720 · 600 · 480 px  
**Stylesheet responsif:** `frontend/src/styles/responsive.css` (import terakhir)  
**Rencana audit lengkap:** [`AUDIT_PLAN.md`](./AUDIT_PLAN.md)

---

## Login (`/`)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Layout | ✅ | Satu kolom terpusat — emblem + SATRIA + kartu login |
| Mobile | ✅ | Role demo 1 kolom di ≤480px; safe-area padding |
| A11y | ✅ | Label `htmlFor` pada field login |
| RBAC | — | Semua role login dari sini |

**Isi:** `SatriaMark`, SATRIA, motto, form, picker akun demo, hint `PoC lab · localhost`.

---

## Pengambilan Data (`/operator`)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Shell | ✅ | `FeaturePanel` ×2 dalam `grid-2` |
| Responsif | ✅ | `grid-2` → 1 kolom ≤900px; intake steps stack ≤720px |
| RBAC | operator, admin | Tab default operator |

**Isi:** sumber live/ZIP, mode quick/full, pipeline telemetri, timing sesi.

**Selesai analisa:** operator hanya toast (tanpa auto-navigate ke Temuan); admin/analis dapat tombol navigasi.

**Batasan Docker:** daftar perangkat live kosong — gunakan ZIP.

**Cek manual:**

1. Refresh perangkat tidak error
2. Upload ZIP + mulai analisa (jika enabled)
3. Pipeline update saat sesi aktif
4. Operator tidak melihat tab Temuan/Galeri/Laporan

---

## Temuan (`/temuan`)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Shell | ✅ | `FeaturePageShell` + KPI grid |
| Session picker | ✅ | Full width (toolbar vertikal) |
| Responsif | ✅ | Kartu temuan ≤600px; filter wrap |
| RBAC | analis, pimpinan, admin | Operator tidak punya akses |

**Isi:** session picker, filter review, modul drill-down, bulk review, keyboard shortcuts.

**Cek manual:**

1. Pilih sesi → temuan load
2. Filter & paginasi
3. Konfirmasi/tolak single + bulk
4. Mobile: kartu temuan, tombol review tidak terpotong

---

## Galeri (`/galeri`)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Shell | ✅ | `FeaturePageShell` |
| Session picker | ✅ | Full width |
| Responsif | ✅ | Chip album wrap |
| RBAC | analis, pimpinan, admin | |

**Isi:** filter album/asal, preview media, paginasi, note toolbar.

---

## Laporan (`/laporan`)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Shell | ✅ | `FeaturePageShell` + KPI |
| Session picker | ✅ | Full width |
| Responsif | ✅ | 2 kolom stack ≤1100px |
| RBAC | pimpinan, admin | Analis baca; otorisasi pimpinan+ |

**Isi:** verdict, meta sesi, export JSON/HTML, audit trail, otorisasi.

---

## Dasbor (`/dasbor`)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Shell | ✅ | `FeaturePageShell` |
| Session picker | ✅ | Full width |
| Responsif | ✅ | `satria-cols` 5→3→2→1 |
| RBAC | analis, pimpinan, admin | Operator → tidak ada tab |

**Isi:** modul analisis, integrity score, multi-session compare, feeds.

**Stub jujur:** WhatsApp/TikTok = modul planned, bukan clearance palsu.

---

## AppShell (chrome global)

| Aspek | Status | Catatan |
|-------|--------|---------|
| Topbar | ✅ | `SatriaMark` + SATRIA + user + Keluar |
| Tabs | ✅ | RBAC-filtered; scroll horizontal ≤720px |
| Breadcrumb | ✅ | Wrap di nav row |
| Case flow / stepper | ✅ | Scroll horizontal ≤720px |
| Error/toast | ✅ | Banner dismissible + toast stack |

**Dihapus (sengaja):** runtime banner, footer toolchain, readiness panel, assurance strip.

---

## Matriks role → tab

| Tab | operator | analis | pimpinan | admin |
|-----|:--------:|:------:|:--------:|:-----:|
| Pengambilan | ✅ | — | — | ✅ |
| Temuan | — | ✅ | ✅ | ✅ |
| Galeri | — | ✅ | ✅ | ✅ |
| Laporan | — | ✅ | ✅ | ✅ |
| Dasbor | — | ✅ | ✅ | ✅ |

**Landing default:** operator → Pengambilan · analis → Temuan · pimpinan → Laporan · admin → Pengambilan

---

## Regresi otomatis

```bash
# Backend (315+ tests)
cd backend && .venv/bin/python -m pytest -q

# Frontend unit (routes + RBAC)
cd frontend && npm run test

# Build
cd frontend && npm run build

# E2E (backend + frontend harus jalan)
cd frontend && npx playwright test
```

---

## Known limitations (PoC)

- WhatsApp / TikTok / short-video: placeholder, bukan crawl penuh
- Docker: tanpa ADB live
- PyTorch optional di image Docker — warning log aman diabaikan
