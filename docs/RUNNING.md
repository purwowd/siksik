# Menjalankan SATRIA — Docker & non-Docker

> **Setup lengkap (instalasi, user admin, web, desktop):** [`SETUP.md`](SETUP.md)

Panduan singkat menjalankan konsol web + API PoC SATRIA di laptop lab.

---

## Ringkasan mode

| | **Docker Compose** | **Host lab (non-Docker)** |
|---|-------------------|---------------------------|
| **Cocok untuk** | Smoke UI, demo alur, review temuan | Akuisisi HP live (ADB/iOS), GPU CUDA |
| **UI** | http://127.0.0.1:5173 | http://127.0.0.1:5173 (`npm run dev`) |
| **API** | http://127.0.0.1:8000 | http://127.0.0.1:8000 (`python run.py`) |
| **ADB / USB** | Tidak (container terisolasi) | Ya, jika `adb` terpasang |
| **Upload ZIP** | Ya (jika `ZIP_ENABLED=1`) | Ya |
| **GPU OCR/Whisper** | Terbatas (image slim, tanpa CUDA) | Penuh dengan `requirements-gpu.txt` |
| **Runtime env** | `docker` (otomatis di compose) | `host` (default) |

---

## Prasyarat

### Docker

- Docker Desktop / Engine 24+
- Docker Compose v2
- Koneksi ke Docker Hub (pull `python:3.12-slim`, `node:22-alpine`, `nginx:1.27-alpine`)

### Host lab

- **Python 3.12+**
- **Node.js 20+** (frontend)
- **ffmpeg** (analisa media)
- Opsional: **adb**, **libimobiledevice**, **NVIDIA driver + CUDA** (GPU)

---

## A) Docker Compose

### 1. Build & jalankan

Dari root repo:

```bash
docker compose up --build
```

Services:

| Service | Port (loopback) | Keterangan |
|---------|-----------------|------------|
| `api` | 127.0.0.1:8000 | FastAPI + uvicorn |
| `ui` | 127.0.0.1:5173 | nginx → static build + proxy `/api` ke `api` |

Volume data persisten: `sadt-data` → `/app/data` di container API.

### 2. Login

Buka http://127.0.0.1:5173 dan masuk dengan akun instalasi (`setup_lab_panitia.py` atau `SATRIA_SEED_*_PASSWORD` pada first boot). Jangan memakai sandi seed di jaringan produksi. Lihat [`RUNBOOK.md`](./RUNBOOK.md).

### 3. Verifikasi cepat

```bash
curl -s http://127.0.0.1:8000/api/v1/health | head -c 200
curl -s http://127.0.0.1:8000/api/v1/auth/roles
```

Di UI: banner runtime **docker** muncul; tab sesuai role setelah login.

### 4. Stop & bersihkan

```bash
# Stop container
docker compose down

# Stop + hapus volume data (reset DB/sesi)
docker compose down -v
```

### 5. Troubleshooting Docker

**Gagal pull image (`EOF` / `failed to resolve metadata`):**

```bash
docker pull python:3.12-slim
docker pull node:22-alpine
docker pull nginx:1.27-alpine
docker compose up --build
```

- Restart Docker Desktop
- Matikan VPN/proxy sementara
- `docker login` (hindari rate limit anonim)

**API 500 di `/sessions`:** rebuild image setelah pull git terbaru (`pages_total` fix di `sessions.py`).

**Perubahan kode backend/UI:** `docker compose up --build` ulang.

---

## B) Host lab (non-Docker)

### 1. Environment

```bash
cp .env.example backend/.env
# Edit backend/.env sesuai preset (CPU Mac vs GPU workstation)
```

Prefix env: **`SATRIA_*`** (preferensi) dengan fallback **`SADT_*`**.

Minimal:

```env
SATRIA_API_HOST=127.0.0.1
SATRIA_API_PORT=8000
SATRIA_RUNTIME_ENV=host
SATRIA_ZIP_ENABLED=1
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt

# Opsional dev + GPU:
# pip install -r requirements-dev.txt -r requirements-gpu.txt

python run.py --reload
# atau dengan flag GPU preset:
# python run.py --reload --gpu
```

API listen: http://127.0.0.1:8000

Health: http://127.0.0.1:8000/api/v1/health

### 3. Frontend

Terminal terpisah:

```bash
cd frontend
npm install
npm run dev
```

UI: http://127.0.0.1:5173 — Vite proxy `/api` → port 8000 (atur `SATRIA_API_PORT` jika API beda port).

### 4. Build production (tanpa Docker)

```bash
cd frontend && npm run build
# Serve dist/ dengan nginx/apache, proxy /api ke backend
```

### 5. Toolchain perangkat (opsional)

**Android:**

