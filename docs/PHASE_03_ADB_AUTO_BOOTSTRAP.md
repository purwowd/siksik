# Phase 03: Auto-Bootstrap Android melalui ADB

Tanggal verifikasi: 16 Juli 2026.

## Hasil

- Sesi Android live yang dimulai melalui `POST /api/v1/sessions` otomatis menjalankan bootstrap agent sebelum akuisisi.
- `POST /api/v1/agent/bootstrap` tersedia untuk retry/preflight eksplisit dan `GET /api/v1/agent/status?device_id=...` menyediakan status bertipe.
- Backend membangun atau memakai ulang APK tervalidasi, membaca application ID, version code/name, SHA-256 APK, serta SHA-256 signer sebelum memilih `install`, `update`, atau `current`.
- Install dan update memakai `adb install -r -g`; hasil install ditarik kembali dan diverifikasi terhadap hash, signer, serta version code artifact yang diinginkan.
- Izin runtime dipilih menurut API Android, diberikan hanya melalui `pm grant`, lalu diverifikasi dari metadata package untuk Android user aktif.
- Accessibility, Notification Access, dan all-files access dimodelkan terpisah sebagai special access. Accessibility dan all-files membuka Settings Android serta menunggu keputusan pengguna dengan deadline terbatas. Notification Listener dicoba melalui `cmd notification allow_listener` dan diverifikasi untuk Android user aktif tanpa membuka layar konfirmasi ketiga. Backend tidak menulis secure settings.
- Activity agent dijalankan dengan session ID, token acak berumur pendek, dan expiry. Token hanya berada di memory serta fingerprint-nya di persistence; token dan serial mentah tidak dikembalikan API atau dicatat log.
- Forward memakai host port dinamis ke loopback agent pada port perangkat `38471`. Handshake memverifikasi session, API version, API port, package, API Android, version agent, dan build identity.
- Forward dan proses agent milik sesi dibersihkan pada failure, cancellation, expiry, teardown, process death, atau shutdown backend. Bootstrap baru merotasi runtime lama dan menghapus forward persisten yang tertinggal.
- Android agent tetap terpasang setelah teardown sehingga sesi berikutnya dapat memilih `current` tanpa install ulang.

## State machine

Urutan sukses yang diterapkan dan dipersistensikan sebagai event:

~~~text
detect_device
→ validate_device
→ resolve_or_build_agent
→ inspect_installed_package
→ install_or_update
→ apply_runtime_permissions
→ verify_special_access
→ start_agent
→ create_forward
→ authenticate_and_negotiate
→ ready
~~~

Jika special access wajib belum disetujui, transisi `awaiting_access` diterbitkan setelah `verify_special_access`, lalu alur kembali berjalan otomatis setelah approval. State terminal tambahan adalah `failed`, `cancelled`, `degraded`, dan `closed`.

Trace perangkat fisik dengan identitas perangkat di-hash:

~~~text
device_ref=android:fe3470fb72f08f4830033b74
first_install_action=install
detect_device → validate_device → resolve_or_build_agent
→ inspect_installed_package → install_or_update
→ apply_runtime_permissions → verify_special_access
→ start_agent → create_forward → authenticate_and_negotiate → ready
teardown → forward removed → process stopped
second_install_action=current
detect_device → validate_device → resolve_or_build_agent
→ inspect_installed_package → install_or_update
→ apply_runtime_permissions → verify_special_access
→ start_agent → create_forward → authenticate_and_negotiate → ready
teardown → forward removed → process stopped
~~~

## Struktur kode

- `backend/app/acquisition/bootstrap.py` mengorkestrasi state machine, persistence progress, recovery, dan lifecycle runtime.
- `backend/app/acquisition/bootstrap_components.py` memisahkan pemeriksaan/install package, izin/special access, dan handshake.
- `backend/app/acquisition/bootstrap_contracts.py` memuat kontrak konfigurasi, working state, progress, dan matriks izin API.
- `backend/app/acquisition/bootstrap_runner.py` menjadi adapter provider sementara sampai crawling agent Phase 04 menggantikan akuisisi ADB legacy.
- `backend/app/acquisition/apk_metadata.py` menjalankan `apkanalyzer` dan `apksigner` melalui argv terbatas tanpa shell.
- `backend/app/acquisition/runtime.py` menyimpan status aman, trace event, dan secret runtime hanya di memory.
- `backend/scripts/validate_android_agent_bootstrap.py` memvalidasi install pertama, no-op install kedua, health, handshake, dan teardown pada perangkat fisik.

## Matriks dukungan

