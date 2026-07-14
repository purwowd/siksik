# SADT PoC — Sistem Analisis Digital Terpadu

Proof of Concept: akuisisi selektif + analisis bertingkat (gallery-first) di **satu server GPU**, satu sesi aktif per waktu.

## Stack

| Layer | Teknologi |
|-------|-----------|
| Backend | Python FastAPI, aiosqlite (WAL), pipeline async |
| Frontend | React 19 + Vite + TypeScript |
| Akuisisi | ADB / libimobiledevice **atau** upload ZIP (opsional) |
| GPU stack | SafeWatch · ICM-Assistant · Qwen2.5-VL · Whisper · PaddleOCR |

---

## Menjalankan di server GPU (lengkap)

### 1. Prasyarat host

```bash
# NVIDIA driver + CUDA
nvidia-smi

# Tools sistem
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg adb git
# opsional iOS:
# sudo apt install -y libimobiledevice-utils ideviceinstaller
```

### 2. Clone & Python env

```bash
cd /opt   # atau home project
git clone <repo-sadt> aiai && cd aiai/backend

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt -r requirements-gpu.txt

# Torch CUDA — sesuaikan indeks CUDA server (contoh 12.4):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

### 3. Env GPU stack

Salin `.env.example` → `backend/.env` lalu isi:

```bash
# Bind — di balik reverse proxy / SSH tunnel, tetap loopback lebih aman
SADT_API_HOST=127.0.0.1
SADT_API_PORT=8000

# Stack GPU
SADT_GPU_STACK_ENABLED=1
SADT_OCR_ENABLED=1
SADT_OCR_BACKEND=paddleocr
SADT_OCR_GPU=1
SADT_OCR_LANGS=en

SADT_GPU_WHISPER_ENABLED=1
SADT_GPU_WHISPER_MODEL=base          # tiny|base|small|medium|large-v3
SADT_GPU_WHISPER_LANG=id

# Checkpoint MLLM (opsional — tanpa ini Whisper+PaddleOCR+bridge tetap jalan)
SADT_GPU_SAFEWATCH_MODEL=/models/safewatch
SADT_GPU_ICM_MODEL=zhaoyuzhi/ICM-LLaVA-v1.5-7B
SADT_GPU_QWEN_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

# Seed password (hanya saat DB users kosong)
# SADT_SEED_ADMIN_PASSWORD=GantiIni@2026

# Upload ZIP hasil ADB
SADT_ZIP_ENABLED=1
SADT_ZIP_MAX_MB=512

# Simulator lab (default mati)
SADT_LAB_DEMO_MODE=0
```

### 4. Start API (GPU)

```bash
cd backend
source .venv/bin/activate
python run.py --host 127.0.0.1 --port 8000 --gpu
# atau dengan reload saat develop:
# python run.py --reload --host 127.0.0.1 --port 8000 --gpu
```

Cek:

```bash
# harus true + gpu_stack.enabled
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@2026"}'
# pakai token → GET /api/v1/health → extras.vision.gpu_stack
```

### 5. Frontend (laptop ops atau server yang sama)

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
# pastikan Vite proxy ke API (SADT_API_PORT)
```

UI: http://127.0.0.1:5173

### 6. Acceptance di server GPU

```bash
cd backend && source .venv/bin/activate
SADT_REQUIRE_GPU=1 python scripts/run_acceptance.py --perf --require-gpu
# atau
bash scripts/deploy_gpu.sh
```

### 7. Model map

| Role | Model | Env |
|------|-------|-----|
| Video moderation | SafeWatch | `SADT_GPU_SAFEWATCH_MODEL` |
| Image moderation | ICM-Assistant | `SADT_GPU_ICM_MODEL` |
| Reasoning | Qwen2.5-VL-7B | `SADT_GPU_QWEN_MODEL` |
| Audio / lirik | Whisper | `SADT_GPU_WHISPER_MODEL` |
| OCR | PaddleOCR | `SADT_OCR_BACKEND=paddleocr` |

Refs: [SafeWatch](https://safewatch-aiguard.github.io/) · [ICM-Assistant](https://github.com/zhaoyuzhi/icm-assistant) · Qwen2.5-VL · openai-whisper · PaddleOCR

---

## Analisa dari ZIP (opsional — tanpa akuisisi live)

Gunakan jika dump sudah diambil di mesin lain (`adb pull` → zip), lalu dianalisis di server GPU.

### Siapkan ZIP

```bash
# Contoh di laptop yang menyambung HP:
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
TOKEN=...   # dari /auth/login
curl -X POST http://127.0.0.1:8000/api/v1/sessions/from-zip \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@adb_media.zip" \
  -F "mode=quick" \
  -F "label=Dump unit A"
```

Endpoint: `POST /api/v1/sessions/from-zip` (permission `sessions:start`).

Nonaktifkan: `SADT_ZIP_ENABLED=0`.

---

## Menjalankan cepat (lab laptop, tanpa GPU penuh)

```bash
bash scripts/start_poc.sh
# atau terpisah:
#   backend: uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
#   frontend: cd frontend && npm run dev
```

## Test

```bash
cd backend && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

| Marker | Isi |
|--------|-----|
| `unit` | L1–L3 / gpu_stack types |
| `api` | endpoint + sesi + ZIP |
| `acceptance` | gate deploy |
| `perf` | SLA pipeline |
| `gpu` | nvidia-smi / torch |

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
| PATCH | `/api/v1/findings/{id}` | review |
| GET | `/api/v1/dashboard` | agregat |

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

Prefer bare-metal GPU host untuk Whisper/MLLM; Compose di repo ini cocok untuk smoke API/UI.

## Fokus PoC: GALERI

- ADB: `DCIM` / `Pictures` / screenshot (+ foto chat opsional)
- ZIP: dump media setara
- L3/L4: Pillow + GPU stack (Whisper untuk lirik audio)
- `msgstore.db` WhatsApp: **belum** masuk ruang lingkup

Demo NEG/POS + simulator: default **OFF** (`SADT_LAB_DEMO_MODE=1` untuk mengaktifkan).

## Pipeline

detect/upload → acquire (ADB / iOS / ZIP) → index/hash → L1–L4 (+ GPU) → findings → report HTML/JSON → authorize (pimpinan).
