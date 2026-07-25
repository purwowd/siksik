# Phase 06: Local Preprocessing

Tanggal verifikasi: 17 Juli 2026.

## Hasil

Android agent SIKSIK 0.7.0 menjalankan Flow Step 4 secara lokal untuk OCR, ekstraksi teks dokumen, exact/perceptual hash, duplicate grouping, face signal, face clustering, dan object label. Hasil ini hanya menjadi sinyal untuk selection Phase 07. Hasil preprocessing tidak membuat finding, tidak menentukan recommendation, dan tidak menggantikan analyzer resmi SIKSIK.

Alur runtime tetap mengikuti authority SIKSIK:

`SessionManager → Android acquisition/crawl → local preprocessing → Phase 07 selection/review → verified direct manifest + individual files → staging/files → analyzer resmi → findings → review → recommendation → report/dashboard`

Backend memulai preprocessing setelah crawl terminal, memantau counter bertipe, dan memvalidasi jumlah terminal tanpa mengunduh ulang seluruh payload record yang tidak dipakai. Endpoint record tetap tersedia untuk diagnosis. Revision selection terkonfirmasi diteruskan melalui direct manifest serta file individual Phase 08; jalur Android agent tidak membuat arsip ZIP. Existing ZIP upload SIKSIK tetap tersedia sebagai provider terpisah.

Source frontend tidak memperoleh style, stylesheet, layout, route, komponen, atau format visual baru. Perubahan frontend yang sudah ada hanya menggolongkan `preparing_agent` dan `awaiting_access` ke tahap Akuisisi pada pipeline lama.

## Engine dan model

| Capability | Engine/model | Versi | Asset SHA-256 |
| --- | --- | --- | --- |
| OCR Latin offline | ML Kit Text Recognition bundled | 16.0.1 | bundled dependency |
| Document text | SIKSIK bounded document text + Apache POI | 1.0.0 / POI 5.5.1 | tidak memakai model |
| Exact hash | SHA-256 streaming | FIPS-180-4 | tidak memakai model |
| Perceptual hash | difference-hash | 64-bit-v1 | tidak memakai model |
| Face detector | BlazeFace short-range float16 | v1 | `b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f` |
| Face crop embedder | MobileNet V3 small float32 | v1, 1.024 dimensi | `bbbb4c51a55a53905af1daec995ca1aae355046f8839bb8c9f5ce9271394bc40` |
| Object detector | EfficientDet-Lite0 int8 | v1 | `0720bf247bd76e6594ea28fa9c6f7c5242be774818997dbbeffc4da460c723bb` |
| Vision runtime | MediaPipe Tasks Vision | 0.10.35 | dependency terpin |

Registry memvalidasi path asset, ukuran registry, TFLite header, SHA-256, input width/height/channel, output-vector contract, runtime version, dan model identity. Capability berubah menjadi `granted` hanya setelah real initialization dan inference health fixture lulus. Missing asset, unreadable asset, hash mismatch, header mismatch, tensor mismatch, dan unknown model menghasilkan reason stabil serta tidak pernah memakai heuristic success.

Ketiga model memakai asset berlisensi Apache-2.0 dengan source URL dan attribution yang disimpan di project SIKSIK. Tidak ada model atau runtime yang dibaca dari LPDP.

## Mapping record ke preprocessing

| Source kind | Preprocessing lokal |
| --- | --- |
| `media_image` | exact SHA-256, dHash, OCR, face detection/embedding, object detection |
| `media_video` | exact SHA-256 dan representative bounded frame untuk dHash/OCR/face/object |
| `media_audio` | exact SHA-256 dan metadata/normalized source signal |
| `document` | exact SHA-256 dan bounded text extraction; PDF memakai page render → bundled OCR |
| `sms` | canonical normalized source text/hash dari typed crawl record |
| `contact` | canonical normalized source text/hash dari typed crawl record |
| `visible_ui` | canonical normalized node text/hash ditambah screenshot yang terikat ke session/crawl/attachment untuk dHash, OCR, face, dan object; teks OCR digabung secara bounded dengan node text |
| `notification` | canonical normalized source text/hash dari typed crawl record |

Content URI tetap berada di SQLite private app dan hanya dibuka oleh Android input boundary. URI tidak masuk wire response atau log. Raw face vector hanya hidup di private preprocessing store selama active crawl, dipakai untuk clustering, lalu dihapus ketika membership diterapkan. Record publik hanya membawa bounds, confidence, vector dimension, cluster ID, label, hash, bounded text, engine identity, duration, status, dan warning.

