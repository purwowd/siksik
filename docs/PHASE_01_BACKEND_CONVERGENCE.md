# Phase 01: Backend Convergence

Tanggal verifikasi: 16 Juli 2026.

## Hasil

- Akuisisi Android legacy, iOS, simulator, dan ZIP sekarang dipilih melalui kontrak provider bertipe tanpa mengubah endpoint atau format hasil lama.
- Boundary Android agent tersedia sebagai provider terpisah dan tetap nonaktif sampai bootstrap serta APK melewati phase berikutnya.
- Jalur fallback Android legacy eksplisit dan dapat dinonaktifkan melalui konfigurasi.
- Transport ADB bersifat lazy sehingga startup SIKSIK tidak membutuhkan Android SDK atau ADB.
- Semua operasi ADB perangkat memvalidasi serial dan menyisipkan `-s <serial>` pada argv.
- Runner proses memakai argv, timeout, pembatasan output, cancellation cleanup, dan kategori error stabil.
- Build APK memakai digest input deterministik, validasi size/hash, stamp atomik, lock, dan invalidasi cache saat source atau artifact berubah.
- Client agent hanya menerima host loopback dari port lokal, memakai bearer per sesi, request ID, model respons strict, batas ukuran, timeout, dan retry terbatas.
- Metadata runtime agent dimiliki `sessions.id`; database tidak menyimpan serial atau bearer token mentah.
- Request baru mendapat `X-Request-ID`; error acquisition baru memakai envelope stabil tanpa mengubah error endpoint lama.
- Logging acquisition memakai field allowlist sehingga serial, token, dan isi koleksi tidak dapat masuk ke output terstruktur.

## Donor yang diserap

| Donor LPDP | Adaptasi SIKSIK |
| --- | --- |
| `adb_core.py` dan `adb_transport.py` | Diubah menjadi transport async, resolution lazy, output bounded, serial-pinned argv, serta error SIKSIK. |
| `companion_artifact.py` | Diubah menjadi service async dengan digest source yang lebih luas dan verifikasi hash APK. |
| `companion_client.py` | Diubah dari client sinkron menjadi `httpx.AsyncClient` dengan model Pydantic strict dan korelasi request ID. |
| `session_service.py` | Hanya invariannya diserap; SIKSIK session tetap authority dan runtime rahasia disimpan di registry memori. |
| `errors.py` dan `logging.py` | Pola kategori error dan JSON logging diadaptasi dengan pesan serta redaction SIKSIK. |

Tidak ada import, runtime path, package, atau proses yang bergantung pada direktori LPDP.

## Database

Infrastructure migrasi versi ditambahkan melalui `schema_migrations`.

Migrasi version 2 menambahkan `agent_runtimes` dengan:

- foreign key `session_id` ke sesi SIKSIK;
- device reference yang sudah di-hash;
- state dan versi agent/API;
- port forward, expiry, dan fingerprint token;
- request ID dan kategori kegagalan;
- timestamp audit.

Migrasi bersifat additive dan idempotent pada database baru maupun database baseline.

## Kompatibilitas

- Seluruh route baseline tetap sama.
- `/sessions/from-zip` tetap merupakan provider terpisah dan tetap memakai ZIP.
- Akuisisi Android agent belum mengganti Android legacy pada phase ini.
- Source frontend, route frontend, class CSS, style, layout, dan format tidak diubah.
- iOS, simulator, laporan, finding/review, rekomendasi, dashboard, timeline, auth, dan RBAC tetap memakai perilaku lama.

## Verifikasi

- Donor LPDP: 117 test lulus.
- Test baru Phase 01: 37 test lulus.
- Full SIKSIK backend: 129 test lulus, 4 test environment-optional dilewati.
- Frontend production build: lulus.
- Pencarian source memastikan tidak ada referensi runtime ke LPDP dan tidak ada `shell=True` atau `os.system` pada modul baru.

Kasus build/install/handshake dengan perangkat Android fisik belum dijalankan pada phase ini karena APK SIKSIK agent baru dibuat pada Phase 02 dan bootstrap otomatis baru diaktifkan pada Phase 03.
