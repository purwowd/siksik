# Progress Implementasi Flow SIKSIK

Terakhir diperbarui: 19 Juli 2026.

Dokumen ini adalah tracker implementasi `Flow.md` di repository SIKSIK. Status dinilai dari acceptance gate, bukan hanya keberadaan source atau keberhasilan compile.

## Sumber kebenaran

Urutan keputusan yang dipakai:

1. Arsitektur, API, database, analysis, findings, RBAC, UI, dan report milik SIKSIK.
2. `Flow.md` dan diagram menentukan capability crawling, preprocessing, selection, review, dan hand-off yang wajib tersedia.
3. Paket `siksik-flow-ai-prompts` menentukan urutan phase, kontrak, batas keamanan, dan acceptance gate.
4. LPDP hanya donor read-only dan tidak boleh menjadi runtime dependency.

Diagram dan `Flow.md` masih menggambarkan bundling JSON/ZIP pada hand-off. Keputusan arsitektur yang lebih spesifik mengunci jalur Android agent menjadi manifest JSON, record JSON/NDJSON, dan file individual terverifikasi tanpa ZIP. Upload ZIP lama SIKSIK tetap tersedia sebagai provider terpisah.

## Status ringkas Flow

| Flow | Phase | Status | Bukti dan batas saat ini |
| --- | --- | --- | --- |
| Step 1: ADB initialization | 01–03 | Lulus historis; follow-up NOT RUN | Build/install/update, permission, foreground service, dynamic forward, authenticated handshake, dan teardown memiliki bukti lama. Follow-up membatasi UX ke Accessibility/Storage serta mengotomasi Notification Listener; perubahan ini belum diuji. |
| Step 2: media dan dokumen | 04 | Lulus otomatis; fisik tertunda | Tujuh adapter, cursor, metadata, EXIF/GPS, audio, dokumen, WhatsApp/Telegram publik, quick/full, dan failure state tersedia. Instrumentation fisik adapter belum dijalankan. |
| Step 3: komunikasi dan sosial | 05, 07 | Implementasi account-owned; validasi operator tertunda | SMS, kontak, notification, blank-white lifecycle, dan explicit return tersedia. Target runtime default difokuskan ke Instagram dan X. Instagram membuka item post akun serta item arsip story; X tetap pada tab akun Posts/Replies agar thread akun lain tidak ikut tercrawl. Perubahan ini belum dijalankan. |
| Step 4: local preprocessing | 06 | Lulus historis; optimization follow-up NOT RUN | Real OCR/document/hash/face/object engine memiliki bukti pada Samsung API 34. Follow-up mengikat screenshot social scoped ke dHash/OCR/face/object, memakai ulang satu decode visual, dan memberi budget full/document terpisah; perubahan ini belum dibangun atau dijalankan. |
| Step 5: automated selection | 07 | Lulus otomatis; fisik tertunda | Backend-owned policy, word-boundary scorer, duplicate representative, below-threshold ledger, budget, revision, dan fingerprint tersedia. Instrumentation fisik belum dijalankan. |
| Step 6: review dan hand-off | 07–10 | Implementasi Phase 08/09 tersedia; belum diuji | Revision terkonfirmasi menghasilkan manifest, record canonical, dan file individual terverifikasi; satu recursive ADB pull tanpa arsip masuk tabel crawl/files lalu analyzer resmi SIKSIK. Phase 10 hanya menambah binding preview opsional tanpa mengganti UI existing. |
| Regression, security, release | 11–12 | Optimasi awal implemented, NOT RUN | Benchmark read-only, phase timing, batching, decode reuse, dan pengurangan I/O tersedia; regression serta benchmark perangkat belum dijalankan. |

## Status per phase

