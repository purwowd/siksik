# SATRIA — Host lab vs Docker

> **Setup lengkap:** [`SETUP.md`](SETUP.md) · **Menjalankan:** [`RUNNING.md`](RUNNING.md) · **Cek per halaman:** [`PAGES.md`](PAGES.md)

## Ringkasan

| Mode | UI | API | ADB/USB | GPU | Demo perangkat live |
|------|----|-----|---------|-----|---------------------|
| **Docker** (`docker compose up`) | `:5173` | `:8000` | Tidak | Terbatas | Tidak (ZIP saja) |
| **Host lab** | `npm run dev` + `python run.py` | `:8000` | Ya (jika terpasang) | Ya (jika CUDA) | Ya |

## Docker (smoke / UI demo)

```bash
docker compose up --build
```

- UI: http://127.0.0.1:5173  
- API: http://127.0.0.1:8000  
- Set `SADT_RUNTIME_ENV=docker` otomatis di compose — banner kontainer muncul di konsol.

**Batasan:** container tidak melihat USB HP; ADB/iOS toolchain biasanya `false`. Gunakan unggah ZIP bila `zip_enabled=true`.

## Host lab (akuisisi perangkat)

1. Pasang toolchain: `adb`, optional `idevice_id` / `idevicebackup2`.
2. Backend: `cd backend && python run.py`
3. Frontend: `cd frontend && npm run dev`
4. Set `SADT_RUNTIME_ENV=host` (default).

### Android USB (macOS / Linux)

- Aktifkan USB debugging di HP.
- `adb devices` harus menampilkan perangkat **authorized**.
- Linux: aturan udev untuk vendor ID; jalankan backend dengan akses USB jika diperlukan.

### iOS (opsional)

- Pasang libimobiledevice (`idevice_id`, `idevicebackup2`).
- Trust komputer di iPhone.

### GPU (opsional)

- Install stack CUDA + `requirements-gpu.txt` untuk OCR/Whisper lebih cepat.
- Tanpa GPU: mode CPU tetap jalan, lebih lambat.

## Variabel lingkungan

```env
# host | docker — ditampilkan di /health → banner UI
SADT_RUNTIME_ENV=host

# SATRIA_* alias juga didukung
SATRIA_RUNTIME_ENV=host
```

## Kapan pakai apa?

- **Presentasi UI / alur SPD / review temuan:** Docker cukup.
- **Demo forensik HP live / agent Android:** host lab wajib.
- **Produksi PoC:** rencanakan host dedicated dengan ADB, backup iOS, dan GPU sesuai SLA analisa.
