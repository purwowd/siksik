from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.branding import PRODUCT_FULL_NAME, promote_satria_env

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
STAGING_DIR = DATA_DIR / "staging"
DB_PATH = DATA_DIR / "poc.db"
SYNTHETIC_DIR = DATA_DIR / "synthetic"

# SATRIA_* wins over SADT_*; legacy SADT_* alone still works.
promote_satria_env(root=ROOT.parent)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SADT_", env_file=".env", extra="ignore")

    app_name: str = f"{PRODUCT_FULL_NAME} — SATRIA"
    api_prefix: str = "/api/v1"
    cors_origins: Annotated[
        list[str],
        NoDecode,
        Field(
            default_factory=lambda: [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:4173",
                "http://127.0.0.1:4173",
            ]
        ),
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return parts or value
        return value

    data_dir: Path = DATA_DIR
    staging_dir: Path = STAGING_DIR
    db_path: Path = DB_PATH
    synthetic_dir: Path = SYNTHETIC_DIR

    # Fokus PoC saat ini: GALERI (msgstore/WA DB ditunda)
    focus_scope: str = "gallery"  # gallery | all (nanti)

    # Lab demo / simulator sintesis — default OFF (ops live saja)
    # Aktifkan: SADT_LAB_DEMO_MODE=1
    lab_demo_mode: bool = False

    # Izinkan force_simulated untuk E2E otomatis (jangan aktifkan di produksi)
    e2e_simulation: bool = False

    # Desktop all-in-one (Tauri): layani build Vite dari FastAPI (satu port)
    desktop_ui_enabled: bool = False
    desktop_ui_dist: Path = ROOT.parent / "frontend" / "dist"

    # Performance knobs — gallery-first
    image_cap_quick: int = 0
    image_cap_full: int = 0  # 0 = tanpa batas (dalam window 3/6 bulan)
    video_cap_quick: int = 0
    video_cap_full: int = 0  # 0 = tanpa batas (FULL)
    max_file_size_mb: int = 4096
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
    adb_max_files_quick: int = 0
    adb_max_files_full: int = 0

    # Android provider boundaries. Agent remains opt-in until its phase gates pass.
    adb_path: str = "adb"
    adb_command_timeout_s: float = 30.0
    android_min_api: int = 26
    android_agent_enabled: bool = True
    # JALANKAN AKUISISI: always adb install -r agent APK (build + install otomatis).
    android_agent_force_reinstall: bool = True
    android_legacy_fallback: bool = True
    android_agent_project_path: Path = ROOT.parent / "android-agent"
    android_agent_apk_path: Path = (
        ROOT.parent / "android-agent" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    )
    android_agent_automation_apk_path: Path = (
        ROOT.parent
        / "android-agent"
        / "automation"
        / "build"
        / "outputs"
        / "apk"
        / "debug"
        / "automation-debug.apk"
    )
    android_agent_build_timeout_s: float = 600.0
    android_java_home: Path | None = None
    android_sdk_home: Path | None = None
    android_agent_package: str = "com.siksik.agent"
    android_agent_component: str = "com.siksik.agent/.session.BootstrapActivity"
    android_agent_api_version: str = "1.0"
    android_agent_device_port: int = 38471
    android_agent_token_ttl_s: int = 3600
    android_agent_request_timeout_s: float = 60.0
    android_agent_request_attempts: int = 3
    android_agent_reconnect_timeout_s: float = Field(default=90.0, ge=5.0, le=600.0)
    android_agent_reconnect_poll_s: float = Field(default=0.5, ge=0.1, le=10.0)
    android_agent_max_response_mb: int = Field(default=4, ge=1, le=16)
    android_agent_install_timeout_s: float = 180.0
    android_agent_inspection_timeout_s: float = 60.0
    android_agent_min_device_storage_mb: int = 128
    android_agent_access_timeout_s: float = 180.0
    android_agent_access_poll_s: float = 1.0
    android_agent_required_special_access: list[str] = []
    android_agent_accessibility_component: str = (
        "com.siksik.agent/com.siksik.agent.accessibility.CaptureAccessibilityService"
    )
    android_agent_notification_component: str = (
        "com.siksik.agent/com.siksik.agent.notification.SessionNotificationListener"
    )
    android_agent_automation_package: str = "com.siksik.agent.automation"
    android_agent_automation_runner: str = (
        "com.siksik.agent.automation/com.siksik.agent.automation.SiksikAndroidJUnitRunner"
    )
    android_agent_automation_test_class: str = (
        "com.siksik.agent.automation.SocialCrawlInstrumentation"
    )
    android_agent_automation_install_timeout_s: float = 180.0
    android_agent_automation_target_timeout_s: float = 1800.0

    android_recovery_enabled: bool = True
    android_recovery_quick_max_items: int = Field(default=25, ge=1, le=500)
    android_recovery_full_max_items: int = Field(default=500, ge=1, le=10_000)
    android_recovery_quick_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        le=8 * 1024 * 1024 * 1024,
    )
    android_recovery_full_max_bytes: int = Field(
        default=8 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        le=32 * 1024 * 1024 * 1024,
    )
    android_recovery_max_file_bytes: int = Field(
        default=4 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
    )
    android_recovery_quick_scan_timeout_s: float = Field(default=120.0, ge=5.0, le=900.0)
    android_recovery_full_scan_timeout_s: float = Field(default=900.0, ge=30.0, le=3600.0)
    android_recovery_query_timeout_s: float = Field(default=300.0, ge=5.0, le=900.0)
    android_recovery_transfer_timeout_s: float = Field(default=3600.0, ge=30.0, le=7200.0)
    android_recovery_output_limit_bytes: int = Field(
        default=32 * 1024 * 1024,
        ge=1024 * 1024,
        le=128 * 1024 * 1024,
    )
    android_recovery_max_cache_source_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        le=2 * 1024 * 1024 * 1024,
    )
    android_agent_social_targets: list[str] = [
        "com.instagram.android",
        "com.twitter.android",
        "com.facebook.katana",
    ]
    android_agent_social_quick_scrolls: int = 200
    android_agent_social_full_scrolls: int = 400
    android_agent_social_quick_screenshots: int = 24
    android_agent_social_full_screenshots: int = 46
    android_social_host_ocr_enabled: bool = True
    android_social_ocr_max_edge_px: int = 1280
    android_social_ocr_mag_ratio: float = 1.25
    android_social_debug_snapshots: bool = True
    android_social_debug_dir: Path = ROOT.parent / "temp_crawl"

    gmail_acquisition_enabled: bool = True
    gmail_client_id: str = ""
    gmail_quick_max_messages: int = Field(default=0, ge=0, le=100_000)
    gmail_full_max_messages: int = Field(default=0, ge=0, le=100_000)
    gmail_request_timeout_s: float = Field(default=30.0, ge=5.0, le=120.0)
    gmail_scope: str = "oauth2:https://www.googleapis.com/auth/gmail.readonly"

    @property
    def resolved_gmail_scope(self) -> str:
        return self.gmail_scope

    ios_social_ui_enabled: bool = True
    ios_afc_media_enabled: bool = True
    ios_afc_docs_enabled: bool = True
    ios_afc_quick_media_count: int = 0
    ios_afc_full_media_count: int = 0
    ios_afc_quick_docs_count: int = 0
    ios_afc_full_docs_count: int = 0
    ios_afc_timeout_s: float = 300.0
    ios_photo_library_recovery_enabled: bool = True
    ios_library_quick_hidden_count: int = Field(default=25, ge=1, le=500)
    ios_library_full_hidden_count: int = Field(default=500, ge=1, le=5_000)
    ios_library_quick_deleted_count: int = Field(default=25, ge=1, le=500)
    ios_library_full_deleted_count: int = Field(default=500, ge=1, le=5_000)
    ios_library_quick_cache_count: int = Field(default=40, ge=1, le=1_000)
    ios_library_full_cache_count: int = Field(default=1_000, ge=1, le=10_000)
    ios_library_quick_metadata_count: int = Field(default=100, ge=1, le=1_000)
    ios_library_full_metadata_count: int = Field(default=2_000, ge=1, le=10_000)
    ios_library_quick_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        le=8 * 1024 * 1024 * 1024,
    )
    ios_library_full_max_bytes: int = Field(
        default=8 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        le=32 * 1024 * 1024 * 1024,
    )
    ios_library_max_file_bytes: int = Field(
        default=4 * 1024 * 1024 * 1024,
        ge=1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
    )
    ios_library_max_cache_source_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=1024 * 1024,
        le=1024 * 1024 * 1024,
    )
    ios_library_quick_cache_entry_limit: int = Field(default=5_000, ge=100, le=100_000)
    ios_library_full_cache_entry_limit: int = Field(default=50_000, ge=100, le=500_000)
    ios_library_quick_ithmb_sources: int = Field(default=2, ge=1, le=32)
    ios_library_full_ithmb_sources: int = Field(default=16, ge=1, le=128)
    ios_library_timeout_s: float = Field(default=900.0, ge=30.0, le=7200.0)
    # Selective backup2 --only sms/contacts (not full device backup).
    ios_sms_contacts_enabled: bool = True
    ios_sms_quick_messages: int = 0
    ios_sms_full_messages: int = 0
    ios_contacts_quick: int = 0
    ios_contacts_full: int = 0
    ios_backup_comms_timeout_s: float = 600.0
    ios_libimobiledevice_backup_enabled: bool = False
    ios_media_puller_path: Path = ROOT.parent / "ios-media-puller"
    ios_social_wda_url: str = "http://127.0.0.1:8100"
    # Pair + DDI download (iPhone baru/reboot) + tunnel restart + WDA install/boot.
    ios_social_wda_boot_timeout_s: float = 300.0
    ios_social_flow_timeout_s: float = 420.0
    ios_social_quick_archive_shots: int = 3
    ios_social_full_archive_shots: int = 0
    ios_social_quick_x_shots: int = 2
    ios_social_full_x_shots: int = 0
    ios_social_targets: list[str] = [
        "com.instagram.android",
        "com.twitter.android",
        "com.facebook.katana",
    ]

    # Upload ZIP hasil ADB (analisa tanpa akuisisi live)
    zip_max_mb: int = 512
    zip_enabled: bool = True
    # host | docker — surfaced in /health for UI runtime banner
    runtime_env: str = "host"

    # Android paths — GALERI dulu (tanpa Databases/msgstore)
    android_paths_quick: list[str] = [
        "/sdcard/DCIM/Camera",
        "/sdcard/DCIM",
        "/sdcard/Pictures",
        "/sdcard/Pictures/Screenshots",
        "/sdcard/Download",
        "/sdcard/Documents",
        "/sdcard/Movies",
        "/sdcard/Music",
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
        "/sdcard/Documents",
        "/sdcard/Movies",
        "/sdcard/Music",
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
        ".mkv",
        ".webm",
        ".mp3",
        ".m4a",
        ".aac",
        ".wav",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".txt",
        ".csv",
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

    # Explicit nudity detection — lightweight bundled NudeNet 320n.
    # Runs for every selected image/video in QUICK and FULL, independent of OCR/GPU stack.
    nudity_detection_enabled: bool = True
    nudity_threshold_anus: float = Field(default=0.50, ge=0.2, le=1.0)
    nudity_threshold_buttocks: float = Field(default=0.60, ge=0.2, le=1.0)
    nudity_threshold_female_breast: float = Field(default=0.55, ge=0.2, le=1.0)
    nudity_threshold_female_genitalia: float = Field(default=0.50, ge=0.2, le=1.0)
    nudity_threshold_male_genitalia: float = Field(default=0.50, ge=0.2, le=1.0)
    nudity_video_frames_quick: int = Field(default=12, ge=1, le=120)
    nudity_video_frames_full: int = Field(default=24, ge=1, le=120)
    nudity_video_min_positive_frames: int = Field(default=1, ge=1, le=10)
    nudity_frame_max_edge_px: int = Field(default=640, ge=320, le=1920)
    nudity_batch_size: int = Field(default=4, ge=1, le=32)
    nudity_max_evidence_items: int = Field(default=6, ge=1, le=24)
    nudity_video_probe_timeout_s: int = Field(default=30, ge=1, le=300)
    nudity_video_extract_timeout_s: int = Field(default=180, ge=10, le=1800)

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