Screenshot social tidak dicari ulang dan tidak memperluas crawl. Input visual hanya dapat di-resolve dari `attachment_ids` milik record `visible_ui` yang sama, melalui file screenshot private yang masih terikat ke `session_id` dan `crawl_id`. Screenshot yang tidak ditemukan tidak memiliki fallback ke galeri, folder lain, feed, atau package lain. Sinyal visual ini memengaruhi selection lokal, sedangkan screenshot individual tetap dikirim melalui direct manifest agar analyzer resmi SIKSIK menganalisis biner yang sama.

Follow-up binding screenshot, decode reuse, budget full/document, dan perubahan bootstrap ADB sampai 19 Juli 2026 belum menjalankan build, test, instrumentation, atau crawl perangkat sesuai instruksi operator. Script kontrak tersedia di `tests`; acceptance aktual tetap dilakukan melalui dashboard.

## Dokumen

Format berikut memiliki jalur dan test positif:

- PDF melalui `PdfRenderer` dan OCR per halaman
- DOC melalui bounded legacy Word extractor
- DOCX melalui bounded, namespace-aware SAX parser
- XLS melalui bounded legacy Excel extractor
- XLSX dengan worksheet/shared-string resolution yang tidak bergantung urutan ZIP entry
- CSV dan TXT melalui bounded decoder dan normalisasi UTF-8
- RTF melalui bounded text parser

Encrypted, corrupt, unsupported feature, oversized, blank, truncated, cancelled, archive entry limit, expanded archive byte limit, PDF page limit, dan partial OCR memiliki state/warning eksplisit. OOXML dibaca sebagai format internal ZIP input; mekanisme ini bukan data bundling atau hand-off ZIP.

## Scheduler, persistence, dan lifecycle

- Concurrency worker maksimum dua.
- Per-item timeout produksi 20 detik; dokumen pada full mode mendapat 120 detik.
- Deadline preprocessing quick 10 menit dan full 40 menit.
- Resource policy menahan claim baru saat Android melaporkan low memory, thermal severe, atau free private storage di bawah 64 MiB.
- Pending item disimpan di SQLite private app dan `processing` dikembalikan ke `pending` setelah restart.
- Cancellation men-terminalkan run dan semua record pending/processing secara atomik.
- Late worker result tidak dapat menimpa record yang sudah cancelled.
- Record result, exact/perceptual signal, private face vector, run state, opaque cursor, dan counter per-preprocessor disimpan secara bounded.
- Counter per-preprocessor mencatat attempted, processed, skipped, truncated, failed, dan cancelled untuk exact hash, perceptual hash, OCR, document text, face, dan object.
- SQLite schema preprocessing memiliki migration v1 ke v2 untuk counter tersebut.
- Session stop/expiry/server close membatalkan worker, menutup engine, menghapus run/record/cursor/counter, lalu menutup database.

State terminal adalah `complete`, `partial`, `cancelled`, atau `failed`. Timeout, deadline, item failure, truncation, skip, dan internal failure tidak disamarkan sebagai complete. Full mode tidak diam-diam memotong preprocessing.

## Batas produksi

| Batas | Nilai |
| --- | ---: |
| visual input | 64 MiB |
| document input | 32 MiB |
| exact SHA-256 stream | 4 GiB per file |
| shared visual base | 4 juta pixel, satu decode per record |
| dHash input | 262.144 pixel |
| OCR input | 4 juta pixel |
| face/object input | 4 juta pixel |
| OCR text | 32.768 karakter |
| OCR regions | 128 |
| text per OCR region | 1.024 karakter |
| document searchable text | 65.536 karakter |
| OOXML entries | 1.024 |
| OOXML expanded bytes | 128 MiB |
| XLSX shared strings | 100.000 entry / 4 juta karakter |
| PDF pages | 24, maksimum 2 juta rendered pixel per page |
| face signals per record | 8 |
| object labels per record | 12 |
| duplicate signals per crawl | 10.000 |
| face signals per crawl | 4.096 |
| preprocessing record JSON | 512 KiB |
| record result page | 20 |
| opaque cursors retained per crawl | 128 |

Exact duplicate representative dipilih deterministik. Perceptual grouping memakai dHash Hamming distance maksimum delapan dengan bounded BK-tree. Anonymous face clustering memakai cosine similarity minimum 0,92 dan tidak mengekspos vector mentah.

## API dan backend contract

Agent menambahkan route session-bound dan authenticated berikut:

