# Sexual Content Detector

Detector konten seksual untuk **gambar dan video** — berjalan **100% lokal**, tanpa cloud API.

Pipeline ensemble: **OpenCV prescreen → NudeNet → SmolVLM (llama.cpp) → rules engine → LGBT + meme Indonesia**. Mendukung tiga mode operasi (`FAST` / `BALANCED` / `FULL`), action tier (`allow` / `review` / `block`), cache, timeout, dan metrics.

---

## Fitur

| Fitur | Deskripsi |
|-------|-----------|
| **Mode operasi** | `FAST` (~50ms), `BALANCED` (~1–2s), `FULL` (~2–4s) |
| **Action tier** | `allow`, `review`, `block` — bukan sekadar boolean |
| **Sidecar llama-server** | Satu proses VLM shared; tidak spawn per request |
| **Cache** | SHA256 in-memory per mode (TTL configurable) |
| **Timeout + metrics** | Per-mode timeout, latency & cache hit rate |
| **Video** | Sampling frame + agregasi max severity |
| **`analyze_bytes()`** | Input dari buffer/upload, bukan hanya file path |
| **LGBT context** | Deteksi bendera, pakaian pride, scene — metadata + hint orientasi |
| **Meme Indonesia** | OCR + VLM overlay, figur publik, satire/topik — **metadata saja** (tidak menggerakkan `action` sendiri) |
| **`meme_rules.yaml`** | Extend figur/frasa/OCR alias tanpa ubah kode Python |

---

## Pipeline

```
Media (file / bytes / video frames)
  │
  ▼
[Tier 1] Prescreen OpenCV          ~1ms     skip landscape/objek aman
  │
  ▼
[Tier 2] NudeNet 320n ONNX         ~50ms    deteksi bagian tubuh
  │
  ▼  (mode BALANCED / FULL)
[Tier 3] VLM two-pass              ~1–4s    describe → classify JSON
  │         + 1 center-crop orientasi (mode FULL, ratio 0.50)
  ▼
[Tier 4] Rules engine              ~0ms     merge + keyword + gender patterns
  │
  ▼
[Tier 5] LGBT + Meme Indonesia     ~50–400ms  pixel/VLM signals + Tesseract OCR (parallel)
  │         layout caption-bar → OCR band → merge figur/frasa → finalize
  ▼
Action resolver → allow / review / block
  │
  ▼
Agregator (video: max severity across frames)
```

Mode **FAST** berhenti setelah Tier 2 (NudeNet).

---

## Persyaratan

| Lingkungan | Rekomendasi |
|------------|-------------|
| **Dev** | Mac Mini M4 16 GB — llama.cpp + Metal (`-ngl 99`) |
| **Prod** | GPU NVIDIA RTX — llama.cpp CUDA via Docker sidecar |
| **Python** | 3.9+ |
| **Tesseract** | OCR overlay meme (`brew install tesseract tesseract-lang` di macOS) |
| **Disk** | ~2 GB (model SmolVLM-500M + NudeNet) |

---

## Setup

```bash
chmod +x scripts/setup.sh scripts/start_sidecar.sh
./scripts/setup.sh

source .venv/bin/activate
pip install -e .          # install sebagai package

# OCR meme (macOS)
brew install tesseract tesseract-lang

python scripts/download_samples.py
python scripts/validate_samples.py        # benchmark konten (mode FULL)
python scripts/validate_meme_benchmark.py # benchmark meme Indonesia (mode BALANCED)
```

`setup.sh` akan:
1. Clone & build `llama-server` (Metal di macOS)
2. Buat venv + install dependencies
3. Download model NudeNet + pre-fetch SmolVLM via HuggingFace

---

## Menjalankan llama-server (sidecar)

Production **tidak** spawn server per request. Jalankan sidecar sekali, lalu detector connect ke `host:port`.

**Mac M4 (dev):**
```bash
# Terminal 1 — sidecar Metal
./scripts/start_sidecar.sh

# Terminal 2 — detector
python main.py --external-server --mode full samples/internet/*.jpg
```

**Spawn lokal (dev tanpa sidecar):**
```bash
python main.py --spawn-server --mode full foto.jpg
```

**RTX GPU (prod):**
```bash
# Pastikan model ada di ./models/
docker compose --profile gpu up -d

python main.py -c config.prod.yaml --external-server --mode balanced foto.jpg
```

---

## Penggunaan CLI

