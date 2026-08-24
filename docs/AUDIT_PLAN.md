# SATRIA — Rencana Audit Komprehensif

Checklist audit per halaman, fitur, logic, API, dan pipeline.  
**Versi:** Agustus 2026 · **Lingkup:** PoC lab (host + Docker)

---

## 0. Tujuan & definisi selesai

| Tujuan | Kriteria lulus |
|--------|----------------|
| UI/UX | Layout penuh lebar, responsif, empty state jelas, tanpa info teknis berlebihan |
| RBAC | Setiap role hanya lihat/aksi yang diizinkan; 403 ditangani graceful |
| Logic | State URL ↔ sesi ↔ filter sinkron; refresh tidak kehilangan konteks |
| API | Kontrak request/response sesuai types frontend; error human-readable |
| Pipeline | Akuisisi → analisa → temuan → review → laporan end-to-end |
| Regresi | pytest + vitest + smoke E2E hijau |

**Lingkungan uji (dua matriks wajib):**

| Mode | Backend | Frontend | Catatan |
|------|---------|----------|---------|
| **Host lab** | `python run.py --reload` | `npm run dev` | ADB live mungkin tersedia |
| **Docker** | `docker compose up --build` | proxy Vite | Perangkat live kosong; pakai ZIP |

**Akun demo:** `operator` · `analis` · `pimpinan` · `admin` (lihat `docs/RUNNING.md`)

---

## 1. Urutan eksekusi (disarankan)

```
Fase A  Lingkungan + auth + shell global
Fase B  Operator (akuisisi) — sumber data untuk sisa alur
Fase C  Temuan + Galeri (analis)
Fase D  Dasbor + drill-down
Fase E  Laporan + otorisasi (pimpinan)
Fase F  Backend API & pipeline (otomatis + spot-check)
Fase G  Regresi + dokumentasi + sign-off
```

Estimasi manual penuh: **4–6 jam** (1 auditor) atau **2–3 jam** (2 orang paralel B/C).

---

## 2. Fase A — Shell global & auth

### 2.1 Login (`/`)

| # | Cek | Cara | Pass? |
|---|-----|------|-------|
| A1 | Form login render | Buka `/` | ☐ |
| A2 | Validasi error | User/sandi salah → banner error | ☐ |
| A3 | Demo role picker | Klik tiap role → field terisi | ☐ |
| A4 | Login sukses | Redirect ke tab landing role | ☐ |
| A5 | Logo & wordmark | `SatriaMark` SVG, tanpa animasi putar | ☐ |
| A6 | Responsif | 375 / 768 / 1280 — stack tunggal, tidak overflow | ☐ |

**Logic:** `useAuthSession` · `api.login` · token di `localStorage` / header

### 2.2 AppShell (setelah login)

| # | Cek | Cara | Pass? |
|---|-----|------|-------|
| A7 | Topbar | Logo + SATRIA + user + Keluar + Tur demo | ☐ |
| A8 | Tab visibility RBAC | Login per role → hanya tab yang diizinkan | ☐ |
| A9 | Breadcrumb | Label tab + sesi aktif | ☐ |
| A10 | Case flow bar | Stepper 5 tahap, progress sesi | ☐ |
| A11 | Toast & error banner | Trigger error API → toast/banner muncul | ☐ |
| A12 | Top loading bar | Request panjang → bar aktif | ☐ |
| A13 | Logout | Keluar → kembali login, state reset | ☐ |
| A14 | Tur demo | Buka/tutup, navigasi step | ☐ |
| A15 | Responsif tabs | ≤720px scroll horizontal tabs | ☐ |

**Logic hooks:** `useConsoleApp` (composer) · `useConsoleNavigation` · `useToastStack` · `useRuntimeHealth`

**Matriks tab × role (backend + frontend selaras):**

| Tab | operator | analis | pimpinan | admin |
|-----|:--------:|:------:|:--------:|:-----:|
| Pengambilan | ✓ | — | — | ✓ |
| Temuan | — | ✓ | ✓ | ✓ |
| Galeri | — | ✓ | ✓ | ✓ |
| Laporan | — | ✓ | ✓ | ✓ |
| Dasbor | — | ✓ | ✓ | ✓ |

Operator tidak punya `findings:read` / `report:read` — hanya akuisisi.

---

## 3. Fase B — Pengambilan Data (`/operator`)

**File:** `OperatorPage.tsx` · `useAcquisitionControls` · `useSessionStream` · backend `acquisition/orchestration.py`

### 3.1 UI & layout