| Phase | Status gate | Catatan |
| ---: | --- | --- |
| 00 Baseline recovery | Lulus | Baseline backend/frontend/database dibekukan. |
| 01 Backend convergence | Lulus | Provider boundary, ADB transport, artifact service, agent client, migration, logging, dan regression tersedia. |
| 02 Android agent refactor | Lulus | Agent mandiri, loopback-only API, auth/session, staging, manifest, cleanup, dan automation shell tersedia. |
| 03 ADB auto-bootstrap | Lulus historis; follow-up NOT RUN | Acceptance lama tercatat. Auto Notification Listener, Android-user-aware probes, ADB discovery, install error classification, dan UX dua akses belum dijalankan. |
| 04 Media/document crawling | Belum lulus penuh | Automated gate lulus; physical adapter acceptance belum dijalankan. |
| 05 Communication/social crawling | Belum lulus penuh | Automated gate lulus; physical social/communication acceptance belum dijalankan. |
| 06 Local preprocessing | Lulus historis; follow-up NOT RUN | F4.1–F4.4 memiliki bukti lama; binding screenshot `visible_ui` ke seluruh visual engine belum diuji. |
| 07 Automated selection/review | Belum lulus penuh | Automated gate lulus; physical selection dan social-return acceptance tertunda karena tidak ada perangkat ADB saat verifikasi final. |
| 08 Direct ingestion | Implemented, NOT RUN | Jalur Android agent tidak lagi memakai fallback broad ADB: direktori manifest dan file individual dipull satu kali secara serial-pinned tanpa ZIP, lalu setiap artifact diverifikasi, dicatat, dan di-cleanup dengan receipt. |
| 09 Analysis integration | Implemented, NOT RUN | MIME crawl-record dianalisis hanya dari `normalized_text`; screenshot/binary memakai analyzer SIKSIK existing. |
| 10 UI integration | Additive preview, NOT RUN | Dashboard existing tetap menjadi jalur test operator. Kontrak finding mendapat field preview opsional; komponen preview existing menampilkan artifact media terverifikasi atau cuplikan teks ternormalisasi tanpa mengubah stylesheet, layout, route, navigasi, maupun format halaman. |
| 11–12 | Optimasi awal implemented, NOT RUN | Menunggu hasil dashboard, regression, dan `tests/full_scan_benchmark.py` pada perangkat/dataset pembanding yang sama. |

## Phase 06: hasil final

| Workstream | Status | Bukti |
| --- | --- | --- |
| Typed preprocessor contracts | Lulus | OCR, document text, exact/perceptual hash, face embedding, dan object detection memiliki interface, capability, execution, duration, warning, dan bounded output. |
| Dependency dan model asset | Lulus | ML Kit 16.0.1 bundled, MediaPipe 0.10.35, POI 5.5.1, tiga model ber-hash, tensor registry, license, source URL, dan real health inference tersedia. |
| OCR | Lulus | Real text/region inference serta blank, corrupt, oversized, timeout, normalization, dan truncation paths diuji fisik. |
| Document text | Lulus | PDF, DOC, DOCX, XLS, XLSX, CSV, TXT, RTF serta corrupt/encrypted/oversized/cancel/truncate diuji. |
| Hash dan duplicate grouping | Lulus | Streaming SHA-256 sampai 4 GiB, deterministic dHash, bounded BK-tree, exact/perceptual membership, dan stable representative tersedia. |
| Face/object inference | Lulus | BlazeFace, MobileNet V3 embedder, EfficientDet-Lite0, model hash/tensor check, real inference, failure capability, bounded signal, dan anonymous cluster tersedia. |
| Scheduler dan persistence | Lulus | Bounded concurrency, timeout, deadline, resource pause, SQLite checkpoint/restart, per-record dan per-preprocessor counter, opaque paging, cancellation race protection, migration, serta cleanup diuji. |
| Crawl/API/backend wiring | Lulus historis; optimization NOT RUN | Crawl record disimpan, preprocessing route authenticated, capability jujur, strict `PreprocessResultV1`, progress session, diagnostic result pagination, dan terminal count validation aktif. Runner normal tidak lagi men-stream ulang seluruh result yang tidak dipakai. |
| Tests dan acceptance | Lulus | 53 unit debug + 53 release, 25 physical instrumentation, backend 187/4, lint/build debug-release, dan frontend production build lulus. |
| Completion report | Ada | `docs/PHASE_06_LOCAL_PREPROCESSING.md`. |