| Android | Izin media | Status |
| --- | --- | --- |
| API 26–32 | `READ_EXTERNAL_STORAGE` | Kontrak dan grant path teruji unit; minimum API agent adalah 26. |
| API 33+ | `READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, notification opsional | Teruji unit dan fisik pada API 33. |
| Special access | Settings OS + polling bounded | Approval, denial, unavailable, timeout, dan revocation/retry teruji dengan transport deterministik. |

Perangkat fisik yang diverifikasi adalah Infinix X6837, Android 13/API 33. Special access tidak diminta pada uji fisik Phase 03 karena service Accessibility/Notification crawler belum diaktifkan sebelum phase yang memilikinya; capability yang belum tersedia tidak dilaporkan sebagai siap.

## Follow-up UX dan kompatibilitas ADB

Follow-up 17 Juli 2026 menambahkan pencarian executable ADB dari `PATH`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, Android Studio SDK macOS, dan lokasi Homebrew yang didukung. Semua install, runtime grant, special-access probe, settings launch, start, instrumentation, forward, pull, dan teardown tetap memakai argv tanpa shell serta serial tervalidasi. APK agent memakai install/update `-r -g`; APK instrumentation yang bertanda test-only dipasang otomatis dengan `-r -t`. Component special access dibandingkan secara exact dan probe diarahkan ke Android user aktif.

Hasil `am start -W` kini diklasifikasikan dari exit code, status, serta marker kegagalan eksplisit. Peringatan OEM bahwa activity tidak dibuat ulang karena intent telah dikirim ke instance Settings yang sudah berada di depan diperlakukan sebagai sukses; unresolved intent, activity yang tidak ada, security exception, permission denial, status non-OK, dan exit nonzero tetap gagal. Ini menutup false negative yang terlihat pada Infinix X6837 tanpa membuat semua output ADB dianggap berhasil. Grant Notification Listener juga diberi tiga probe verifikasi singkat karena beberapa OEM memperbarui secure service state secara asynchronous.

UX keamanan pada HP dibatasi pada dua kategori: Accessibility wajib untuk social crawl, serta Storage/All files pada full mode. Runtime media/SMS/contact/notification permissions tetap dicoba lewat install `-g` dan `pm grant`. Notification Listener otomatis atau menjadi capability parsial; tidak ada fallback ke layar izin tambahan. USB debugging authorization, API minimum 26, ABI, install-via-USB policy OEM, dan device policy tetap prasyarat yang tidak dapat dibypass.

Perbaikan kompatibilitas ini belum menjalankan build, test, instrumentation, atau crawl perangkat. Script fake-transport berada di `tests/android_adb_automation_contract.py`; status acceptance perubahan ini adalah `NOT RUN`.

## Error stabil

Bootstrap membedakan dependency/ADB, unauthorized, offline, no-device, timeout, device locked, storage rendah, API unsupported, build conflict/failure/timeout, install failure, signature mismatch, version mismatch, runtime permission denied/unsupported, special access denied/timeout/awaiting user, agent unreachable, invalid response, API mismatch, auth mismatch, session mismatch, cancellation, dan internal error.

Setiap error membawa kategori stabil, status HTTP, retryability, request ID, dan exit code dependency bila relevan. Output command, token, serial mentah, path private perangkat, dan payload personal tidak dimasukkan ke envelope publik.

## Verifikasi

- Targeted backend ADB/artifact/APK metadata/client/runtime/provider/bootstrap/API: 79 test lulus.
- Seluruh suite backend SIKSIK: 171 test lulus dan 4 test dependency opsional dilewati.
- Android unit test debug: 24 lulus.
- Android unit test release: 24 lulus.
- Android lint dan build APK aplikasi/test/automation: lulus.
- Instrumentation Infinix X6837 API 33: 4 test aplikasi dan 1 test automation lulus.
- Uji fisik backend: clean install menghasilkan `install`, bootstrap kedua menghasilkan `current`, keduanya mencapai authenticated `ready`.
- Teardown fisik: tidak ada forward atau proses agent tersisa; package agent tetap terpasang.

## Kompatibilitas

- Tidak ada source frontend yang diubah pada Phase 03. Style, layout, format, route, dan komponen UI SIKSIK tetap utuh.
- Simulator, iOS, upload ZIP yang sudah ada, serta provider non-Android tidak menjalankan bootstrap.
- Handoff Android agent tetap menggunakan file individual dan manifest; tidak ada arsip ZIP baru.
- Analyzer dan recommendation authority resmi SIKSIK tidak diganti.
- Setelah agent mencapai `ready`, Phase 03 masih memakai adapter Android ADB legacy yang sudah ada untuk akuisisi. Penggantian crawling ke API agent dilakukan di Phase 04, sehingga scope tidak diklaim selesai lebih awal dan fungsi Android lama tetap berjalan selama transisi.
