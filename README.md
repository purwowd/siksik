# SADT PoC — Sistem Analisis Digital Terpadu

Proof of Concept: akuisisi selektif + analisis bertingkat (gallery-first) di **satu workstation/server NVIDIA GPU**, satu sesi aktif per waktu.

**Target setup GPU:** Linux (Ubuntu/Debian) **atau** Windows 10/11 + NVIDIA. Apple Silicon/Mac = lab CPU (EasyOCR), bukan host CUDA penuh.

## Stack

| Layer | Teknologi |
|-------|-----------|
| Backend | Python FastAPI, aiosqlite (WAL), pipeline async |
| Frontend | React 19 + Vite + TypeScript (Node 20+) |
| Akuisisi | ADB / libimobiledevice **atau** upload ZIP (opsional) |
| GPU stack | SafeWatch · ICM-Assistant · Qwen2.5-VL · Whisper · PaddleOCR |

**PoC “cukup jalan” di GPU:** OCR (PaddleOCR) + Whisper + Pillow/lexicon. SafeWatch/ICM/Qwen = **opsional** (butuh weight/plugin terpisah).

---

## Menjalankan di laptop/server GPU

### Ringkas checklist setelah install

1. `nvidia-smi` → driver OK
2. `python run.py --gpu` (atau set env setara)
3. Login admin → `GET /health` → `gpu_available`, `vision.ocr.available`, Whisper OK
4. `POST /admin/clear-hash-cache` (sekali setelah nyalakan OCR/Whisper pertama kali)
5. Analisa **FULL** (ADB live atau ZIP) → Temuan → analis **Konfirmasi/Tolak** (status **MENUNGGU REVIEW** sampai semua temuan direview; **TIDAK LULUS** hanya setelah confirm)

---

### A) Linux (Ubuntu / Debian) — host NVIDIA

#### 1. Prasyarat host

```bash
nvidia-smi   # driver + CUDA runtime harus terlihat

sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg adb git curl
# Frontend: Node 20+ (nodejs.org atau nvm)
# Opsional iOS:
# sudo apt install -y libimobiledevice-utils ideviceinstaller
```

#### 2. Clone & Python env

```bash
git clone <repo-sadt> aiai && cd aiai/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt -r requirements-gpu.txt

# Torch CUDA — sesuaikan indeks dengan CUDA driver (contoh 12.4):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

Cek GPU PyTorch:

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)"
```

#### 3. Env GPU

```bash
cp ../.env.example .env   # root repo → backend/.env
# edit .env (lihat blok "Env minimum GPU" di bawah)
```

#### 4. Start API + UI

```bash
# Terminal 1 — API
cd backend && source .venv/bin/activate
python run.py --host 127.0.0.1 --port 8000 --gpu

# Terminal 2 — UI
cd frontend && npm install && npm run dev -- --host 127.0.0.1 --port 5173
```

UI: http://127.0.0.1:5173

**Route UI:** `/operator` · `/temuan` · `/laporan` · `/dasbor` — query `?sesi=<uuid|8char>&filter=pending` untuk share sesi aktif.

---

### B) Windows 10/11 — laptop NVIDIA

#### 1. Prasyarat host

- Install **NVIDIA Game Ready / Studio driver** → PowerShell: `nvidia-smi`
- **Python 3.11+** (python.org, centang “Add to PATH”)
- **Git**, **Node.js 20+**, **ffmpeg** (di PATH), **ADB platform-tools** (di PATH)
- Visual C++ Redistributable (sering dibutuhkan Torch/Paddle)

Contoh opsional (Chocolatey):

```powershell
choco install python git nodejs-lts ffmpeg adb -y
```

#### 2. Clone & venv (PowerShell)

```powershell
git clone <repo-sadt> aiai
cd aiai\backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt -r requirements-gpu.txt

# Torch CUDA (sesuaikan cu124 / cu121 dengan driver):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

Cek:

```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)"
```

> **Catatan Paddle:** `paddlepaddle-gpu` di `requirements-gpu.txt` di-skip di macOS. Di Windows/Linux NVIDIA, jika `pip` gagal resolve wheel GPU, ikuti [install PaddlePaddle](https://www.paddlepaddle.org.cn/install/quick) untuk versi CUDA Anda, lalu `pip install paddleocr`.

#### 3. Env GPU

```powershell
copy ..\.env.example .env
notepad .env
```

#### 4. Start API + UI (dua jendela PowerShell)

```powershell
# API
cd aiai\backend
.\.venv\Scripts\Activate.ps1
python run.py --host 127.0.0.1 --port 8000 --gpu

