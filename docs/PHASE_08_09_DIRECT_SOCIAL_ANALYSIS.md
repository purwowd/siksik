# Phase 08–09: Direct Ingestion dan Analysis untuk Crawl Android

Tanggal implementasi: 17 Juli 2026. Status: `IMPLEMENTED, NOT RUN`.

## Outcome

Jalur Android agent setelah selection sekarang mengikuti urutan SIKSIK:

`confirmed selection → agent direct staging → manifest + record canonical + file individual → serial-pinned ADB pull → size/hash verification → crawl persistence → files indexing → official SIKSIK analysis → findings/review/recommendation`

Tidak ada archive output pada jalur ini. Existing ZIP upload tetap provider tersendiri dan tidak diubah. Frontend juga tidak diubah; operator menguji hasil melalui dashboard SIKSIK existing.

## Social provenance

Visible-UI record wajib membawa salah satu scope berikut:

- `own_posts`
- `own_tweets`
- `own_story_archive`
- `own_comments`
- `own_replies`

Scope diaktifkan hanya setelah automation membuktikan area akun yang sedang login. Package dan scope aktif disimpan pada session capture; UiAutomation maupun Accessibility ditolak apabila binding tersebut tidak cocok. Record lama/tanpa scope tidak masuk inventory transfer.

## Direct hand-off

Agent 0.7.0 men-stage hanya candidate dari revision selection yang sudah confirmed. Setiap candidate menghasilkan file `application/vnd.siksik.crawl-record+json`. Original binary terpilih dan screenshot social disalin sebagai file individual. Manifest mengikat session, crawl, policy, revision, selection fingerprint, record, artifact role, path relatif, MIME, size, dan SHA-256.

Backend memakai stage ID serta idempotency key deterministik dan menarik direktori stage satu kali melalui ADB yang dipin ke serial session. Transfer tetap berupa manifest serta file individual, bukan archive. Backend kemudian memverifikasi path containment, relasi record, count, byte total, size, dan SHA-256 setiap artifact sebelum commit. Insert record/artifact dibatch dalam satu transaksi. Tabel `crawl_records`, `crawl_artifacts`, `crawl_transfers`, `crawl_events`, dan `crawl_permissions` ditambahkan secara aditif. Cleanup agent baru diminta setelah receipt ingestion tersimpan.

## Official analysis

Indexer membuat row `files` normal untuk record canonical, screenshot, dan binary. Hash serta waktu media yang baru diverifikasi dari manifest/record canonical dipakai ulang agar server tidak membaca binary dua kali; sumber non-agent tetap memakai hashing dan metadata reader existing. Record canonical diparse dengan `InventoryRecordV1`; analyzer hanya membaca `normalized_text`, sehingga nama key atau metadata JSON tidak dapat menjadi keyword finding. Screenshot/binary tetap melewati analyzer resmi image/OCR SIKSIK. Agent preprocessing dan score hanya provenance/selection aid, bukan finding resmi.

Findings tetap menunjuk `files.id`, memakai review state existing, dan recommendation existing tetap authority. Source, app, social scope, timestamp, record ID, dan provenance dibawa secara aditif pada `files.meta_json`.

## Validasi yang disediakan

- `backend/tests/test_direct_transfer_contract.py`
- `tests/run_contract_tests.sh`
- `tests/dashboard_social_flow_check.py`
- `tests/full_scan_benchmark.py`
- Android automation/store tests yang memeriksa scope, bounded capture, dan fail-closed behavior

Tidak satu pun script tersebut dijalankan pada sesi implementasi ini. Tidak ada klaim build, unit test, instrumentation, physical crawl, transfer, analysis, atau dashboard acceptance untuk perubahan 0.7.0.

## Compatibility

- Tidak ada perubahan source frontend.
- Tidak ada perubahan style, format, route, layout, atau komponen UI SIKSIK.
- iOS, simulator, dan existing ZIP provider tetap terpisah.
- Tidak ada dependency runtime atau path ke LPDP/forensics reference.
- Tidak ada root, private target-app database, credential extraction, atau aksi mutasi sosial.