| # | Cek | Pass? |
|---|-----|-------|
| B1 | Grid 2 panel (intake + telemetri) | ☐ |
| B2 | Responsif ≤900px → 1 kolom | ☐ |
| B3 | Pipeline track visual sesuai status sesi | ☐ |
| B4 | Tidak ada panel readiness/toolchain (sudah dihapus) | ☐ |

### 3.2 Fitur akuisisi

| # | Cek | Host | Docker | Pass? |
|---|-----|------|--------|-------|
| B5 | Refresh perangkat | Daftar ADB (jika ada) | Kosong / expected | ☐ |
| B6 | Pilih perangkat + mode quick/full | Start sesi | N/A live | ☐ |
| B7 | Upload ZIP + start | Start dari ZIP | Start dari ZIP | ☐ |
| B8 | Progress streaming | SSE/poll update progress | Same | ☐ |
| B9 | Cancel sesi | Status cancelled | Same | ☐ |
| B10 | Telemetri | File count, findings count, timing | Same | ☐ |
| B11 | Error handling | File ZIP invalid / terlalu besar | Same | ☐ |

### 3.3 Logic

| # | Cek | Pass? |
|---|-----|-------|
| B12 | `busy` lock UI saat start/cancel | ☐ |
| B13 | Sesi baru jadi sesi aktif workspace | ☐ |
| B14 | Navigasi ke tab lain → sesi tetap | ☐ |
| B15 | `authorizeNote` tersimpan untuk laporan | ☐ |

**API terkait:** `POST /sessions` · `POST /sessions/{id}/cancel` · `POST /sessions/from-zip` · `GET /devices` · `GET /sessions/{id}` · agent endpoints

---

## 4. Fase C — Temuan (`/temuan`)

**File:** `FindingsPage.tsx` · `FindingsList.tsx` · `useReviewActions` · `useWorkspaceQueries`

### 4.1 UI

| # | Cek | Pass? |
|---|-----|-------|
| C1 | Session picker **full width** (bukan setengah) | ☐ |
| C2 | KPI: menunggu / dikonfirmasi / ditolak / total | ☐ |
| C3 | Filter chip: semua · menunggu · dikonfirmasi · ditolak | ☐ |
| C4 | Empty state: belum pilih sesi | ☐ |
| C5 | Empty state: antrean kosong (filter pending) | ☐ |
| C6 | Skeleton saat loading | ☐ |
| C7 | Kartu temuan + media preview | ☐ |
| C8 | Paginasi | ☐ |
| C9 | Keyboard J/K/C/R + panel bantuan `?` | ☐ |
| C10 | Responsif: kartu temuan mobile | ☐ |

### 4.2 Review logic

| # | Cek | Pass? |
|---|-----|-------|
| C11 | Konfirmasi single finding | ☐ |
| C12 | Tolak single finding | ☐ |
| C13 | Bulk konfirmasi / tolak | ☐ |
| C14 | `reviewBusyId` / `bulkBusy` disable tombol | ☐ |
| C15 | KPI & list refresh setelah review | ☐ |
| C16 | Filter URL `?filter=pending` persist | ☐ |
| C17 | Filter modul `?modul=gallery` dari drill-down dasbor | ☐ |
| C18 | Verdict notice jika rekomendasi terbuka | ☐ |

**RBAC:** role tanpa `findings:review` → tombol review hidden/disabled

**API:** `GET /sessions/{id}/findings` · `POST .../review` · bulk review endpoints

---

## 5. Fase C — Galeri (`/galeri`)

**File:** `GalleryPage.tsx` · `GalleryList.tsx` · `MediaPreview.tsx` · `mediaFetchQueue`

| # | Cek | Pass? |
|---|-----|-------|
| G1 | Session picker full width | ☐ |
| G2 | Chip album: semua · frequent · recent · favorite | ☐ |
| G3 | Chip album asal (kind=album) | ☐ |
| G4 | URL `?album=` persist | ☐ |
| G5 | Thumbnail load (queue, tidak flood) | ☐ |
| G6 | Preview buka/tutup | ☐ |
| G7 | Paginasi | ☐ |
| G8 | Empty: tanpa sesi / album kosong | ☐ |
| G9 | Note toolbar: bukan seluruh isi HP | ☐ |

**API:** `GET /sessions/{id}/gallery` · `GET /media/...`

---

## 6. Fase D — Dasbor (`/dasbor`)

**File:** `DashboardPage.tsx` · `satriaModules.ts` · `AnalysisColumn.tsx` · backend `dashboard/stats.py`

