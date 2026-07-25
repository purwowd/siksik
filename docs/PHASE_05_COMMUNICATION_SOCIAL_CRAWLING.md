# Phase 05: Communication and Social Crawling

Tanggal verifikasi: 16 Juli 2026.

## Hasil

Android agent SIKSIK menambahkan seluruh sumber Flow Step 3: SMS, kontak, visible social UI, dan notifikasi selama sesi aktif. Implementasi tidak memakai root, tidak membaca database private aplikasi target, tidak mengubah data aplikasi target, dan tidak memasukkan dependency runtime dari LPDP.

Pada implementasi inti Phase 05, source frontend SIKSIK tidak diubah. Follow-up 19 Juli 2026 hanya menambah binding preview opsional pada kontrak finding dan komponen preview existing; route halaman, layout, stylesheet, navigasi, dan format UI lama tetap sama. Existing ZIP upload juga tetap berfungsi sebagai provider terpisah.

## Penguncian scope akun perangkat

Follow-up agent 0.7.0 mengganti strategi feed generik dengan strategi account-owned yang fail-closed:

| Target | Scope yang boleh disimpan | Bukti akun sendiri sebelum capture |
| --- | --- | --- |
| Instagram | `own_posts`, `own_story_archive`, `own_comments` | profile tab dan Edit profile/Sunting profil; archive atau Your activity diverifikasi lagi |
| X | `own_tweets`, `own_replies` | drawer Profile/Profil dan resource Edit profile; tab Posts/Replies dipilih eksplisit |
| Facebook | `own_posts`, `own_story_archive`, `own_comments` | See your profile/Lihat profil Anda dan Edit profile/Sunting profil; archive atau Activity log diverifikasi lagi |

Home feed, rekomendasi, profil akun lain, direct message, like, follow, dan activity yang tidak menuju komentar akun perangkat tidak disimpan. Capture scope baru diaktifkan setelah marker akun sendiri terbukti. Accessibility Service dan UiAutomation hanya dapat menulis ketika package serta `social_scope` aktif cocok; fallback screenshot tanpa scope telah dihapus.

Notification tetap merupakan source komunikasi terpisah sesuai Flow.md dan hanya aktif selama crawl. Notification dari package social target Instagram, X, dan Facebook ditolak di listener serta persistence boundary agar DM, rekomendasi, like, follow, atau activity notification tidak masuk sebagai pengganti scope account-owned social.

Perubahan follow-up ini belum dijalankan pada perangkat atau test runner dalam sesi implementasi ini sesuai instruksi operator. Script validasi disediakan di folder `tests`, dan acceptance aktual dilakukan operator melalui dashboard.

## Source adapter

Inventory memakai sebelas adapter berurutan. Tujuh adapter Phase 04 tetap dipertahankan dan empat adapter berikut ditambahkan:

| Adapter | API Android | Record bertipe |
| --- | --- | --- |
| `sms_content_provider` | `Telephony.Sms` | `sms` |
| `contacts_content_provider` | `ContactsContract` | `contact` |
| `accessibility_visible_ui` | `AccessibilityService` | `visible_ui` |
| `notification_listener` | `NotificationListenerService` | `notification` |

SMS dan kontak memakai projection terbatas, checkpoint ID stabil, pagination, resume, cancel, normalisasi, hash identitas opaque, dan status provider eksplisit. Detail kontak dibaca dalam satu query batch per halaman, bukan query N+1. Perangkat tanpa fitur telephony melaporkan sumber SMS sebagai `unsupported`; permission atau provider yang hilang tidak dinyatakan sebagai sukses kosong.

Visible UI menyimpan text/content description, bounds, package, window, activity context, event time, dan screen sequence ke SQLite private agent. Notifikasi hanya diterima ketika sesi crawl aktif, di-upsert memakai identitas stabil, membedakan update dan removal, serta berhenti menerima data setelah sesi terminal.

## Permission dan special access

| Akses | Berlaku | Perilaku bootstrap |
| --- | --- | --- |
| `READ_SMS` | Android API 26 ke atas dengan telephony | diminta otomatis melalui ADB; penolakan menjadi `denied`, bukan menggagalkan sumber lain |
| `READ_CONTACTS` | Android API 26 ke atas | diminta otomatis melalui ADB; penolakan menjadi `denied` |
| Accessibility Service | semua API target | halaman Settings dibuka dan dipantau otomatis; konfirmasi OS tetap dilakukan user |
| Notification Listener | semua API target | diaktifkan dan diverifikasi lewat `cmd notification allow_listener`; kegagalan OEM menjadi sumber parsial tanpa membuka layar izin tambahan |
| Full shared storage | Android API 30 ke atas, full mode | halaman All files access dibuka dan dipantau; penolakan membuat dokumen shared-storage parsial |

`android.hardware.telephony` dinyatakan opsional agar perangkat tanpa radio tetap dapat memakai adapter lain. Access yang dicabut saat crawl menghasilkan `partial` atau `denied` dengan reason stabil. Capture hanya aktif untuk session/crawl yang cocok dan dibersihkan oleh lifecycle agent.

Bootstrap, build/reuse, install/update APK agent, install APK automation, runtime grants, start, ADB forwarding, handshake, restart, dan teardown dilakukan backend tanpa command manual. Seluruh command terikat ke serial dan Android user aktif. UX keamanan pada HP dibatasi pada Accessibility serta Storage ketika full shared-storage membutuhkannya. USB debugging authorization dan kebijakan OEM tetap merupakan prasyarat platform; kondisi yang diblokir dilaporkan eksplisit dan tidak disamarkan sebagai sukses.

## Automation read-only

