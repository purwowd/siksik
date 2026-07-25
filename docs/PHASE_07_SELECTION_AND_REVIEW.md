# Phase 07: Automated Selection and Optional Review

Tanggal verifikasi otomatis: 17 Juli 2026.

## Hasil

Selection core yang diperkenalkan pada SIKSIK Android agent 0.6.0 tetap dipakai oleh agent 0.7.0 untuk menjalankan Flow Step 5 setelah preprocessing terminal: policy selection dibuat oleh backend SIKSIK, diverifikasi di APK, dipakai untuk scoring deterministik, lalu seluruh keputusan disimpan dalam ledger private Android. Record di bawah threshold tetap dipertahankan sampai cleanup. Selection dibekukan dengan revision dan SHA-256 fingerprint sebelum hand-off.

Alur authority tetap mengikuti SIKSIK:

`SessionManager → Android crawl → local preprocessing → local selection → optional review melalui API SIKSIK → verified direct manifest/file hand-off → analyzer SIKSIK → findings/review → recommendation/report`

Agent hanya menentukan record transfer candidate. Agent tidak membuat finding, tidak mengubah review finding, dan tidak menentukan recommendation. Follow-up Phase 08/09 mengonsumsi revision terkonfirmasi melalui record canonical serta file individual tanpa ZIP; agent tidak membuat arsip dan analyzer SIKSIK tetap menjadi authority.

## Policy dan scoring

`SelectionPolicyV1` dibuat oleh backend untuk setiap session dan dikirim melalui bootstrap loopback bertoken. APK tidak memiliki dictionary risiko produksi kedua. Policy memuat versi, dictionary dan match term yang sudah dinormalisasi, seluruh bobot, duplicate policy, threshold, budget, dan fingerprint.

| Signal | Bobot basis point |
| --- | ---: |
| image/video source | 300 |
| audio source | 100 |
| document source | 400 |
| SMS source | 600 |
| contact source | 0 |
| visible UI source | 700 |
| notification source | 500 |
| OCR text | 1.000 |
| document text | 1.100 |
| SMS text | 900 |
| visible UI/notification text | 1.000 |
| keyword hit unik | 4.000 |
| face present | 400 |
| object knife/scissors/person | 1.500/600/200 sebelum confidence weighting |

Threshold adalah 5.500 basis point atau 0,55 dan bersifat inklusif. SMS, document, dan notification dengan satu keyword valid mencapai threshold; OCR image memerlukan fusi signal tambahan sesuai Flow. Quick mode dibatasi 2.500 candidate dan 1 GiB, sedangkan full mode 10.000 candidate dan 8 GiB. Score selalu dibatasi pada rentang 0–1.

Normalisasi dan boundary mengikuti lexicon SIKSIK: lowercase, whitespace stabil, separator whitespace/hyphen/underscore/slash/dot, dan negative alphanumeric boundary. Phrase didahulukan, token fallback mengikuti daftar term dari backend, dan term yang sama tidak diberi bobot berulang. Duplicate non-representative tetap tercatat tetapi tidak otomatis dipilih.

## Ledger, revision, dan review

- Semua record yang selesai preprocessing dievaluasi dan disimpan, termasuk yang berada di bawah threshold.
- Candidate budget diterapkan deterministik dengan urutan score menurun lalu `record_id`.
- Selected set, override, operator, policy fingerprint, dan revision membentuk SHA-256 selection fingerprint.
- Default `review_candidates=false` langsung mengonfirmasi revision pertama dan mempertahankan one-click flow lama.
- `review_candidates=true` berhenti pada `awaiting_review` sampai operator pemilik session atau admin mengonfirmasi.
- Include/exclude/none merupakan override eksplisit dan menaikkan revision.
- Mutasi membawa `expected_revision`; revision lama menghasilkan conflict dan tidak menimpa perubahan baru.
- Confirmation idempotent dan tidak menaikkan revision.
- Selection yang sudah confirmed immutable. Perubahan berikutnya harus menjadi revision/crawl baru sebelum staging.
- Cleanup session menghapus ledger private Android. Backend mempertahankan snapshot audit SIKSIK.

## API

Internal agent route yang ditambahkan:

- `POST/GET /v1/sessions/{session}/crawl/{crawl}/selection`
- `GET /v1/sessions/{session}/crawl/{crawl}/selection/candidates`
- `PATCH /v1/sessions/{session}/crawl/{crawl}/selection/candidates/{record_id}`
- `POST /v1/sessions/{session}/crawl/{crawl}/selection/confirm`
- `POST /v1/sessions/{session}/crawl/{crawl}/selection/cancel`

Public API SIKSIK yang ditambahkan:

- `GET /api/v1/sessions/{session_id}/crawl`
- `GET /api/v1/sessions/{session_id}/candidates`
- `PATCH /api/v1/sessions/{session_id}/candidates/{record_id}`
- `POST /api/v1/sessions/{session_id}/candidates/confirm`

Candidate list mendukung pagination, source filter, selected filter, dan minimum score. Response hanya memuat bounded evidence, score/reason, signal, source app, duplicate relationship, selected state, dan thumbnail availability. Agent token, content URI, absolute path, dan raw face vector tidak keluar melalui API.

