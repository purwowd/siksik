# SATRIA Desktop

All-in-one workstation shell (Tauri 2) — React UI + Python FastAPI sidecar.

> **Setup lengkap (instalasi, akun, web + desktop):** [`SETUP.md`](SETUP.md)  
> **Host lab / ADB:** [`LAB_HOST.md`](LAB_HOST.md)

## Prasyarat

| Tool | Versi |
|------|-------|
| Node.js | 20+ |
| Rust | stable (`rustup`) |
| Python venv | `backend/.venv` sudah ter-install |

```bash
# Muat PATH Cargo di shell saat ini (setelah rustup install)
. "$HOME/.cargo/env"

# macOS / Linux — instal Rust sekali
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
. "$HOME/.cargo/env"
```

## Mode dev (hot reload UI)

Backend SATRIA **harus** jalan di port **8000** (Vite proxy). Shell desktop akan:

1. Spawn `backend/run.py` jika belum ada di `:8000`
2. Buka Vite dev server di **`:5175`** (port khusus desktop — tidak bentrok dengan `npm run dev` di `:5173`)

```bash
# Terminal 1 — opsional jika mau backend manual
cd backend && .venv/bin/python run.py --reload --host 127.0.0.1 --port 8000

# Terminal 2 — desktop dev
cd desktop
npm install
npm run dev
```

> Jika backend sudah jalan (mis. `python run.py --reload`), shell tidak spawn duplikat.

## Build produksi (installer)

Satu port **8765** — FastAPI layani API + static UI (`SADT_DESKTOP_UI=1`).

```bash
cd desktop
npm install
npm run build
```

Artefak:

- **macOS:** `src-tauri/target/release/bundle/macos/SATRIA.app`
- **Windows:** `src-tauri/target/release/bundle/msi/`
- **Linux:** `src-tauri/target/release/bundle/deb/` atau AppImage

## Arsitektur

```
┌─────────────────────────────────────┐
│  Tauri window (WebView)             │
│  Dev:  http://localhost:5175        │
│  Prod: http://127.0.0.1:8765        │
└──────────────┬──────────────────────┘
               │ /api/v1/*
┌──────────────▼──────────────────────┐
│  FastAPI sidecar (spawn otomatis)   │
│  + ADB · GPU · SQLite · staging    │
└─────────────────────────────────────┘
```

> Shell desktop **memakai `frontend/` + `backend/` yang sama** dengan lab web. Identitas peserta, laporan PDF, dll. ikut otomatis — tidak ada salinan UI di folder `desktop/`.

## Env desktop

| Variabel | Fungsi |
|----------|--------|
| `SADT_DESKTOP_UI=1` | Layani `frontend/dist` dari FastAPI |
| `SADT_DESKTOP_UI_DIST` | Path ke build Vite (default repo/frontend/dist) |

## Troubleshooting

| Gejala | Solusi |
|--------|--------|
| Port 8000 sudah dipakai | Stop proses lain atau biarkan shell pakai instance yang ada |
| Ekspor HTML/JSON/PDF gagal / app freeze | Dialog Simpan harus dari JS (async). Restart `./dev.sh`. PDF butuh Google Chrome terpasang. |
| Window JSON `{"app":...}` | Jangan `cargo run` langsung — pakai `./dev.sh` agar Vite `:5175` jalan. Tutup app lama dulu. |
| App "Not Responding" | Versi lama memblokir UI saat probe; update `lib.rs` lalu `./dev.sh` ulang. Pastikan backend `:8000` tidak hang. |
| Window kosong | Vite desktop pakai port **5175** — jalankan ulang `./dev.sh` (bukan `cargo run` langsung) |
| Port 5175 sudah dipakai | Bebaskan port atau set `SATRIA_DESKTOP_UI_PORT=5176` + sesuaikan `devUrl` di `tauri.conf.json` |
| `cargo not found` | Install Rust via rustup |
| Backend gagal start | Pastikan `backend/.venv` + `pip install -r requirements.txt` |

Web browser tetap bisa dipakai paralel (nginx / Vite) — desktop tidak mengganti deploy web lab.

## Login

Pakai akun lab yang sama dengan web (`admin`, `operator`, dll.).  
Password: default seed atau file `backend/data/lab-panitia-credentials.txt` setelah `setup_lab_panitia.py`.