F4.1 OCR, F4.2 document text, F4.3 hash/duplicate, dan F4.4 face/object dinyatakan lulus. Physical inference dijalankan pada Samsung SM-A736B, Android 14, API 34, dengan ADB serial-pinned. Isi koleksi perangkat tidak direkam dalam acceptance log.

## Defect Phase 06 yang ditutup

1. Normalisasi whitespace sekarang terpisah dari status truncation.
2. Document cancellation dipetakan sebagai cancelled, bukan read failure.
3. RTF truncation dipropagasikan eksplisit.
4. PDF OCR failure menghasilkan partial/failed warning yang jujur.
5. XLSX shared strings diselesaikan secara semantik tanpa bergantung urutan archive entry.
6. Duplicate clustering memakai bounded BK-tree dan face clustering memiliki signal limit.
7. Exact SHA-256 memakai batas staging 4 GiB, bukan batas visual 64 MiB.
8. Model registry memvalidasi asset hash, header, tensor metadata, dan real inference.
9. Capability production hanya `granted` setelah health inference lulus.
10. Cancellation, late-worker race, timeout, deadline, restart, cursor, counter, dan cleanup memiliki physical instrumentation evidence.

## Risiko tersisa lintas phase

1. APK 0.5.0 berukuran 108.434.276 byte debug dan 99.243.319 byte release unsigned. Storage/install latency dan packaging perlu dioptimalkan pada Phase 12 tanpa menurunkan capability.
2. Phase 04 masih memerlukan physical catalog crawl dengan representative media/document collection; fixture instrumentation saja tidak menutup gate tersebut.
3. Phase 05 masih memerlukan physical SMS/contact/social/notification lab sesuai matrix; provider fixture instrumentation saja tidak menutup gate tersebut.
4. Implementasi direct ingestion belum memiliki hasil eksekusi pada sesi ini. Hash mismatch, reconnect, cleanup receipt, canonical analysis, dan scope sosial perlu divalidasi melalui script/dashboard operator.
5. Satu perangkat Samsung API 34 membuktikan Phase 06; device/API/OEM matrix yang lebih luas tetap bagian regression Phase 11.
6. Dukungan Android adalah API 26 ke atas sesuai `minSdk`; tidak ada klaim bahwa kebijakan OEM, ABI, USB authorization, atau device policy dapat dibypass. Kondisi tersebut harus gagal eksplisit.

## Urutan berikutnya

1. Operator menjalankan satu sesi dari dashboard untuk scope sosial yang tersedia pada akun perangkat.
2. Operator menjalankan `tests/dashboard_social_flow_check.py` terhadap session tersebut tanpa mencetak konten.
3. Defect hasil dashboard ditutup tanpa memperluas social scope atau mengubah style frontend.
4. Phase 11–12 dilanjutkan setelah gate perangkat, transfer, analyzer, dan cleanup terbukti.

## Verifikasi historis sebelum follow-up 0.7.0

Perintah final dijalankan pada 17 Juli 2026:

- Backend: `187 passed, 4 skipped` dalam 67,37 detik.
- Frontend: strict TypeScript dan Vite production build lulus; tidak ada perubahan stylesheet/layout/route.
- Android unit debug: 53 lulus.
- Android unit release: 53 lulus.
- Android lint debug dan release: lulus.
- Android `assembleDebug` dan `assembleRelease`: lulus untuk agent 0.5.0/versionCode 5.
- Android physical instrumentation: `25 passed, 0 failed, 0 skipped` pada Samsung SM-A736B Android 14/API 34.
- Static boundary audit: tidak ada runtime reference ke LPDP, `ZipOutputStream` di source agent, debug print, emoji comment, atau attribution AI.

Follow-up account-owned crawler serta Phase 08/09 tidak menjalankan build, test, instrumentation, atau crawl perangkat. Ini disengaja sesuai instruksi operator; statusnya tetap `NOT RUN`, bukan lulus.

## Follow-up aktif: scoped visual signal dan ADB UX