| # | Cek | Pass? |
|---|-----|-------|
| D1 | Operator **tidak** bisa akses (403 / redirect) | ☐ |
| D2 | Kolom modul: gallery · sosmed · email · WA · forensic | ☐ |
| D3 | Modul **planned/stub** (WA, TikTok): copy jujur "belum aktif" | ☐ |
| D4 | Modul live: metrik + drill-down ke Temuan | ☐ |
| D5 | Integrity score panel | ☐ |
| D6 | Multi-session compare | ☐ |
| D7 | Dist bars / risk timeline | ☐ |
| D8 | Feed sesi & temuan terbaru + paginasi | ☐ |
| D9 | Grid responsif 5→3→2→1 kolom | ☐ |
| D10 | Tidak ada hero SPD / assurance strip redundan | ☐ |

**Logic:** `openSessionWithModule` · `buildAnalysisModules` availability flags

**API:** `GET /dashboard?session_id=` · `GET /sessions` (paginated) · findings paginated

---

## 7. Fase E — Laporan (`/laporan`)

**File:** `ReportPage.tsx` · `AuditTrailPanel.tsx` · backend `reports.py`

| # | Cek | Pass? |
|---|-----|-------|
| E1 | Hanya pimpinan + admin (tab visible) | ☐ |
| E2 | Verdict notice sesuai `recommendation` | ☐ |
| E3 | Meta sesi (progress, method, counts) | ☐ |
| E4 | Review summary box | ☐ |
| E5 | Daftar temuan ringkas + paginasi | ☐ |
| E6 | Export JSON download | ☐ |
| E7 | Export HTML download | ☐ |
| E8 | Otorisasi laporan (`report:authorize`) | ☐ |
| E9 | Block authorize jika masih pending review | ☐ |
| E10 | Audit trail panel | ☐ |
| E11 | Layout 2 kolom → stack ≤1100px | ☐ |

---

## 8. Fase F — Logic cross-cutting (frontend)

Audit **satu per satu** — centang jika perilaku verified.

### 8.1 `useSessionWorkspace.ts`

| # | Perilaku | Pass? |
|---|----------|-------|
| W1 | Resolve `?sesi=` (UUID penuh + prefix 8 char) | ☐ |
| W2 | Pilih sesi → sync URL | ☐ |
| W3 | Stream SSE sesi aktif / reconnect | ☐ |
| W4 | Pending count → toast/navigate findings | ☐ |
| W5 | Session list refresh | ☐ |

### 8.2 `useWorkspaceQueries.ts`

| # | Perilaku | Pass? |
|---|----------|-------|
| W6 | Fetch findings saat tab findings + sesi | ☐ |
| W7 | Fetch gallery saat tab gallery | ☐ |
| W8 | Fetch report saat tab report | ☐ |
| W9 | Fetch dashboard stats | ☐ |
| W10 | Dependency `reviewFilter` / `moduleFilter` / page | ☐ |
| W11 | 403 dashboard untuk operator → tidak crash | ☐ |

### 8.3 `useConsoleNavigation.ts`

| # | Perilaku | Pass? |
|---|----------|-------|
| W12 | `goToTab` bawa query sesi/filter/album/modul | ☐ |
| W13 | Landing tab per role | ☐ |
| W14 | URL deep-link buka tab + sesi langsung | ☐ |

### 8.4 Shared infra

| # | Area | Pass? |
|---|------|-------|
| W15 | `api.ts` / `endpoints.ts` — semua path `/api/v1` | ☐ |
| W16 | Auth header on protected routes | ☐ |
| W17 | `mediaFetchQueue` throttle | ☐ |
| W18 | `workflow.ts` step mapping vs status sesi | ☐ |

---

## 9. Fase F — Backend API per domain

Jalankan otomatis dulu, lalu spot-check manual via Swagger/curl.

```bash
cd backend && .venv/bin/python -m pytest -q
# Target: 315+ passed, skipped expected (GPU/torch)
```

| Router | File | Cek manual / pytest |
|--------|------|---------------------|
| health | `health.py` | `GET /health` → toolchain flags |
| auth | `auth.py` | login/logout/me; RBAC `test_rbac.py` |
| devices | `devices.py` | list devices (host vs docker) |
| sessions | `sessions.py` | CRUD, list pagination, cancel, zip |
| findings | `findings.py` | list, filter, review, bulk |
| gallery | `gallery.py` | albums, items, pagination |
| media | `media.py` | serve file, path traversal blocked |
| reports | `reports.py` | generate, export, authorize |
| dashboard | `dashboard.py` | aggregate stats per session |
| selection | `selection.py` | crawl selection contracts |
| agent | `agent.py` | bootstrap, artifact upload |
| admin | `admin.py` | admin-only ops |

