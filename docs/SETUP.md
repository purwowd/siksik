# SATRIA — Setup & Menjalankan (Web + Desktop)

Panduan **satu pintu** untuk instalasi pertama, akun pengguna, menjalankan konsol **web**, dan aplikasi **desktop** (Tauri).

| Dokumen terkait | Isi |
|-----------------|-----|
| [`PROGRESS_REPORT.md`](PROGRESS_REPORT.md) | Status progress, hasil uji, agenda optimasi reasoning |
| [`RUNNING.md`](RUNNING.md) | Perbandingan Docker vs host, troubleshooting jaringan |
| [`DESKTOP.md`](DESKTOP.md) | Detail arsitektur Tauri, export PDF, port desktop |
| [`LAB_HOST.md`](LAB_HOST.md) | ADB/USB, iOS, GPU host |
| [`PAGES.md`](PAGES.md) | Cek UI per tab & role |
| [`HARDENING.md`](HARDENING.md) | TLS, backup, produksi |

---

## 1. Prasyarat

| Komponen | Web (dev) | Web (Docker) | Desktop (dev) | Host GPU / ADB |
|----------|-----------|--------------|---------------|----------------|
| **Python** | 3.12+ | (di image) | 3.12+ venv | 3.12+ |
| **Node.js** | 20+ | (di image) | 20+ | 20+ |
| **Rust** | — | — | stable (`rustup`) | — |
| **ffmpeg** | disarankan | di image | disarankan | wajib analisa media |
| **adb** | opsional | tidak | opsional | wajib Android live |
| **NVIDIA CUDA** | opsional | tidak | opsional | disarankan workstation |

```bash
# Cek cepat
python3 --version    # ≥ 3.12
node --version       # ≥ 20
ffmpeg -version      # opsional tapi disarankan
adb devices          # opsional — host lab Android
nvidia-smi           # opsional — GPU
rustc --version      # hanya untuk desktop build
```

---

## 2. Instalasi pertama (clone → siap jalan)

Dari **root repository**:

```bash
# 1) Backend Python
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
pip install -r requirements-dev.txt   # opsional — pytest

# 2) Environment
cp ../.env.example .env              # atau preset lab (§4)

# 3) Frontend
cd ../frontend
npm install

# 4) Desktop (opsional)
cd ../desktop
npm install
# Rust sekali saja:
# curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh && . "$HOME/.cargo/env"
```

**GPU (opsional, host NVIDIA):**

```bash
cd backend && source .venv/bin/activate
pip install -r requirements-gpu.txt
# Torch CUDA — sesuaikan dengan driver (contoh cu124):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
python scripts/apply_env_preset.py gpu.8gb   # backup .env → .env.bak
```

---

## 3. Konfigurasi environment

File aktif: **`backend/.env`** (salin dari root `.env.example`).

Prefix env: **`SATRIA_*`** (preferensi) dengan fallback **`SADT_*`**.

### Minimal (lab CPU / Mac)

```env
SATRIA_API_HOST=127.0.0.1
SATRIA_API_PORT=8000
SATRIA_ZIP_ENABLED=1
SATRIA_RUNTIME_ENV=host
```

### Preset siap pakai

```bash
cd backend
python scripts/apply_env_preset.py --list
python scripts/apply_env_preset.py mac.lab      # Mac / CPU + EasyOCR
python scripts/apply_env_preset.py gpu.8gb      # workstation NVIDIA (~8 GB VRAM)
python scripts/apply_env_preset.py lab.panitia  # host panitia (tanpa sim dummy)
```

| Preset | File | Kapan |
|--------|------|-------|
| Mac lab | `env/mac.lab.env` | Laptop Apple, tanpa CUDA |
| GPU 6/8/12 GB | `env/gpu.*.env` | Workstation NVIDIA |
| Lab panitia | `env/lab.panitia.env` | Demo resmi, sim off |
| Demo cepat | `env/gpu.demo-fast.env` | Presentasi singkat |

---

## 4. Akun pengguna & password

### 4.1 Akun default (DB kosong)

Saat **pertama kali** API start dan tabel `users` kosong, sistem membuat 4 akun seed (`operator`, `analis`, `pimpinan`, `admin`).

Sandi seed **wajib** di-set lewat env sebelum first boot produksi:

```env
SATRIA_SEED_OPERATOR_PASSWORD=...
SATRIA_SEED_ANALIS_PASSWORD=...
SATRIA_SEED_PIMPINAN_PASSWORD=...
SATRIA_SEED_ADMIN_PASSWORD=...
```

Produksi panitia: `python scripts/setup_lab_panitia.py` lalu simpan file kredensial lokal (gitignored). Rotasi: `python scripts/rotate_lab_passwords.py --from-env`. Lihat juga [`RUNBOOK.md`](./RUNBOOK.md).

> **Penting:** Password seed **hanya dipakai saat DB kosong**. Jika DB sudah ada, ubah password lewat script rotate (§4.3).

### 4.2 Setup lab panitia (password acak — disarankan)

Generate `.env` + password kuat + file kredensial:

```bash
cd backend
python scripts/setup_lab_panitia.py

# Opsional — tambah origin CORS untuk LAN/TLS:
python scripts/setup_lab_panitia.py --cors-extra https://10.0.0.5:8443
```

**Output:**

| File | Isi |
|------|-----|
| `backend/.env` | Preset panitia + password seed |
| `backend/data/lab-panitia-credentials.txt` | Username + password (gitignored) |
| `deploy/env/docker-panitia.generated.env` | Env untuk Docker prod |

Baca kredensial:

```bash
cat backend/data/lab-panitia-credentials.txt
```

### 4.3 Rotate / ganti password (DB sudah ada)

**Dari env** (set password baru di `.env` dulu):

```bash
cd backend
export SATRIA_SEED_ADMIN_PASSWORD='password-baru-admin'
export SATRIA_SEED_OPERATOR_PASSWORD='...'
# ... role lain ...
.venv/bin/python scripts/rotate_lab_passwords.py --from-env
```

**Satu user interaktif:**

```bash
cd backend
.venv/bin/python scripts/rotate_lab_passwords.py --user admin
```

### 4.4 Reset total (hapus semua sesi & user)

```bash
# Hentikan API dulu, lalu:
rm -f backend/data/sadt.db backend/data/sadt.db-wal backend/data/sadt.db-shm
# Start API lagi → seed user dibuat ulang dari .env
```

Atau Docker: `docker compose down -v` (hapus volume).

---

## 5. Menjalankan — Web (browser)

### 5.1 Dev — dua terminal (disarankan)

```bash
# Terminal 1 — API
cd backend && source .venv/bin/activate
python run.py --reload --host 127.0.0.1 --port 8000
# GPU: python run.py --reload --gpu

# Terminal 2 — UI
cd frontend && npm run dev
```

| Service | URL |
|---------|-----|
| **Konsol web** | http://127.0.0.1:5173 |
| **API docs** | http://127.0.0.1:8000/docs |
| **Health** | http://127.0.0.1:8000/api/v1/health |

Login → pilih role → tab sesuai RBAC.

### 5.2 Dev — satu perintah (Mac lab)

```bash
bash scripts/start_poc.sh
# API :8000 + UI :5173, watchdog auto-restart API
```

### 5.3 Docker (smoke / demo tanpa ADB)

```bash
docker compose up --build
# UI http://127.0.0.1:5173 · API http://127.0.0.1:8000
```

**Batasan Docker:** tidak ada USB/ADB; gunakan **upload ZIP** di tab Penerimaan.

Stop + reset data: `docker compose down -v`

### 5.4 Build production (static UI)

```bash
cd frontend && npm run build    # → frontend/dist/
cd ../backend && source .venv/bin/activate
# Layani dist via nginx atau set SATRIA_DESKTOP_UI=1 + path dist
```

Detail TLS/nginx: [`HARDENING.md`](HARDENING.md) · `deploy/nginx/`

---

## 6. Menjalankan — Desktop (Tauri)

Shell desktop memakai **`frontend/` + `backend/` yang sama** — tidak ada salinan UI terpisah.

### 6.1 Prasyarat desktop

```bash
# Rust (sekali)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
. "$HOME/.cargo/env"

cd desktop && npm install
cd ../backend && source .venv/bin/activate && pip install -r requirements.txt
```

### 6.2 Dev desktop

```bash
cd desktop
./dev.sh
# atau: npm run dev
```

| Port | Service |
|------|---------|
| **5175** | Vite UI (khusus desktop — tidak bentrok web :5173) |
| **8000** | FastAPI (auto-spawn jika belum jalan) |