```bash
# Analisis gambar
python main.py foto.jpg
sd-detector foto.jpg                        # setelah pip install -e .

# Mode & output
python main.py foto.jpg --mode fast
python main.py foto.jpg --mode balanced --json
python main.py video.mp4 --include-frames

# Sidecar vs spawn
python main.py foto.jpg --external-server     # default jika spawn_server=false
python main.py foto.jpg --spawn-server        # dev: spawn llama-server lokal

# Cache & metrics
python main.py foto.jpg --no-cache
python main.py samples/internet/*.jpg --metrics

# Override tier
python main.py foto.jpg --no-prescreen --no-nudenet
python main.py foto.jpg --no-meme              # nonaktifkan modul meme Indonesia
```

**Exit code:**

| Code | Arti |
|------|------|
| `0` | Semua `allow` |
| `1` | Ada `review` |
| `2` | Ada `block` |

---

## Penggunaan Python API

```python
from sd_detector import ContentDetector, DetectionMode

# Sidecar (recommended)
with ContentDetector(mode=DetectionMode.BALANCED) as det:
    # Dari file
    result = det.analyze("photo.jpg")
    print(result.action)          # allow | review | block
    print(result.verdict.severity)
    print(result.verdict.latency_ms)
    print(result.verdict.indonesian_meme.public_figures)  # metadata meme

    # Dari bytes (upload HTTP, S3, dll.)
    data = open("photo.jpg", "rb").read()
    result = det.analyze_bytes(data, source="upload-123")

    # Metrics
    print(det.metrics.snapshot())
```

---

## FastAPI HTTP API

```bash
pip install -e ".[api]"

# Terminal 1 — llama sidecar (skip jika mode fast)
./scripts/start_sidecar.sh

# Terminal 2 — API server
sd-detector-api
# atau: python api_server.py
# atau: uvicorn sd_detector.api:app --host 0.0.0.0 --port 8000
```