**Keamanan:** `test_security.py` · `test_rbac.py` · path traversal media · auth on mutating routes

**Branding compat:** `test_branding_compat.py` — SATRIA vs SIKSIK MIME/session id

---

## 10. Fase F — Pipeline akuisisi & analisa

| # | Tahap | Verifikasi | Pass? |
|---|-------|------------|-------|
| P1 | Intake | ZIP / ADB / agent record accepted | ☐ |
| P2 | Orchestration | `orchestration.py` stages complete | ☐ |
| P3 | Indexing | Media indexed → gallery populated | ☐ |
| P4 | Analysis | Findings generated with category/source/layer | ☐ |
| P5 | Recommendation | `lulus` / `menunggu_review` / threat paths | ☐ |
| P6 | Review impact | Confirmed/rejected affects report authorize | ☐ |
| P7 | Module tags | Findings filterable by modul drill-down | ☐ |

**Test files:** `test_acquisition_modules.py` · `test_android_recovery.py` · `test_zip_upload.py` · `test_analysis_unit.py` · `test_recommendation.py`

---

## 11. Fase G — Regresi otomatis

| Suite | Perintah | Pass? |
|-------|----------|-------|
| Backend full | `cd backend && .venv/bin/python -m pytest -q` | ☐ |
| Frontend unit | `cd frontend && npm run test` | ☐ |
| Frontend build | `cd frontend && npm run build` | ☐ |
| E2E smoke | Stack jalan + `cd frontend && npx playwright test` | ☐ |
| Route smoke | `appRoutes.smoke.test.ts` | ☐ |

**E2E perluasan (rekomendasi):**

- [ ] Login per 4 role → assert tab set
- [ ] Analis: temuan filter + session picker visible full width
- [ ] Pimpinan: laporan tab + export button
- [ ] Deep link `/temuan?sesi=<id>&filter=pending`

---

## 12. Viewport & perangkat (regresi visual)

Uji **setiap tab** pada:

| Viewport | Perangkat |
|----------|-----------|
| 375×812 | Mobile |
| 768×1024 | Tablet |
| 1280×800 | Laptop |
| 1440×900 | Desktop lebar |

**Perhatian khusus:** session picker, filter row, KPI grid, finding cards, report aside, dash module grid.

---

## 13. Known limitations (jangan laporkan sebagai bug)

- WhatsApp DB penuh / TikTok crawl: **stub UI**, bukan fitur hidup
- Docker: **tanpa ADB live** — expected
- Operator memanggil dashboard untuk pending → **403 ditangkap**
- PyTorch optional di Docker — warning log aman
- PoC DB `backend/data/poc.db` — data lab, bukan produksi

---

## 14. Template sign-off per halaman

Salin blok ini per tab setelah audit:

```
Halaman: _______________
Auditor: _______________
Tanggal: _______________
Lingkungan: [ ] Host  [ ] Docker
Viewport: [ ] Mobile [ ] Tablet [ ] Desktop

UI/layout:     Pass / Fail — catatan:
Fungsi utama:  Pass / Fail — catatan:
Logic/state:   Pass / Fail — catatan:
RBAC:          Pass / Fail — catatan:
Responsif:     Pass / Fail — catatan:

Issue dibuka: #___ #___ 
```

---

## 15. Prioritas perbaikan (setelah audit)

| Prioritas | Kriteria |
|-----------|----------|
| **P0** | Crash, data loss, RBAC bypass, akuisisi tidak jalan |
| **P1** | Fitur utama broken (review, export, session picker) |
| **P2** | UX/responsif, copy misleading, performance |
| **P3** | Polish CSS, dead code, docs outdated |

**Docs yang perlu sync setelah audit:** `PAGES.md` (hapus referensi readiness/banner lama), `RUNNING.md`, `ARCHITECTURE.md`

---

## 16. Referensi file kunci

| Area | Path |
|------|------|
| Shell | `frontend/src/app/AppShell.tsx` |
| Composer | `frontend/src/app/hooks/useConsoleApp.ts` |
| Console hooks | `frontend/src/app/hooks/console/*` |
| Routes | `frontend/src/app/routes.ts` |
| API client | `frontend/src/shared/api/endpoints.ts` |
| Backend entry | `backend/app/main.py` |
| API v1 | `backend/app/api/v1/*` |
| Acquisition | `backend/app/acquisition/orchestration.py` |
| RBAC | `backend/app/services/auth.py` |
| Running guide | `docs/RUNNING.md` |
| Page checklist (legacy) | `docs/PAGES.md` |
| **Hardening produksi** | [`docs/HARDENING.md`](HARDENING.md) |