> Jangan `cargo run` langsung — pakai `./dev.sh` agar Vite `:5175` aktif.

**Backend manual (opsional):**

```bash
cd backend && .venv/bin/python run.py --reload --host 127.0.0.1 --port 8000
```

### 6.3 Build installer

```bash
cd frontend && npm run build
cd ../desktop && npm run build
```

Artefak:

- **macOS:** `desktop/src-tauri/target/release/bundle/macos/SATRIA.app`
- **Windows:** `.../bundle/msi/`
- **Linux:** `.../bundle/deb/` atau AppImage

Mode produksi desktop: satu port **8765** (`SATRIA_DESKTOP_UI=1`).

### 6.4 Export PDF di desktop

- Butuh **Google Chrome** terpasang (headless print)
- Dialog Simpan native — nama default: `SATRIA_{no-peserta}_{nama}_{datetime}.pdf`
- Troubleshooting: [`DESKTOP.md`](DESKTOP.md)

---

## 7. Data demo (tanpa HP)

```bash
cd backend && source .venv/bin/activate

# Hapus dummy lama + buat 6 kasus calon ASN
python scripts/seed_dummy_data.py --purge --rich

# Variasi:
python scripts/seed_dummy_data.py --purge --quick
python scripts/seed_dummy_data.py --purge --full --review-one
```

Model: **1 calon · 1 perangkat · 1 sesi · N temuan**.  
Tidak mengubah `LAB_DEMO_MODE` di `.env`.

---

## 8. Verifikasi setelah setup

```bash
# 1) Login API
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<sandi-instalasi>"}' | python3 -m json.tool

# 2) Health (ganti TOKEN)
curl -s http://127.0.0.1:8000/api/v1/health -H "Authorization: Bearer TOKEN"

# 3) Roles publik
curl -s http://127.0.0.1:8000/api/v1/auth/roles

# 4) Tes backend
cd backend && .venv/bin/python -m pytest -m "unit or api" -q

# 5) Tes frontend
cd frontend && npm run test && npm run build
```

**Checklist UI:**

1. Login admin → semua 5 tab terlihat
2. Login operator → hanya tab Penerimaan
3. Operator: isi identitas → ZIP atau sim → pipeline selesai
4. Analis: review temuan → rekomendasi berubah
5. Pimpinan: Laporan → export PDF/HTML → Sahkan

---

## 9. Peta URL konsol

| Tab | Path | Role |
|-----|------|------|
| Penerimaan | `/penerimaan` | operator, admin |
| Temuan | `/temuan` | analis, pimpinan, admin |
| Galeri | `/galeri` | analis, pimpinan, admin |
| Laporan | `/laporan` | pimpinan, admin (+ analis baca) |
| Ikhtisar | `/ikhtisar` | analis, pimpinan, admin |

Query: `?sesi=<uuid>&filter=pending&modul=gallery`

Alias: `/operator` → `/penerimaan`, `/dasbor` → `/ikhtisar`.

---

## 10. Troubleshooting umum

| Gejala | Solusi |
|--------|--------|
| Login gagal | Cek password di `lab-panitia-credentials.txt` atau rotate ulang |
| DB locked / error aneh | Stop API, hapus `-wal`/`-shm`, restart |
| Port 8000 dipakai | `lsof -ti:8000 \| xargs kill` atau ganti `SATRIA_API_PORT` |
| Perangkat kosong di Operator | Normal di Docker; host: `adb devices` |
| Sim tidak muncul | Set `SATRIA_LAB_DEMO_MODE=1` atau pakai seed dummy |
| Desktop blank / JSON | Tutup app, jalankan `./dev.sh` (bukan cargo run langsung) |
| Desktop freeze saat PDF | Update ke versi terbaru; Chrome harus terpasang |
| OCR lambat di Mac | Expected — pakai preset `mac.lab` atau host GPU |

---

## 11. Alur operasi singkat (panitia)

```
Operator: Identitas peserta → Live/ZIP → Jalankan
    ↓
Analis: Temuan → Konfirmasi / Tolak (bulk OK)
    ↓
Pimpinan: Laporan → Export → Sahkan
```

Satu sesi aktif per waktu (global lock). Retry calon gagal: no. peserta sama diizinkan jika sesi sebelumnya **failed/cancelled**.