- `visible_ui.attachment_ids` sekarang dibaca dari canonical crawl record dan hanya di-resolve melalui screenshot private dengan pasangan `session_id`/`crawl_id` yang sama.
- Screenshot tersebut menjadi input Android dHash, OCR, face, dan object preprocessing. OCR digabung secara bounded dengan UI-node text sehingga scorer Phase 07 menerima sinyal teks dan visual tanpa menambah target/scope crawl.
- Notification tetap source komunikasi terpisah, tetapi notification dari Instagram/X/Facebook ditolak pada listener dan store sehingga tidak dapat memasukkan DM, rekomendasi, like, follow, atau activity di luar scope account-owned social.
- Build/reuse, install/update agent, install automation APK, runtime permission, start, forward, handshake, restart, dan teardown tetap diorkestrasi backend melalui ADB serial-pinned.
- Accessibility wajib dikonfirmasi user. Storage/All files hanya dibuka pada full mode. Notification Listener dicoba otomatis dan diverifikasi untuk Android user aktif; kegagalan menjadi sumber parsial tanpa layar izin ketiga.
- Audit statis 17 Juli 2026 memastikan coordinator tidak membuka Notification Settings, tidak menulis secure settings, dan seluruh command perangkat tetap memakai serial tervalidasi serta Android user aktif. Audit ini bukan bukti runtime.
- Script fake-transport `tests/android_adb_automation_contract.py` dan validator read-only `tests/dashboard_social_flow_check.py` tersedia, tetapi tidak dijalankan. Validator dashboard dapat mewajibkan screenshot, empat sinyal visual Android, seluruh artifact analyzed, serta tidak adanya notification package sosial. Build, test, instrumentation, dan device crawl untuk follow-up ini seluruhnya berstatus `NOT RUN`.

## Follow-up aktif: kompatibilitas OEM dan payload inventory

- Diagnosis read-only pada Infinix X6837 menemukan `am start -W` mengembalikan exit 0 dan `Status: ok`, tetapi juga peringatan bahwa activity tidak dibuat ulang karena intent dikirim ke Settings yang sudah berada di depan. Parser lama salah menganggap setiap teks `Activity not started` sebagai kegagalan.
- Parser Settings sekarang menerima delivery ke instance aktif dan output exit-0 tanpa marker kegagalan, tetapi tetap menolak unresolved intent, activity tidak ada, security exception, permission denial, status non-OK, dan exit nonzero.
- Kegagalan mode Cepat merupakan masalah terpisah: satu respons inventory melewati batas lama 1 MiB. Client production kini memakai batas default 4 MiB dengan konfigurasi tervalidasi 1–16 MiB.
- Pagination sekarang memakai media/dokumen/publik 100, SMS 64, kontak 100, visible UI 1, notifikasi 50, dan candidate selection 50 di bawah ceiling respons 4 MiB. Backend tidak lagi mengunduh ulang seluruh payload preprocessing hanya untuk menghitungnya; terminal totals tetap divalidasi dan endpoint record tetap tersedia. Cursor inventory/candidate tetap diikuti sampai terminal sehingga perubahan tidak mengurangi scope Full maupun budget sampling Cepat.
- Notification Listener yang berhasil diperintah lewat ADB diprobe ulang secara bounded sebanyak tiga kali untuk menampung propagasi state asynchronous pada OEM, tanpa membuka layar izin ketiga.
- Script kontrak diperbarui dengan bentuk output OEM Infinix dan batas pagination/response. Sesuai instruksi operator, script, build, instrumentation, dan crawl tidak dijalankan; seluruh perubahan pada bagian ini tetap `NOT RUN` sampai dicoba dari dashboard.

## Follow-up aktif: preview finding dan fokus Instagram/X