# UI
cd aiai\frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

ADB USB: developer options + USB debugging; `adb devices` harus menampilkan perangkat.

---

### Preset `.env` per VRAM (`backend/env/`)

| File | VRAM | Kapan pakai |
|------|------|-------------|
| `env/mac.lab.env` | CPU (Mac) | Lab UI / EasyOCR |
| `env/gpu.6gb.env` | 4–6 GB | RTX 3050/4050 — Whisper `tiny`, worker 4 |
| `env/gpu.8gb.env` | ~8 GB | **Rekomendasi panitia** — Whisper `base`, cap FULL 5000 |
| `env/gpu.12gb.env` | ≥10–12 GB | Whisper `small`, concurrency 8 |
| `env/gpu.demo-fast.env` | GPU apa saja | Demo singkat — OCR selektif, QUICK |

```bash
cd backend
python scripts/apply_env_preset.py --list
python scripts/apply_env_preset.py gpu.8gb   # backup .env → .env.bak
python run.py --reload --host 127.0.0.1 --port 8000 --gpu
# lalu sekali: POST /admin/clear-hash-cache
```

Cek VRAM: `nvidia-smi`. Tanpa mengetahui VRAM, mulai dari **`gpu.8gb`**; OOM → turun ke `gpu.6gb`.

### Env minimum GPU (`backend/.env`)

```bash
SADT_API_HOST=127.0.0.1
SADT_API_PORT=8000

# Core media text (wajib untuk demo screenshot/video)
SADT_MEDIA_TEXT_ENABLED=1
SADT_OCR_ENABLED=1
SADT_OCR_BACKEND=paddleocr
SADT_OCR_GPU=1
SADT_OCR_LANGS=en
SADT_OCR_FULL_GALLERY=1

SADT_GPU_WHISPER_ENABLED=1
SADT_GPU_WHISPER_MODEL=base
SADT_GPU_WHISPER_LANG=id

# Full stack MLLM — opsional; tanpa weight, bridge/heuristic tetap jalan
SADT_GPU_STACK_ENABLED=1
# SADT_GPU_SAFEWATCH_MODEL=C:\models\safewatch
# SADT_GPU_ICM_MODEL=zhaoyuzhi/ICM-LLaVA-v1.5-7B
# SADT_GPU_QWEN_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

SADT_ZIP_ENABLED=1
SADT_ZIP_MAX_MB=512
SADT_LAB_DEMO_MODE=0
```

`python run.py --gpu` otomatis meng-set stack OCR/Whisper on (tetap boleh isi `.env` eksplisit).

### Verifikasi health

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@2026"}' | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s http://127.0.0.1:8000/api/v1/health -H "Authorization: Bearer $TOKEN"
# Cek: gpu_available=true, vision.ocr.available=true, analysis_engine mengandung ocr=1

curl -s -X POST http://127.0.0.1:8000/api/v1/admin/clear-hash-cache \
  -H "Authorization: Bearer $TOKEN"
```

Password default lab: lihat `.env.example` — ganti sebelum dipakai di luar localhost.

### Model map

| Role | Model | Env | Wajib PoC? |
|------|-------|-----|------------|
| OCR | PaddleOCR | `SADT_OCR_BACKEND=paddleocr` | **Ya** |
| Audio / lirik | Whisper | `SADT_GPU_WHISPER_MODEL` | **Ya** (video) |
| Video moderation | SafeWatch | `SADT_GPU_SAFEWATCH_MODEL` / plugin | Opsional |
| Image moderation | ICM-Assistant | `SADT_GPU_ICM_MODEL` / plugin | Opsional |
| Reasoning | Qwen2.5-VL | `SADT_GPU_QWEN_MODEL` / plugin | Opsional |

Refs: [SafeWatch](https://safewatch-aiguard.github.io/) · [ICM-Assistant](https://github.com/zhaoyuzhi/icm-assistant) · Qwen2.5-VL · openai-whisper · PaddleOCR

### Acceptance di host GPU

```bash
cd backend
# Linux: source .venv/bin/activate
# Windows: .\.venv\Scripts\Activate.ps1

pip install -r requirements-gpu.txt
SADT_REQUIRE_GPU=1 python scripts/run_acceptance.py --perf --require-gpu

# Linux helper:
# bash scripts/deploy_gpu.sh