Docs interaktif: [http://localhost:8000/docs](http://localhost:8000/docs)

### Endpoints

| Method | Path | Deskripsi |
|--------|------|-----------|
| `GET` | `/health` | Status detector + llama sidecar |
| `GET` | `/metrics` | Latency, cache hit rate, action counts |
| `POST` | `/v1/analyze` | Upload gambar atau video (auto-detect) |
| `POST` | `/v1/analyze/image` | Upload gambar saja |
| `POST` | `/v1/analyze/video` | Upload video saja |

Query params: `mode=fast|balanced|full`, `include_frames=true`

### Contoh curl

```bash
# Health
curl http://localhost:8000/health

# Gambar — mode fast (~50ms, tanpa LLM)
curl -X POST "http://localhost:8000/v1/analyze?mode=fast" \
  -F "file=@samples/internet/01_safe_landscape.jpg"

# Gambar — mode balanced (butuh sidecar)
curl -X POST "http://localhost:8000/v1/analyze?mode=balanced" \
  -F "file=@samples/internet/06_suggestive_bikini.jpg"

# Video
curl -X POST "http://localhost:8000/v1/analyze/video?mode=balanced&include_frames=true" \
  -F "file=@video.mp4"
```

**HTTP status:**

| Code | Arti |
|------|------|
| `200` | `allow` |
| `422` | `review` |
| `403` | `block` |
| `408` | timeout |
| `413` | file > `SD_MAX_UPLOAD_MB` (default 50) |

Header response: `X-SD-Action: allow|review|block`

### Environment

| Var | Default | Deskripsi |
|-----|---------|-----------|
| `SD_CONFIG` | `config.yaml` | Path config |
| `SD_MODE` | dari config | Mode default (`fast`/`balanced`/`full`) |
| `SD_API_HOST` | `0.0.0.0` | Bind host |
| `SD_API_PORT` | `8000` | Port |
| `SD_MAX_UPLOAD_MB` | `50` | Max upload size |

---

## Mode operasi

| Mode | Tier aktif | Latency tipikal (M4) | Use case |
|------|------------|----------------------|----------|
| `fast` | Prescreen + NudeNet | ~50ms | Pre-filter volume tinggi, CDN edge |
| `balanced` | + 1× VLM | ~1–2s | Moderation umum (prod recommended) |
| `full` | + orientasi 1 crop | ~2–4s | Audit detail, compliance |

Set mode via CLI `--mode`, `config.yaml`, atau constructor:

```python
ContentDetector(mode=DetectionMode.FULL)
```

---

## Output

```json
{
  "path": "photo.jpg",
  "media_type": "image",
  "mode": "balanced",
  "severity": "suggestive",
  "nudity": "partial",
  "orientation": "heterosexual",
  "lgbt": {
    "present": true,
    "flag_colors": ["rainbow"],
    "symbols": ["pride_flag"],
    "clothing": [],
    "scene": ["pride_parade"],
    "signals": ["rainbow", "pride_flag"],
    "orientation_hint": "none"
  },
  "indonesian_meme": {
    "present": true,
    "is_meme": true,
    "has_text_overlay": true,
    "text_language": "id",
    "overlay_text": ["HIDUP SAWIT", "SANTUY BRO"],
    "public_figures": ["prabowo"],
    "satire_type": ["political_satire"],
    "topics": ["economy", "palm_oil"],
    "signals": ["caption_bar", "phrase_figure"],
    "confidence": 0.82
  },
  "acts": ["bikini"],
  "confidence": 0.78,
  "action": "review",
  "flagged": true,
  "latency_ms": 1420.5,
  "cache_hit": false,
  "reason": "Rules: woman in bikini at beach"
}
```

| Field | Nilai |
|-------|-------|
| `severity` | `safe`, `suggestive`, `explicit` |
| `nudity` | `none`, `partial`, `full` |
| `orientation` | `none`, `heterosexual`, `gay`, `lesbian`, `bisexual`, `other` |
| `lgbt` | Konteks LGBT dari pixel + VLM (bendera, pakaian, scene) |
| `lgbt.present` | Ada sinyal visual LGBT (bendera/warna/pakaian/pride) |
| `lgbt.flag_colors` | `rainbow`, `trans`, `bisexual`, `lesbian`, `gay`, `progress`, … |
| `lgbt.clothing` | `rainbow_clothing`, `pride_merch`, … |
| `lgbt.orientation_hint` | Hint dari gender + bendera + adegan (ciuman+dll.) |
| `indonesian_meme` | Konteks meme politik Indonesia (overlay, figur, satire, topik) |
| `indonesian_meme.present` | Ada sinyal meme (layout + teks overlay / figur publik) |
| `indonesian_meme.public_figures` | Figur ter-resolve: `jokowi`, `prabowo`, `gibran`, `anies`, … |
| `indonesian_meme.overlay_text` | Teks overlay dari OCR + VLM (setelah alias/noise filter) |
| `indonesian_meme.satire_type` | `political_satire`, `humor`, `criticism`, `deepfake`, … |
| `indonesian_meme.topics` | `economy`, `mbg`, `palm_oil`, `election`, … |
| `acts` | `kissing`, `bikini`, `nudity`, `lingerie`, `sexual_contact`, … |
| `action` | `allow`, `review`, `block` |
| `flagged` | `true` jika `action != allow` (backward compat) |

**Action tier** (threshold di `config.yaml → detector.action`):

- `explicit` + confidence ≥ 0.65 → **block**
- `suggestive` / `explicit` (lower confidence) → **review**
- `safe` → **allow**

---

## Konfigurasi

File utama: [`config.yaml`](config.yaml)  
Prod GPU: [`config.prod.yaml`](config.prod.yaml)

```yaml
llama:
  model_hf: "ggml-org/SmolVLM-500M-Instruct-GGUF"
  host: "127.0.0.1"
  port: 8080
  n_gpu_layers: 99       # 99 = offload semua layer (Metal/CUDA)
  spawn_server: false    # false = pakai sidecar

detector:
  mode: "full"           # fast | balanced | full
  nudenet_inference_resolution: 320
  orientation_crop_ratio: 0.50   # mode FULL

  action:
    block_explicit: 0.65
    review_suggestive: 0.55

  cache:
    enabled: true
    ttl_sec: 3600

  timeout:
    fast_sec: 5.0
    balanced_sec: 30.0
    full_sec: 60.0

meme:
  enabled: true
  ocr_enabled: true
  ocr_lang: "ind+eng"      # Tesseract: Indonesia + English
  ocr_full_res: true       # caption-bar OCR full-res untuk teks kecil
  ocr_workers: 2           # parallel band OCR
```

Aturan figur/frasa/OCR alias: [`meme_rules.yaml`](meme_rules.yaml) — edit tanpa deploy ulang kode Python.

```yaml
# meme_rules.yaml (ringkas)
figures:          # alias nama tokoh → canonical id
phrase_figures:   # frasa overlay → boost figur (mis. "hidup sawit" → prabowo)
ocr_aliases:      # koreksi noise OCR → teks canonical
satire_patterns / topic_patterns / format_patterns
```

Upgrade akurasi VLM: ganti `model_hf` ke `ggml-org/moondream2-20250414-GGUF` (~1.8 GB).

---

## Benchmark

### Konten umum (`samples/internet/`)

77 sample (Wikimedia + sintetis) — validasi severity, nudity, LGBT, action:

```bash
python scripts/download_samples.py   # download + regenerate expected.json
python scripts/validate_samples.py   # mode FULL (sidecar atau --spawn-server)
```

| Kategori | Jumlah | Contoh |
|----------|--------|--------|
| Safe | 8+ | landscape, office, food, cat, city, family, sports, wedding |
| Suggestive | 11+ | bikini, kiss hetero/gay/lesbian, pride, hug, shirtless |
| Art nudity | 2 | Venus, David |
| LGBT flags | 5+ | rainbow, trans, bisexual, pride shirt |
| Official portrait | 5 | Jokowi, Prabowo, Gibran, Anies (safe baseline) |

Laporan: `samples/internet/validation_report.json`

### Meme Indonesia (`samples/internet/*meme*.jpg` + `samples/user_memes/`)

**52 sample** — figur publik, overlay viral, satire politik (gemoy, ndasmu, hidup sawit, MBG, termul, …):

```bash
# Terminal 1 — sidecar wajib (mode balanced)
./scripts/start_sidecar.sh

# Terminal 2
python scripts/validate_meme_benchmark.py   # target: 52/52 pass (100%)
```

| Set | Jumlah | Isi |
|-----|--------|-----|
| Internet viral | 43 | `33_meme_*` – `77_meme_*` (sintetis dari portrait Commons) |
| User meme | 9 | PNG asli (`samples/user_memes/`, termasuk AI/deepfake overlay) |

Laporan: `samples/meme_benchmark_report.json`

### Full batch (semua sample)

```bash
python main.py samples/internet/*.jpg samples/user_memes/*.png \
  --mode balanced --external-server --no-cache --json \
  > samples/all_results.json
```

Hasil terakhir (85 file): meme terdeteksi 53, latency avg ~1.3s / p50 ~1.1s (balanced, M4).

> **Catatan:** 100% hanya pada curated sample set ini, bukan jaminan real-world. VLM kecil (500M) dan OCR masih bisa salah pada meme viral baru atau teks overlay parah. Extend [`meme_rules.yaml`](meme_rules.yaml) untuk frasa/alias baru.

---

## Struktur proyek

```
sexual-deviance/
├── config.yaml              # config dev (Mac M4)
├── config.prod.yaml         # config prod (RTX sidecar)
├── meme_rules.yaml          # figur, frasa, OCR alias, satire/topik meme ID
├── docker-compose.yml       # llama-server CUDA
├── main.py                  # CLI wrapper
├── pyproject.toml           # pip install -e .
├── scripts/
│   ├── setup.sh             # build llama.cpp + deps
│   ├── start_sidecar.sh     # sidecar Metal (Mac)
│   ├── download_samples.py  # download Commons + synthesize meme viral
│   ├── validate_samples.py  # benchmark konten (FULL)
│   └── validate_meme_benchmark.py  # benchmark meme Indonesia (BALANCED)
├── samples/
│   ├── internet/            # 76+ gambar + expected.json
│   ├── user_memes/          # 9 PNG asli + expected.json
│   ├── all_results.json     # hasil full batch balanced
│   └── meme_benchmark_report.json
└── src/sd_detector/
    ├── detector.py          # orchestrator, cache, timeout, metrics
    ├── classifier.py        # VLM two-pass + meme finalize
    ├── indonesian_meme.py   # layout, merge OCR/VLM, deepfake overlay
    ├── meme_ocr.py          # Tesseract band OCR, caption-bar
    ├── meme_rules.py        # load meme_rules.yaml, figure/phrase match
    ├── meme_bands.py        # deteksi strip teks atas/bawah
    ├── modes.py             # FAST / BALANCED / FULL
    ├── actions.py           # allow / review / block
    ├── rules.py             # ensemble rules
    ├── nudenet_tier.py      # NudeNet ONNX
    ├── llama_backend.py     # llama-server client
    ├── prescreen.py         # OpenCV prescreen
    ├── cache.py             # LRU result cache
    ├── metrics.py           # latency & counter
    └── cli.py               # sd-detector entry point
```

---

## Arsitektur production

```
┌─────────────────┐     HTTP      ┌──────────────────┐
│  sd-detector    │ ────────────► │  llama-server    │
│  (Python)       │   :8080       │  (sidecar)       │
│                 │               │  SmolVLM + GPU   │
│  NudeNet ONNX   │               └──────────────────┘
│  (in-process)   │
└─────────────────┘
```

- **Dev (M4):** sidecar native Metal via `start_sidecar.sh`
- **Prod (RTX):** sidecar Docker CUDA, detector scale horizontal tanpa reload model
- **FAST mode:** tidak butuh llama-server — cukup NudeNet in-process

---

## Lisensi

MIT — model pihak ketiga (SmolVLM, NudeNet) mengikuti lisensi masing-masing.
