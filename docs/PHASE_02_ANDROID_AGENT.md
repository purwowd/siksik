# Phase 02: Android Agent SIKSIK

Tanggal verifikasi: 16 Juli 2026.

## Hasil

- Android Agent sekarang merupakan subproject mandiri di `siksik/android-agent` dengan application ID `com.siksik.agent`.
- Build memakai minSdk 26, compile/target SDK 35, Java/Kotlin 17, API `1.0`, capability schema `1`, dan port perangkat `38471`.
- Bootstrap Activity yang exported memvalidasi session ID, token, dan expiry sebelum menjalankan foreground service yang non-exported.
- HTTP API hanya bind ke `127.0.0.1`, memakai bearer token per sesi, request ID, body/query/header strict, response bounded, dan worker queue terbatas.
- Route dipisah menjadi sesi/capability, grant, katalog media, thumbnail, staging, manifest, cleanup, dan stop.
- Capability response menggunakan state bertipe serta memuat agent version, build SHA-256, API version, capability schema, port, package, API Android, storage, dan sesi aktif.
- Capability yang belum dikerjakan pada phase berikutnya dilaporkan `unavailable`; agent tidak mengiklankan crawling, OCR, hashing preprocessing, face/object model, atau scoring sebagai siap.
- Photo Picker, directory grant, izin media, katalog berhalaman, thumbnail bounded, selective staging, manifest, dan cleanup mempertahankan invariant donor yang relevan.
- Staging menghasilkan file individual terverifikasi dan `manifest.json` berisi size serta SHA-256. Jalur handoff agent tidak membuat arsip ZIP.
- Automation test APK shell tersedia sebagai module terpisah dan hanya menyediakan environment probe; social crawling belum diimplementasikan pada Phase 02.

## Donor yang diserap

| Donor LPDP | Adaptasi SIKSIK |
| --- | --- |
| Build/manifest companion | Dibuat ulang dengan namespace, metadata build, lifecycle, resource, dan hardening SIKSIK. |
| Companion server | Dipecah menjadi request/response boundary, route sesi, grant, media, staging, serta bounded runner. |
| Session authenticator | Diadaptasi ke error `agent_*`, token hingga 512 karakter, validasi bootstrap TTL, dan zeroing saat destroy. |
| Grant coordinator/store | Dipisah menurut permission boundary dan memakai scope serta state SIKSIK. |
| Media catalog | Dipisah menjadi model, path policy, identifier store, dan catalog dengan opaque media ID. |
| Staging manager | Dipisah menjadi selection, model, path policy, state store, manifest builder, dan manager non-ZIP. |
| Unit/instrumentation tests | Ditulis ulang pada package SIKSIK dan diperluas untuk build metadata, worker bound, stage limit, manifest, serta cleanup. |

Tidak ada source-set, Gradle include, symlink, import, package, atau runtime path yang merujuk kembali ke LPDP.

## Kontrak backend

- Konfigurasi package/component backend sekarang menunjuk ke `com.siksik.agent/.session.BootstrapActivity`.
- Model client backend memvalidasi capability response bertipe secara strict, termasuk SHA-256 build dan port.
- Artifact service menemukan APK di `android-agent/app/build/outputs/apk/debug/app-debug.apk`, memvalidasi ukuran/hash, menulis stamp atomik, dan menggunakan ulang artifact yang input digest-nya sama.

## Verifikasi

- Android unit test debug: 24 lulus.
- Android unit test release: 24 lulus.
- Android lint: lulus tanpa error.
- APK aplikasi dan automation shell: berhasil dibangun.
- Instrumentation pada Infinix X6837, Android API 33: 3 test aplikasi dan 1 test automation lulus.
- Instrumentation memverifikasi authenticated loopback capability, missing auth, request ID, typed metadata, stop lifecycle, cleanup file individual, idempotent cleanup, serta automation environment probe.
- APK aplikasi: 2.5 MiB dan tervalidasi oleh backend artifact service; pemanggilan kedua memakai cache artifact tervalidasi.
- Targeted backend contract/artifact/ADB/runtime: 29 test lulus.
- Manifest APK memverifikasi application ID, min/target SDK, Bootstrap Activity exported, Agent Service non-exported, dan cleartext client disabled.
- Package debug/test sementara dibersihkan dari perangkat setelah instrumentation selesai.

## Kompatibilitas

- Tidak ada source frontend SIKSIK yang diubah pada Phase 02.
- Style, layout, format, route, dan komponen UI web SIKSIK tetap utuh.
- Upload ZIP SIKSIK yang sudah ada tetap tersedia dan tidak diubah; larangan ZIP hanya berlaku pada handoff baru Android Agent.
- Analyzer, recommendation authority, auth, RBAC, iOS, simulator, dan Android legacy belum diganti oleh phase ini.

## Ditunda sesuai urutan phase

- Orkestrasi build/install/start/forward/handshake otomatis backend diaktifkan pada Phase 03.
- Grant orchestration serta lifecycle akses dilanjutkan pada Phase 04.
- Crawling UI aplikasi sosial berada pada Phase 05 dan seterusnya.
- Preprocessing, scoring, rekomendasi, persistence akhir, dan perubahan UI additive tetap mengikuti phase masing-masing.
