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
    max_file_size_mb: int = 50
    cv_batch_size: int = 16
    worker_concurrency: int = 4
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
        # Media chat sebagai foto saja (bukan DB) — opsional sekunder
        "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images",
        "/sdcard/WhatsApp/Media/WhatsApp Images",
        "/sdcard/Telegram/Telegram Images",
    ]
    android_paths_full: list[str] = [
        "/sdcard/DCIM",
        "/sdcard/Pictures",
        "/sdcard/Download",
        "/sdcard/Movies",
        "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images",
        "/sdcard/WhatsApp/Media/WhatsApp Images",
        "/sdcard/Telegram/Telegram Images",
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

    # ---- GPU moderation stack (SafeWatch / ICM / Qwen-VL / Whisper / PaddleOCR) ----
    # Aktif: python run.py … --gpu  atau  SADT_GPU_STACK_ENABLED=1
    gpu_stack_enabled: bool = False
    gpu_video_keyframes: int = 3
    gpu_safewatch_enabled: bool = True
    gpu_safewatch_model: str = ""  # path checkpoint SafeWatch
    gpu_icm_enabled: bool = True
    gpu_icm_model: str = ""  # e.g. zhaoyuzhi/ICM-LLaVA-v1.5-7B atau path lokal
    gpu_qwen_enabled: bool = True
    gpu_qwen_model: str = ""  # e.g. Qwen/Qwen2.5-VL-7B-Instruct
    gpu_whisper_enabled: bool = True
    gpu_whisper_model: str = "base"  # tiny|base|small|medium|large-v3
    gpu_whisper_lang: str = "id"  # kosongkan untuk auto
    gpu_ocr_backend: str = "paddleocr"

    risk_keywords: list[str] = [
        "anti pemerintah",
        "anti presiden",
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


settings = Settings()


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.synthetic_dir.mkdir(parents=True, exist_ok=True)