- `POST /v1/sessions/{session}/crawl/{crawl}/preprocessing`
- `GET /v1/sessions/{session}/crawl/{crawl}/preprocessing`
- `GET /v1/sessions/{session}/crawl/{crawl}/preprocessing/records`
- `POST /v1/sessions/{session}/crawl/{crawl}/preprocessing/cancel`

`CrawlRecordV1.preprocessing` divalidasi backend sebagai model strict, bukan dictionary bebas. Contract mencakup engine/model identity, status, duration, bounded warning, OCR region, document state, exact/perceptual hash, face bounds/dimension/cluster, object labels, duplicate membership, dan normalized source signal. Extra field, invalid enum, invalid hash, over-limit text/list, content URI, dan inconsistent record count ditolak.

Progress preprocessing dipublikasikan di dalam `SessionStatus.ACQUIRING` agar frontend lama tetap kompatibel dan tidak memerlukan perubahan visual sebelum Phase 10. Analysis, files, findings, review, recommendation, report, dashboard, timeline, RBAC, iOS, simulator, dan provider ZIP lama tidak diubah authority-nya.

## Test dan physical acceptance

Final automated evidence:

- Backend penuh: 187 lulus, 4 environment-optional dilewati.
- Android unit debug: 53 lulus.
- Android unit release: 53 lulus.
- Unit khusus Phase 06: 18 lulus, terdiri dari 9 document, 6 primitive/hash/clustering, dan 3 model-registry test.
- Android instrumentation penuh: 25 lulus, 0 gagal, 0 dilewati.
- Instrumentation khusus Phase 06: 11 lulus, terdiri dari 4 real-engine dan 7 coordinator/store test.
- Android lint debug dan release: lulus.
- APK debug dan release: berhasil dibangun.
- Frontend strict TypeScript dan production Vite build: lulus.

Physical instrumentation dijalankan dengan ADB serial-pinned pada Samsung SM-A736B, Android 14, API 34. Test membuktikan:

- keenam preprocessing capability dilaporkan `granted` setelah health inference
- bundled OCR membaca text dan region, menangani blank, corrupt, dan oversized input
- PDF renderer memberi page bitmap ke bundled OCR
- BlazeFace, MobileNet V3 embedder, dan EfficientDet-Lite0 load serta menjalankan real inference
- tensor mismatch tidak mengklaim capability
- bounded concurrency, opaque paging, per-preprocessor counter, restart checkpoint, item timeout, cancellation race, resource deadline, dan session cleanup
- loopback API, inventory, communication provider/store, EXIF, dan staging regression lama tetap lulus

Fixture hanya berisi text/image/PDF/DOC deterministik buatan test dan tidak mengambil isi koleksi perangkat. Acceptance log tidak menyimpan collected content.

## Observasi performa dan artifact

Pada perangkat acceptance, empat real-engine instrumentation test selesai sekitar 0,86 detik secara agregat di dalam suite: OCR 0,178 detik, model/tensor/inference fixture 0,481 detik, PDF→OCR 0,169 detik, dan tensor-mismatch 0,036 detik. Angka ini adalah timing fixture kecil, bukan benchmark catalog produksi.

APK debug berukuran 108.434.276 byte, sedangkan release unsigned 99.243.319 byte. Kenaikan terutama berasal dari bundled ML Kit, MediaPipe native runtime, Apache POI, dan tiga model dengan total sekitar 8,54 MiB. Build/install/storage preflight tetap otomatis, tetapi ukuran APK harus menjadi target optimasi Phase 12 dan diuji lagi pada device storage rendah.

## Exit gate dan batas phase

| Gate | Status | Bukti |
| --- | --- | --- |
| F4.1 OCR | Lulus | bundled real inference, region/text bound, blank/corrupt/oversized failure paths, capability `granted` |
| F4.2 Document text | Lulus | PDF/DOC/DOCX/XLS/XLSX/CSV/TXT/RTF, corrupt/encrypted/oversized/cancel/truncate |
| F4.3 Hash dan duplicate | Lulus | streaming SHA-256, deterministic dHash/group/representative, bounded clustering |
| F4.4 Face/object | Lulus | real model hash/tensor/load/inference, unavailable/error path, bounded public signal |

Acceptance historis Phase 06 dinyatakan lulus untuk engine 0.5.0. Keyword matching, weighted scoring, threshold policy, auto-selection, candidate review, immutable revision, dan selection fingerprint tetap dimiliki Phase 07, sedangkan direct manifest serta individual-file hand-off tanpa ZIP dimiliki Phase 08. Follow-up screenshot binding pada agent 0.7.0 tetap `NOT RUN` sampai acceptance operator melalui dashboard.