Automation dibuat sebagai instrumentation APK terpisah dan backend membangun, memasang, serta menjalankannya melalui ADB yang selalu terikat ke serial perangkat. Target yang didukung kontrak tetap adalah:

- `com.twitter.android`
- `com.facebook.katana`
- `com.instagram.android`

Konfigurasi runtime default follow-up 19 Juli 2026 hanya menjalankan `com.twitter.android` dan `com.instagram.android` agar acceptance saat ini fokus pada X dan Instagram. Facebook dipertahankan sebagai compatibility capability dan tidak menjadi target crawl default.

Instagram tidak berhenti di halaman profil atau halaman Archive: automation memilih item grid post akun dan item Story Archive sebelum capture, lalu berpindah terbatas selama marker akun/surface masih valid. X tetap memilih serta menggulir tab akun Posts dan Replies; thread tweet tidak dibuka karena dapat menampilkan reply akun lain di luar boundary.

Setiap target memakai interface strategi navigasi. Operasi yang diizinkan hanya existence check, launch, navigasi read-only ke scope di atas, stable wait, bounded scroll, screenshot private, dan back navigation. Budget scroll dibagi deterministik ke seluruh scope target. Quick mode dibatasi tiga scroll/enam screenshot; full mode dua belas scroll/enam belas screenshot. Budget screenshot tetap per target dan memberi ruang untuk initial capture serta hasil scroll pada arsip story, posting, dan komentar. Timeout target adalah 90 detik. Automation tidak melakukan click aksi, kirim pesan, post, like, follow, delete, purchase, perubahan setting target, atau input credential.

Hasil instrumentation merupakan JSON bertipe yang memuat target, state, reason aman, jumlah scroll, ID screenshot, dan durasi. Output ditolak bila target tidak cocok, field berlebih, payload terlalu besar, atau kontrak tidak valid. Cancellation hanya menghentikan package instrumentation SIKSIK, bukan package target.

## Batas capture dan wire

| Batas | Nilai |
| --- | --- |
| event visible UI per package | minimum interval 500 ms |
| depth tree | 16 |
| node per snapshot | 256 |
| text/content description per node | 512 karakter |
| bounds koordinat | -100.000 sampai 100.000 |
| normalized visible text | 32.768 karakter |
| SMS body | 32.768 karakter |
| normalized contact text | 8.192 karakter |
| capture record per jenis/sesi | 5.000 |
| page media/public WhatsApp/public Telegram | maksimum 25 record |
| page shared storage/document tree | maksimum 20 record |
| page SMS | maksimum 10 record |
| page kontak | maksimum 10 record |
| page visible UI | satu record |
| page notifikasi | maksimum 5 record |
| page hasil preprocessing | maksimum 5 record |
| page candidate selection | maksimum 25 record |
| respons loopback backend | default 4 MiB, dapat dikonfigurasi 1–16 MiB |

Snapshot visible UI dideduplikasi per crawl dan content hash. Target package dibatasi secara dinamis pada service serta divalidasi ulang saat penyimpanan. Screenshot berada di private app storage dan hanya ID aman yang melewati wire. Content URI, notification key mentah, raw checkpoint, bearer token, dan path private tidak melewati kontrak publik.

Batas halaman di atas hanya memecah pagination. Full mode tetap mengikuti cursor sampai setiap source terminal dan quick mode tetap mengikuti budget sampling yang sama, sehingga kompatibilitas payload tidak mengurangi scope. Batas respons diatur oleh `SADT_ANDROID_AGENT_MAX_RESPONSE_MB`; nilai default 4 MiB tetap memiliki hard ceiling 16 MiB pada client.

## Logging dan data sensitif

Body SMS, nomor telepon, OTP, alamat mentah, data kontak, visible text, dan isi notifikasi tidak pernah ditulis ke log. Structured log hanya menerima allowlist field operasional seperti request/session/crawl ID, package target yang telah divalidasi, state, count, dan duration. Error publik memakai kategori serta pesan generik dan tidak meneruskan output instrumentation atau source content.

## Verifikasi

- Backend penuh: 186 test lulus, 4 test environment-optional dilewati.
- Android unit test debug: 35 lulus.
- Android unit test release: 35 lulus.
- Android instrumentation source berhasil dikompilasi untuk projection/pagination SMS-kontak, provider kosong/rusak, protected capture store, allowlist, dedupe, restart, revocation, notification lifecycle, route loopback, serta cleanup.
- Automation instrumentation berhasil dikompilasi untuk launch/wait/scroll/screenshot/back, missing/changed target, cancellation, timeout, dan exact target registry.
- Android lint debug/release: lulus.
- APK agent debug/release dan APK automation debug: berhasil dibangun.
- Frontend production build: lulus tanpa perubahan source frontend.
- Acceptance backend membuktikan field sensitif tidak masuk structured log atau error publik dan provider non-Android tidak pernah men-dispatch Android agent runner.

Instrumentation Phase 05 belum dijalankan pada perangkat fisik karena `adb devices -l` tidak melihat perangkat saat gate ini. Karena itu exit gate lab untuk satu aplikasi social dicatat belum terverifikasi secara fisik pada kombinasi API/OEM sesi ini; build, kontrak, dan seluruh fixture tetap lulus. Bootstrap fisik Phase 03 sebelumnya berhasil pada Infinix X6837 API 33, tetapi hasil tersebut tidak digunakan sebagai klaim uji fisik Flow Step 3.

## Batas phase

Phase ini hanya membentuk typed inventory Flow Step 3 dan orchestration read-only. OCR, hash preprocessing, entity extraction, face/object signal, scoring, review, direct ingestion, integrasi analyzer, dan UI tambahan tidak diaktifkan lebih awal. SIKSIK tetap menjadi authority untuk analysis, findings, review, recommendation, report, dashboard, dan timeline.