```bash
adb devices   # harus "device" + authorized
```

**iOS (opsional):**

```bash
idevice_id -l
```

Tanpa perangkat: gunakan **upload ZIP** di tab Penerimaan.

### 6. GPU (opsional)

```bash
nvidia-smi
cd backend && source .venv/bin/activate
pip install -r requirements-gpu.txt
# Torch CUDA — sesuaikan wheel dengan driver:
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
python run.py --reload --gpu
```

Setelah OCR/Whisper aktif pertama kali: `POST /api/v1/admin/clear-hash-cache` (role admin).

---

## C) Tes otomatis

```bash
# Backend (dari backend/, venv aktif)
python -m pytest -q

# Frontend unit/smoke routes
cd frontend && npm run test

# E2E browser (butuh Chromium)
cd frontend
npx playwright install chromium
npm run test:e2e
# Pastikan UI/API sudah jalan (compose atau dev server)
```

---

## D) Peta URL konsol

| Tab | Path | Role tipikal |
|-----|------|--------------|
| Penerimaan | `/penerimaan` | operator |
| Temuan | `/temuan` | analis, admin |
| Galeri | `/galeri` | analis, admin |
| Laporan | `/laporan` | pimpinan, admin |
| Ikhtisar | `/ikhtisar` | analis, pimpinan, admin |

Query umum: `?sesi=<uuid>&filter=pending&modul=gallery`

Alias: `/operator` → `/penerimaan`, `/dasbor` → `/ikhtisar`.

Detail per halaman: [`docs/PAGES.md`](PAGES.md).

---

## E) Kapan pakai mode apa?

| Kebutuhan | Mode |
|-----------|------|
| Demo UI / training operator | Docker |
| CI smoke / onboarding dev | Docker |
| Forensik HP USB live | Host lab |
| OCR/Whisper GPU penuh | Host lab + CUDA |
| Agent Android loopback | Host lab |

Lihat juga: [`LAB_HOST.md`](LAB_HOST.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`HARDENING.md`](HARDENING.md)

---

## F) Hardening pilot (TLS, backup, password)

### Setup lab panitia (password + CORS + tanpa sim)

```bash
cd backend
python scripts/setup_lab_panitia.py
# opsional origin LAN:
python scripts/setup_lab_panitia.py --cors-extra https://10.0.0.5:8443
```

Menulis `backend/.env`, rotate password di DB, credentials di `backend/data/lab-panitia-credentials.txt`.

Preset saja (tanpa generate password): `python scripts/apply_env_preset.py lab.panitia`

### Dummy data (tanpa HP)

```bash
cd backend
python scripts/seed_dummy_data.py --quick --cancel-active
python scripts/seed_dummy_data.py --pending-pure --cancel-active   # antrean 100% pending
python scripts/seed_dummy_data.py --full --cancel-active   # + sesi iOS / laporan
# opsional: --review-one (konfirmasi 1 temuan di sesi #2)
```

Butuh tidak ada sesi aktif. Simulasi tidak mengubah `LAB_DEMO_MODE` di `.env`.

### Backup harian DB + staging

```bash
cd backend
.venv/bin/python scripts/backup_lab_data.py --dest ../backups --keep 14
```

Cron contoh: lihat docstring di `backend/scripts/backup_lab_data.py`.

### Rotate password lab

Set env di `.env`, lalu:

```bash
cd backend
export SATRIA_SEED_ADMIN_PASSWORD='…'
# … role lain …
.venv/bin/python scripts/rotate_lab_passwords.py --from-env
```

Atau interaktif: `python scripts/rotate_lab_passwords.py --user admin`

### Docker + TLS (loopback)

```bash
chmod +x deploy/gen-self-signed-cert.sh
./deploy/gen-self-signed-cert.sh deploy/certs
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
# UI/API: https://127.0.0.1:8443
```

Sesuaikan `SADT_CORS_ORIGINS` di `docker-compose.prod.yml` sebelum expose ke LAN.

### E2E workflow (review + authorize)

Backend + `npm run dev` harus hidup:

```bash
cd frontend
PLAYWRIGHT_BASE_URL=http://127.0.0.1:5173 npx playwright test
```

---

## G) Desktop (Tauri)

Ringkas — detail: [`DESKTOP.md`](DESKTOP.md) · setup penuh: [`SETUP.md`](SETUP.md)

```bash
cd backend && source .venv/bin/activate && pip install -r requirements.txt
cd ../desktop && npm install && ./dev.sh
```

| Mode | UI | API |
|------|-----|-----|
| Dev desktop | `:5175` (Vite) | `:8000` (auto-spawn) |
| Prod installer | `:8765` (embedded) | same process |

Login sama dengan web. Export PDF butuh Google Chrome terpasang.