Permission `candidates:review` hanya diberikan kepada operator dan admin. Operator dibatasi pada session yang dibuatnya; admin dapat mengoperasikan seluruh session. Mutasi hanya aktif saat `awaiting_review`, sedangkan confirmed snapshot tetap dapat dibaca secara audit dan tidak dapat diubah.

## Perilaku layar putih dan social crawl

`BootstrapActivity` sekarang benar-benar berupa `FrameLayout` putih tanpa status text, progress, komponen baru, atau perubahan style frontend SIKSIK. Selama inventory media, dokumen, SMS, kontak, notification, preprocessing, dan selection berjalan di service/API lokal, layar perangkat tetap menampilkan Activity putih tersebut jika SIKSIK berada di foreground.

Visible social UI tidak dapat diambil secara sah di balik Activity putih yang menutupi aplikasi target. UI Automator dan Accessibility hanya dapat membuktikan node aplikasi yang benar-benar berada di foreground. Karena itu, saat bounded social crawl berlangsung, Instagram/Facebook/Twitter target tampil sementara, hanya menjalankan launch, stable wait, bounded scroll, dan screenshot private. Setelah setiap target—termasuk missing target, cancellation, timeout, atau failure—automation secara eksplisit membuka kembali Activity putih SIKSIK.

Implementasi tidak memakai overlay untuk menyembunyikan target, tidak melakukan click aksi sosial, tidak memasukkan credential, dan tidak melewati konfirmasi special access Android. Ini menjaga hasil visible-UI tetap selaras dengan Flow dan provenance yang dapat dipertanggungjawabkan.

## Audit `forensics-refrences`

| Referensi | Keputusan | Alasan |
| --- | --- | --- |
| `chat4n6` | Pola konseptual saja | Ide provenance, hash manifest, dan pagination sejalan untuk Phase 08/09; tidak diperlukan sebagai runtime atau parser Phase 07. |
| `LockKnife` | Tidak dipakai | GPLv3 dan mayoritas teknik root/private database/exploit/decryption berada di luar boundary no-root, public API, visible UI SIKSIK. |
| `folder-lock-decrypt-android` | Tidak dipakai | Decryption khusus aplikasi dan lisensi yang tidak jelas tidak sejalan dengan selection/social crawl saat ini. |

Tidak ada source, dependency, runtime path, private-database parser, exploit, atau decryption code yang disalin dari folder referensi. Konsep custody/hash yang relevan sudah diterapkan ulang dalam kontrak SIKSIK sendiri.

## Donor LPDP

Pola yang direfactor adalah expected-revision conflict, immutable confirmation, canonical fingerprint, deterministic ordering, dan pagination. Backend, database, API, UI, serta runtime LPDP tidak dibawa ke SIKSIK. Tidak ada import atau path runtime ke repository LPDP.

## Verifikasi otomatis

- Backend full suite: 190 lulus dan 4 environment-optional dilewati sebelum tambahan test manual-review runner; test Phase 07 dan regression runner setelah refactor juga lulus.
- Test khusus policy/repository/public review: normalization, fingerprint, ownership, pagination/filter, include, stale revision, idempotent confirmation, dan immutability lulus.
- Android unit debug dan release: masing-masing 56 lulus.
- Android test baru mencakup canonical policy mismatch, word boundary, equal threshold, duplicate representative, below-threshold ledger, budget, revision conflict, include, idempotent confirm, dan immutable confirm.
- Android lint debug/release, agent debug/release build, automation APK build, dan instrumentation APK compile lulus.
- Frontend strict TypeScript serta production Vite build lulus. Tidak ada stylesheet, layout, route, atau format visual frontend yang diubah.
- APK agent debug berukuran 108.499.524 byte; release unsigned 99.259.427 byte; automation debug 764.735 byte.

## Physical acceptance yang tertunda

Saat final physical command dijalankan pada 17 Juli 2026, `adb devices -l` tidak menampilkan perangkat. Karena itu test berikut belum diklaim lulus pada Phase 07:

- Activity putih pada perangkat nyata selama background crawl
- instrumentation ledger selection pada Android nyata
- Instagram/Facebook bounded crawl dan explicit return ke layar putih
- manual special-access confirmation
- auto-confirm dan manual-review end-to-end melalui backend hidup

APK agent, automation APK, dan instrumentation APK sudah dibangun sehingga test dapat langsung dijalankan ketika satu perangkat berstatus `device` tersambung. Semua ADB command wajib tetap dipin ke serial tersebut.

## Exit gate

Gate otomatis F5.1 keyword matching, F5.2 weighted scoring, F5.3 auto-selection, serta F6.1 optional review lulus. Phase 07 belum dinyatakan lulus penuh karena physical selection/social acceptance belum dijalankan pada perangkat yang tersambung. Implementasi Phase 08/09 kemudian dilanjutkan atas instruksi eksplisit operator, tetapi tidak mengubah status physical exit gate Phase 07 yang masih `NOT RUN`.
