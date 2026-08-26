# SATRIA Workstation — Runbook operasional

Satu halaman untuk panitia on-prem. Lab GPU, **satu sesi aktif** per waktu.

## Mulai konsol

1. Host: `cd backend && source .venv/bin/activate && SATRIA_LAB_DEMO_MODE=0 python run.py`
2. UI produksi: `cd frontend && npm run build` lalu layani `dist/` (atau `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`).
3. Buka HTTPS edge `https://127.0.0.1:8443` atau UI yang dikonfigurasi.
4. Masuk dengan akun yang dibuat saat instalasi (`setup_lab_panitia.py`) — **bukan** akun demo.

## Ganti kata sandi

```bash
cd backend && source .venv/bin/activate
python scripts/rotate_lab_passwords.py --from-env
```

Simpan hasil di brankas panitia, bukan di repo.

## Cadangan & pulihkan

```bash
cd backend && source .venv/bin/activate
python scripts/backup_lab_data.py --dest /var/backups/satria
python scripts/backup_lab_data.py --restore /var/backups/satria/satria_YYYYMMDD_HHMMSS
```

Uji pulih minimal sekali sebelum operasi resmi. Hentikan API saat restore.

## Batalkan sesi macet

Operator: tombol **Batalkan** di Penerimaan.  
Jika proses host menggantung: hentikan `python run.py`, lalu mulai ulang. Hanya satu pemeriksaan boleh jalan.

## Mode produksi (wajib)

- `SATRIA_LAB_DEMO_MODE=0`
- `SATRIA_E2E_SIMULATION=0`
- CORS hanya origin UI instalasi
- API bind loopback + TLS di depan
- Overlay env: salin `deploy/env/production.env.example` → `production.env`, isi sandi, `chmod 600`

SATRIA adalah **alat bantu seleksi + review analis**, bukan dasar tunggal kelulusan ASN.

## Hapus staging sesi (berita acara)

Setelah kasus selesai dan cadangan diambil:

```bash
cd backend && source .venv/bin/activate
python scripts/wipe_session_staging.py --session-id UUID --actor pimpinan
```

Sertifikat JSON tersimpan di `backend/data/wipe-certificates/`. Rekaman sesi di database tidak ikut terhapus.