pytest -m "gpu_ocr or gpu_whisper" -q --tb=short
```

---

## Analisa dari ZIP (opsional — tanpa akuisisi live)

Gunakan jika dump sudah diambil di mesin lain (`adb pull` → zip), lalu dianalisis di workstation GPU.

### Siapkan ZIP

```bash
adb pull /sdcard/DCIM ./dump/DCIM
adb pull /sdcard/Pictures ./dump/Pictures
adb pull /sdcard/Download ./dump/Download
cd dump && zip -r ../adb_media.zip .
```

Struktur yang didukung:

- Folder `DCIM` / `Pictures` / `Download` / `Movies`, atau
- Flat file `.jpg/.mp4/...`, atau
- Sudah terklasifikasi `gallery/` `video/` `documents/`

### Via UI

1. Login sebagai **operator** / **admin**
2. Tab **Operator** → Sumber analisa → **Upload ZIP hasil ADB**
3. Pilih file `.zip` → mode QUICK/FULL → **Analisa ZIP**

### Via API

```bash
TOKEN=...
curl -X POST http://127.0.0.1:8000/api/v1/sessions/from-zip \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@adb_media.zip" \
  -F "mode=quick" \
  -F "label=Dump unit A"
```

Nonaktifkan: `SADT_ZIP_ENABLED=0`.

---

## Menjalankan cepat (lab tanpa CUDA — Mac / CPU)

Cocok untuk UI + ADB + EasyOCR CPU (lambat). **Bukan** pengganti host NVIDIA untuk PoC full.

```bash
# backend/.env contoh Mac:
# SADT_OCR_ENABLED=1
# SADT_OCR_BACKEND=easyocr
# SADT_OCR_GPU=0

bash scripts/start_poc.sh
# atau:
#   backend: cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
#   frontend: cd frontend && npm run dev
```

---

## Test

```bash
cd backend
# Linux: source .venv/bin/activate
# Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -m "unit or api" -q
```

| Marker | Isi |
|--------|-----|
| `unit` | L1–L3 / lexicon / hash_cache |
| `api` | endpoint + sesi + ZIP |
| `acceptance` | gate deploy |
| `perf` | SLA pipeline |
| `gpu` | nvidia-smi / torch |
| `gpu_ocr` | PaddleOCR/EasyOCR nyata |
| `gpu_whisper` | Whisper ASR nyata |

### Gate media text di mesin GPU

```bash
cd backend && source .venv/bin/activate
pip install -r requirements.txt -r requirements-gpu.txt
# + torch CUDA sesuai host

export SADT_OCR_ENABLED=1 SADT_OCR_BACKEND=paddleocr SADT_OCR_GPU=1
export SADT_MEDIA_TEXT_ENABLED=1
export SADT_GPU_WHISPER_ENABLED=1 SADT_GPU_WHISPER_MODEL=base SADT_GPU_WHISPER_LANG=id

