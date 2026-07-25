# Phase 04: Media and Document Crawling

Tanggal verifikasi: 16 Juli 2026.

## Hasil

Android agent SIKSIK 0.3.0 menyediakan inventory bertipe dan berhalaman untuk seluruh sumber Flow Step 2. Enumerasi tidak menyalin atau men-stage file asli. Backend menghabiskan cursor setiap sumber sebelum jalur analisis kompatibilitas lama berjalan; hand-off langsung tanpa ZIP tetap dikerjakan pada Phase 08.

Source frontend SIKSIK tidak diubah. Route, layout, stylesheet, komponen, navigasi, dan format UI lama tetap sama.

## Source adapter

| Adapter | API Android | Cakupan |
| --- | --- | --- |
| `media_store_image` | MediaStore Images | gambar dan metadata gambar |
| `media_store_video` | MediaStore Video | video, dimensi, dan durasi |
| `media_store_audio` | MediaStore Audio | audio dan durasi |
| `shared_storage_document` | MediaStore Files | PDF, DOC, DOCX, XLS, XLSX, CSV, TXT, dan RTF |
| `document_tree` | DocumentsContract | recursive tree yang dipilih user ketika scoped storage memerlukannya |
| `public_whatsapp` | MediaStore Files | media pada folder publik WhatsApp |
| `public_telegram` | MediaStore Files | media pada folder publik Telegram |

Adapter memakai urutan tetap. Media publik diproses sebelum MediaStore generik agar provenance aplikasi dipertahankan. Deduplikasi overlap memakai ID MediaStore dan fingerprint path, MIME, ukuran, serta waktu modifikasi untuk external-storage document tree. Provider dokumen lain tetap memakai identitas provider agar file berbeda tidak digabung secara spekulatif.

## Permission dan special access

| Android API | Media | Dokumen penuh | Lokasi EXIF |
| --- | --- | --- | --- |
| 33 ke atas | `READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `READ_MEDIA_AUDIO` | `MANAGE_EXTERNAL_STORAGE` atau document tree | `ACCESS_MEDIA_LOCATION` opsional |
| 29 sampai 32 | `READ_EXTERNAL_STORAGE` | API 30 ke atas memakai all-files atau tree; API 29 melaporkan keterbatasan scoped storage | `ACCESS_MEDIA_LOCATION` opsional |
| 26 sampai 28 | `READ_EXTERNAL_STORAGE` | MediaStore Files dengan izin storage | tidak diperlukan |

Backend meminta all-files secara otomatis melalui ADB saat full mode. Android tetap menjadi authority untuk layar konfirmasi OS. Penolakan akses opsional tidak dipalsukan sebagai sukses: bootstrap berlanjut dan adapter terkait melaporkan `restricted`, `denied`, atau `partial` dengan alasan tepat. Tidak ada root, pengubahan security setting yang tidak didukung, atau pembacaan private app sandbox.

## Kontrak inventory

Endpoint loopback baru tetap terikat bearer token, session ID, dan `X-Request-ID`:

- `POST /v1/sessions/{session_id}/crawl`
- `GET /v1/sessions/{session_id}/crawl`
- `GET /v1/sessions/{session_id}/crawl/{crawl_id}`
- `GET /v1/sessions/{session_id}/crawl/{crawl_id}/records`
- `POST /v1/sessions/{session_id}/crawl/{crawl_id}/cancel`
- `POST /v1/sessions/{session_id}/crawl/{crawl_id}/resume`

Respons record membawa ID opaque, source kind/app/adapter, locator tersanitasi, nama, MIME, ukuran, dimensi, durasi, tanggal sumber, normalized capture time, directory hint, EXIF kamera/orientasi/GPS, warning metadata, dan status thumbnail. UTC memakai notasi `Z`. Content URI hanya hidup di memori agent dan dilarang oleh model strict backend.

State crawl dan cursor disimpan di SQLite private agent. Cursor wire berupa token opaque; checkpoint MediaStore atau DocumentsContract tidak diekspos. Stop session menghapus run, cursor, dan dedupe ledger tanpa menghapus sumber asli.

## Kebijakan quick dan full

- Ukuran halaman maksimum 100 record dan seluruh query memakai urutan stabil serta checkpoint.
- Quick mode membaca paling banyak 200 baris per adapter, menandai `sampled`, menyimpan cursor kelanjutan, dan tidak mengklaim telah melakukan full crawl.
- Full mode melanjutkan cursor sampai setiap sumber accessible terminal.
- Sumber yang hilang, grant yang dicabut, permission yang ditolak, provider yang tidak stabil, atau batas tree yang tercapai menghasilkan state serta reason stabil dan cursor resume bila tersedia.
- Queue document tree dibatasi 2.048 node, depth 32, delapan root, dan checkpoint 8 MiB.
- Metadata rusak atau kosong menjadi warning per record dan tidak menggagalkan seluruh crawl.

## Verifikasi

- Backend penuh: 177 test lulus, 4 test environment-optional dilewati.
- Test Phase 04 mencakup kontrak strict, penolakan content URI, tujuh adapter, semua ekstensi dokumen, permission API 26/29/33, audio, paging, quick bound, full completion, deduplikasi, cancel/resume, denied/restricted/unsupported, permission revocation, provider disappearance, traversal/control character, dan bounded large catalog.
- Android unit test debug: 31 lulus.
- Android unit test release: 31 lulus.
- Android instrumentation source: berhasil dikompilasi untuk loopback route, state store/controller, synthetic EXIF/GPS/malformed image, dan cleanup.
- Android lint debug/release: lulus.
- APK debug dan release: berhasil dibangun.
- Frontend production build: lulus tanpa perubahan source frontend.

Instrumentation Phase 04 belum dijalankan pada perangkat fisik karena ADB tidak melihat perangkat saat gate ini. Phase 03 sebelumnya telah memverifikasi bootstrap otomatis pada Infinix X6837 API 33, tetapi hasil tersebut tidak dinyatakan sebagai verifikasi fisik untuk adapter Phase 04.

## Batas phase

Phase ini hanya membentuk inventory Flow Step 2. SMS, kontak, visible UI, notification capture, preprocessing, scoring, review, direct ingestion, dan UI tambahan tidak diaktifkan lebih awal. Existing ZIP upload, simulator, iOS, analysis, findings, review, recommendation, report, dashboard, dan timeline tetap memakai perilaku lama.