- Finding yang berasal dari record canonical tidak lagi bergantung pada ekstensi `.siksik-record.json` untuk menentukan preview. API mencari relasi record yang sama dan hanya memilih artifact `source_binary` atau `screenshot` yang terverifikasi serta bertipe image/video.
- Jika finding bukan image/video, API mengirim cuplikan `normalized_text` maksimal 320 karakter. Jika teks canonical tidak tersedia, cuplikan memakai evidence analyzer yang sudah bounded. Canonical JSON dan isi berkas mentah tidak dijadikan payload preview.
- Field `preview_path` dan `preview_text` bersifat opsional sehingga client lama tetap kompatibel. Frontend memakai komponen, ukuran, class, layout, serta stylesheet SIKSIK yang sudah ada; tidak ada style atau format halaman yang diganti.
- Target runtime default social crawl saat ini hanya Instagram dan X. Dukungan Facebook tetap berada di kontrak kompatibilitas tetapi tidak dijalankan oleh konfigurasi default.
- Instagram membuktikan profil akun melalui Edit profile, membuka item pada grid post, lalu hanya melanjutkan post berikutnya ketika marker author tetap cocok. Story Archive dibuka dari profil akun, item arsip dipilih, dan perpindahan story dibatasi selama viewer archive tetap terbukti.
- X membuktikan profil akun melalui Edit profile dan handle, lalu menangkap tab Posts dan Replies dengan scroll terbatas. Automation tidak membuka thread tweet karena layar thread dapat memuat reply dari akun lain dan melanggar boundary account-owned.
- Parser hasil instrumentation menerima status/result line dari stdout maupun stderr tanpa meneruskan output mentah ke error publik atau log.
- Seluruh perubahan follow-up 19 Juli 2026 ini berstatus `IMPLEMENTED, NOT RUN`. Tidak ada build, test, instrumentation, atau crawl perangkat yang dijalankan; acceptance tetap dilakukan operator melalui dashboard.

## Follow-up aktif: optimasi full scan

- Full inventory tetap mencakup seluruh cursor gambar, dokumen, SMS, dan source lain yang terminal; page batching hanya mengurangi round-trip loopback.
- Visual preprocessing men-decode frame/gambar sekali pada base bounded 4 juta pixel, lalu memberikan bitmap owned kepada dHash, OCR, face, dan object engine. Foto beresolusi besar di-downsample dan tidak lagi ditolak hanya karena dimensi aslinya melebihi cap decoder.
- Deadline preprocessing tetap 10 menit untuk quick dan menjadi 40 menit untuk full. Item dokumen pada full mendapat timeout 120 detik; PDF tetap bounded 24 halaman dan melaporkan truncation eksplisit ketika lebih panjang.
- Exact hashing serta copy staging memakai buffer 256 KiB. State transfer durable disimpan setiap 16 record dan pada terminal, bukan fsync setiap record; binary dan manifest tetap disinkronkan serta setiap artifact tetap memiliki SHA-256.
- Backend menarik satu direktori stage melalui ADB tanpa archive, memverifikasi manifest serta seluruh file satu per satu, membatch insert SQLite, dan memakai ulang hash/metadata canonical terverifikasi saat indexing. Sumber non-agent tetap dihitung hash dan metadata melalui jalur lama.
- Instagram/X full memakai budget 24 perpindahan per target. Wait internal yang duplikat pada post/story Instagram dihapus; engine tetap menunggu UI stabil satu kali sebelum capture. Scope tetap hanya konten akun perangkat.
- `tests/full_scan_benchmark.py` membaca database secara read-only, memeriksa source state/count/timing, dan hanya menerima klaim lebih cepat bila baseline memakai perangkat, dataset, scope, serta batas waktu yang sama.
- Seluruh follow-up optimasi ini berstatus `IMPLEMENTED, NOT RUN`; tidak ada build, test, instrumentation, maupun crawl perangkat yang dijalankan.

## Aturan pembaruan tracker

- Perbarui status hanya setelah bukti test atau physical acceptance tersedia.
- Catat command, jumlah test, device/API/OEM, dan limitation tanpa menyimpan isi koleksi.
- Jangan mengubah status `Belum lulus penuh` menjadi `Lulus` jika physical exit gate masih `NOT RUN`.
- Implementasi phase berikutnya hanya boleh mendahului gate jika operator memerintahkannya secara eksplisit; status acceptance tetap terpisah dan tidak boleh dinaikkan tanpa bukti.