pytest -m unit -q --tb=short -k "lexicon or media_text or ocr"
pytest -m "gpu_ocr or gpu_whisper" -q --tb=short
```

---

## Endpoint utama

| Method | Path | Keterangan |
|--------|------|------------|
| GET | `/api/v1/health` | status + GPU stack (auth) |
| GET | `/api/v1/devices` | perangkat live |
| POST | `/api/v1/sessions` | akuisisi live + analisa |
| POST | `/api/v1/sessions/from-zip` | analisa dari ZIP (opsional) |
| GET | `/api/v1/sessions/{id}` | progress |
| GET | `/api/v1/sessions/{id}/findings` | temuan |
| GET | `/api/v1/sessions/{id}/media?path=` | preview foto/video staging (auth) |
| PATCH | `/api/v1/findings/{id}` | review (confirm/reject → rekomendasi dihitung ulang) |
| GET | `/api/v1/sessions/{id}/risk-timeline` | timeline risiko 5 tahun |
| GET | `/api/v1/dashboard` | agregat (+ `?session_id=`) |
| POST | `/api/v1/admin/clear-hash-cache` | invalidate cache enrichment (admin) |
| POST | `/api/v1/admin/recompute-recommendations` | hitung ulang status LULUS/MENUNGGU REVIEW/TIDAK LULUS (admin) |

**Rekomendasi sesi (tiga status):**
- **`MENUNGGU REVIEW`** — ada temuan `pending`, belum diverifikasi analis
- **`TIDAK LULUS`** — minimal satu finding **`confirmed`**
- **`LULUS`** — tidak ada temuan, atau semua temuan sudah `rejected`

### GPU stack wiring

Urutan backend MLLM: **plugin** → **weights/HF** → **bridge**. Untuk PoC, Whisper + PaddleOCR tetap yang utama.

```bash
SADT_GPU_SAFEWATCH_PLUGIN=my_pkg.safewatch_adapter
# atau: {SADT_GPU_SAFEWATCH_MODEL}/sadt_adapter.py
SADT_GPU_ICM_MODEL=Salesforce/blip-image-captioning-base
SADT_GPU_QWEN_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
```

## RBAC

| Role | Login | Akses |
|------|-------|--------|
| operator | `operator` | Akuisisi / ZIP |
| analis | `analis` | Dasbor, review |
| pimpinan | `pimpinan` | Laporan, sahkan |
| admin | `admin` | Semua |

Password default lab di `.env.example` — override `SADT_SEED_*_PASSWORD`. Hash: **bcrypt**. API default **127.0.0.1**.

## Docker (opsional)

```bash
docker compose up --build
```

Prefer bare-metal GPU untuk Whisper/OCR/MLLM; Compose cocok smoke API/UI.

## Fokus PoC: GALERI

- ADB: `DCIM` / `Pictures` / screenshot (+ foto chat opsional)
- ZIP: dump media setara
- L3/L4: Pillow + **media text** (OCR + Whisper + keyframe) + GPU stack opsional
- Matching lexicon: **word-boundary**; FULL + `SADT_OCR_FULL_GALLERY=1` OCR gallery/documents
- Hash cache fingerprint — setelah pasang OCR/Whisper, clear cache admin / otomatis miss
- Byte mentah JPEG **tidak** di-scan sebagai teks L1 (hindari FP `bom`)
- `msgstore.db` WhatsApp: **belum** masuk ruang lingkup

Demo simulator: default **OFF** (`SADT_LAB_DEMO_MODE=1` untuk aktifkan).

### Media text — latency & kecepatan

| Tahap | Per item (order of magnitude) | Catatan |
|-------|-------------------------------|---------|
| Pillow L3 | ~5–30 ms / foto | Murah |
| OCR 1 gambar (GPU) | ~50–300 ms | Cold-start lebih lama |
| OCR 1 gambar (CPU) | ~0.3–2 s | Mac lab |
| ffmpeg keyframe | ~0.5–3 s / video | N=`SADT_VIDEO_OVERLAY_KEYFRAMES` |
| Whisper `base` (GPU) | ~0.5–3× durasi audio | |
| Whisper `base` (CPU) | real-time × beberapa | |
| MLLM (SafeWatch/ICM/Qwen) | detik–puluhan detik | Opsional + checkpoint |

**QUICK** ~800 foto / ~80 video. **FULL** ~3000 foto + semua video (bottleneck Whisper/ffmpeg).

### Knob tuning (`backend/.env`)

| Env | Default | Fungsi |
|-----|---------|--------|
| `SADT_IMAGE_CAP_QUICK` / `FULL` | 800 / 3000 | Batas foto galeri dianalisa |
| `SADT_VIDEO_CAP_QUICK` / `FULL` | 80 / 0 | Batas video (0 = tanpa batas FULL) |
| `SADT_WORKER_CONCURRENCY` | 4 (6 via `--gpu`) | Paralel analisa |
| `SADT_OCR_MAX_EDGE_PX` | 1600 | Resize sebelum OCR (0 = asli) |
| `SADT_OCR_SHARPEN` | 1 | Autocontrast + unsharp sebelum OCR |
| `SADT_VIDEO_WHISPER_MAX_DURATION_S` | 0 | Skip ASR video panjang (`0` = tanpa batas total) |
| `SADT_VIDEO_WHISPER_TRANSCRIBE_FIRST_S` | 120 | ASR hanya N detik pertama (kecepatan) |
| `SADT_VIDEO_OVERLAY_KEYFRAMES` | 5 | Keyframe + OCR on-screen per video |
| `SADT_OCR_FULL_GALLERY` | 1 | FULL: OCR semua foto galeri |
| `SADT_CLIP_TOKOH_ENABLED` | 1 | CLIP zero-shot tokoh/presiden (`pip install transformers`) |
| `SADT_CLIP_TOKOH_THRESHOLD` | 0.24 | Ambang skor CLIP |

`python run.py --gpu` meng-set default GPU (PaddleOCR, worker=6, cap FULL=5000). Override tetap lewat `.env`.

Percepat (trade-off): `SADT_MEDIA_TEXT_ENABLED=0`, `SADT_GPU_WHISPER_MODEL=tiny`, mode QUICK, `SADT_OCR_FULL_GALLERY=0`.

## Pipeline

detect/upload → acquire (ADB / iOS / ZIP) → index/hash → L1–L4 (+ media text / GPU) → findings → report HTML/JSON → authorize (pimpinan).
