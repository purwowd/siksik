from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
STAGING_DIR = DATA_DIR / "staging"
DB_PATH = DATA_DIR / "poc.db"
SYNTHETIC_DIR = DATA_DIR / "synthetic"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SADT_", env_file=".env", extra="ignore")

    app_name: str = "Sistem Analisis Digital Terpadu — PoC"
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]

    data_dir: Path = DATA_DIR
    staging_dir: Path = STAGING_DIR
    db_path: Path = DB_PATH
    synthetic_dir: Path = SYNTHETIC_DIR

    # Fokus PoC saat ini: GALERI (msgstore/WA DB ditunda)
    focus_scope: str = "gallery"  # gallery | all (nanti)

    # Lab demo / simulator sintesis — default OFF (ops live saja)
    # Aktifkan: SADT_LAB_DEMO_MODE=1
    lab_demo_mode: bool = False

    # Performance knobs — gallery-first
    image_cap_quick: int = 800
    image_cap_full: int = 3000
    video_cap_quick: int = 80
    video_cap_full: int = 0  # 0 = tanpa batas (FULL)
    max_file_size_mb: int = 50
    cv_batch_size: int = 16
    worker_concurrency: int = 4
    # Resize sebelum OCR — 0 = tanpa downscale; 2200 lebih baik untuk poster/meme
    ocr_max_edge_px: int = 2200
    # Upscale foto kecil (meme WA/crop) agar EasyOCR baca lebih jelas; 0 = off
    ocr_min_edge_px: int = 1200
    # Sharpen ringan sebelum OCR (poster/screenshot)
    ocr_sharpen: bool = True
    # EasyOCR: paragraph=False + filter conf (hindari teks digabung ambyar)
    ocr_paragraph: bool = False
    ocr_min_confidence: float = 0.18
    # Perbesaran internal EasyOCR (1.5 default library; 2.0 lebih baik teks kecil)
    ocr_mag_ratio: float = 2.0
    # Skip Whisper ASR pada video lebih panjang dari ini (detik); 0 = tanpa batas total
    video_whisper_max_duration_s: int = 0
    # Hanya transcribe N detik pertama (kecepatan); 0 = seluruh audio (hingga max_duration)
    video_whisper_transcribe_first_s: int = 120
    hash_chunk_bytes: int = 1024 * 1024
    adb_pull_timeout_s: int = 120
    adb_max_files_quick: int = 800
    adb_max_files_full: int = 5000

    # Upload ZIP hasil ADB (analisa tanpa akuisisi live)
    zip_max_mb: int = 512
    zip_enabled: bool = True

    # Android paths — GALERI dulu (tanpa Databases/msgstore)
    android_paths_quick: list[str] = [
        "/sdcard/DCIM/Camera",
        "/sdcard/DCIM",
        "/sdcard/Pictures",
        "/sdcard/Pictures/Screenshots",
        "/sdcard/Download",
        "/sdcard/Movies",
        # Media chat sebagai foto/video (bukan DB)
        "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images",
        "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Video",
        "/sdcard/WhatsApp/Media/WhatsApp Images",
        "/sdcard/WhatsApp/Media/WhatsApp Video",
        "/sdcard/Telegram/Telegram Images",
        "/sdcard/Telegram/Telegram Video",
    ]
    android_paths_full: list[str] = [
        "/sdcard/DCIM",
        "/sdcard/Pictures",
        "/sdcard/Download",
        "/sdcard/Movies",
        "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images",
        "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Video",
        "/sdcard/WhatsApp/Media/WhatsApp Images",
        "/sdcard/WhatsApp/Media/WhatsApp Video",
        "/sdcard/Telegram/Telegram Images",
        "/sdcard/Telegram/Telegram Video",
        "/sdcard/Android/media/org.telegram.messenger",
    ]
    # Preferensi ekstensi — fokus media galeri
    android_prefer_ext: list[str] = [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".heic",
        ".gif",
        ".mp4",
        ".mov",
        ".3gp",
    ]

    # OCR (enable di server GPU)
    ocr_enabled: bool = False
    ocr_backend: str = "paddleocr"  # paddleocr | easyocr | tesseract | fake
    ocr_gpu: bool = True
    ocr_langs: str = "en"
    # EasyOCR model dir (default: data/easyocr) — hindari ~/.EasyOCR di lab Mac
    ocr_model_dir: Path | None = None

    # Enrichment teks: screenshot/chat OCR, foto berteks, video ASR + on-screen OCR
    # Default ON — jalan jika engine terpasang; tanpa engine = no-op
    media_text_enabled: bool = True
    # Mode FULL: OCR semua gambar di gallery/pictures/dcim (bukan hanya edge/screenshot)
    ocr_full_gallery: bool = True
    video_overlay_keyframes: int = 5
    gpu_whisper_enabled: bool = True

    # CLIP zero-shot tokoh / presiden (butuh: pip install transformers)
    clip_tokoh_enabled: bool = True
    clip_tokoh_model: str = "openai/clip-vit-base-patch32"
    clip_tokoh_threshold: float = 0.24
    clip_tokoh_margin: float = 0.04

    # ---- GPU moderation stack (SafeWatch / ICM / Qwen-VL / Whisper / PaddleOCR) ----
    # Aktif: python run.py … --gpu  atau  SADT_GPU_STACK_ENABLED=1
    gpu_stack_enabled: bool = False
    gpu_video_keyframes: int = 5
    gpu_safewatch_enabled: bool = True
    gpu_safewatch_model: str = ""  # path checkpoint SafeWatch
    gpu_safewatch_plugin: str = ""  # dotted module with moderate(path) → hits
    gpu_icm_enabled: bool = True
    gpu_icm_model: str = ""  # e.g. zhaoyuzhi/ICM-LLaVA-v1.5-7B atau path lokal
    gpu_icm_plugin: str = ""  # dotted module with moderate(path) → hits
    gpu_qwen_enabled: bool = True
    gpu_qwen_model: str = ""  # e.g. Qwen/Qwen2.5-VL-7B-Instruct
    gpu_qwen_plugin: str = ""  # optional override for VL moderate(path)
    gpu_whisper_model: str = "base"  # tiny|base|small|medium|large-v3
    gpu_whisper_lang: str = "id"  # kosongkan untuk auto
    gpu_ocr_backend: str = "paddleocr"

    risk_keywords: list[str] = [
        "anti pemerintah",
        "anti presiden",
        "ganti presiden",
        "gulingkan",
        "makar",
        "hasut",
        "provokasi",
        "separatis",
        "radikal",
        "bom",
        "senjata ilegal",
        "narkoba",
        "judi online",
        "pornografi anak",
        "kudeta",
        "revolusi berdarah",
    ]
    # Kata kunci tambahan khusus video (ASR/lirik + nama file) — merge dengan risk_keywords
    video_risk_keywords: list[str] = [
        "papua",
        "papua merdeka",
        "free papua",
        "pesta haram",
        "pesta babi",
        "pantai barat",
        "west papua",
    ]
    # Nama tokoh / frasa OCR (foto poster, berita)
    tokoh_keywords: list[str] = [
        "jokowi",
        "joko widodo",
        "prabowo",
        "prabowo subianto",
        "presiden",
        "wakil presiden",
    ]
    # Ujaran/sindiran meme di teks gambar (sering bersama foto tokoh)
    # Frasa politis → OCR biasa; umpatan kasar → hanya naik bila ada tokoh (fusi)
    meme_hate_keywords: list[str] = [
        "lengserkan",
        "lengserkan Jokowi",
        "turunkan",
        "tenggelamkan",
        "diktator",
        "firaun",
        "boneka asing",
        "penghianat",
        "khianat negara",
        "jual negara",
        "antek asing",
        "antek aseng",
        "hutang luar negeri",
        "cebong",
        "kampret",
        "kadrun",
    ]
    meme_insult_keywords: list[str] = [
        "anjing",
        "bajingan",
        "sialan",
        "tolol",
        "bodoh",
        "munafik",
        "penjahat",
        "koruptor",
        "boneka",
    ]


settings = Settings()


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.synthetic_dir.mkdir(parents=True, exist_ok=True)
