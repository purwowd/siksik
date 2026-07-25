# Baseline SIKSIK Phase 00

Tanggal verifikasi: 16 Juli 2026.

Dokumen ini membekukan kontrak yang harus tetap kompatibel selama implementasi Flow. Penambahan berikutnya wajib bersifat additive kecuali phase terkait secara eksplisit mengganti perilaku lama.

## Gate verifikasi

- Python: 3.11.15 dalam `backend/.venv`.
- Backend: 92 test lulus, 4 test opsional dilewati oleh kondisi environment yang sudah ada.
- Frontend: TypeScript project build dan Vite production build lulus.
- Database baru: inisialisasi schema dan empat akun seed lulus.
- Tidak ada perubahan frontend pada Phase 00.

## Endpoint yang dibekukan

| Method | Path |
| --- | --- |
| GET | `/api/v1/health` |
| POST | `/api/v1/auth/login` |
| POST | `/api/v1/auth/logout` |
| GET | `/api/v1/auth/me` |
| GET | `/api/v1/auth/users` |
| GET | `/api/v1/auth/roles` |
| GET | `/api/v1/devices` |
| GET | `/api/v1/toolchain` |
| POST | `/api/v1/sessions` |
| POST | `/api/v1/sessions/from-zip` |
| GET | `/api/v1/sessions` |
| GET | `/api/v1/sessions/{session_id}` |
| POST | `/api/v1/sessions/{session_id}/cancel` |
| GET | `/api/v1/sessions/{session_id}/findings` |
| GET | `/api/v1/sessions/{session_id}/media` |
| GET | `/api/v1/sessions/{session_id}/report` |
| POST | `/api/v1/sessions/{session_id}/authorize` |
| GET | `/api/v1/findings` |
| PATCH | `/api/v1/findings/{finding_id}` |
| GET | `/api/v1/sessions/{session_id}/risk-timeline` |
| POST | `/api/v1/admin/clear-hash-cache` |
| POST | `/api/v1/admin/recompute-recommendations` |
| GET | `/api/v1/dashboard` |

## Kontrak data utama

- Jenis perangkat: `android`, `ios`, `simulated`.
- Mode akuisisi: `quick`, `full`.
- Skenario simulator: `lulus`, `tidak_lulus`.
- Status sesi: `pending`, `detecting`, `acquiring`, `indexing`, `analyzing`, `completed`, `failed`, `cancelled`.
- Status review: `pending`, `confirmed`, `rejected`.
- Layer finding: `L1`, `L2`, `L3`, `L4`.
- Rekomendasi: `LULUS`, `MENUNGGU REVIEW`, atau `TIDAK LULUS` sesuai hasil dan keputusan review.
- Respons sesi mempertahankan `id`, identitas perangkat, label, mode, skenario, status, progress, timing, recommendation, timestamp, dan error.
- Respons finding mempertahankan identitas sesi/file, source, path, kategori, label, confidence, layer, evidence, status review, timestamp, dan metadata waktu media.
- Pagination mempertahankan `items`, `page`, `page_size`, `total`, dan `pages`.

## Database baru

| Table | Fungsi baseline |
| --- | --- |
| `sessions` | Lifecycle, progress, timing, rekomendasi, dan error sesi. |
| `files` | Inventaris file hasil akuisisi dan status analisis. |
| `findings` | Temuan, evidence, layer, review, dan waktu media. |
| `hash_cache` | Cache hasil analisis berbasis hash dan fingerprint engine. |
| `users` | Akun dan role. |
| `auth_tokens` | Token sesi autentikasi. |

Migrasi harus additive, idempotent, dan dapat membuka database baseline ini tanpa kehilangan data.

## Perilaku frontend yang dibekukan

- Route tetap: `/operator`, `/dasbor`, `/temuan`, dan `/laporan`.
- Hak akses route tetap mengikuti RBAC saat ini.
- Struktur, layout, style, format, komponen, dan class CSS yang sudah ada tidak boleh diganti.
- Upload ZIP yang sudah ada tetap tersedia dan tetap memakai kontrak lama.
- Fitur Flow baru hanya boleh ditambahkan menggunakan bahasa visual dan pola komponen yang sudah ada.

## Invarian regresi

- Hanya satu sesi aktif pada satu waktu.
- Simulator hanya dapat dipakai ketika lab mode aktif.
- Finding baru berstatus `pending`.
- Finding pending menghasilkan `MENUNGGU REVIEW`; finding confirmed dapat menghasilkan `TIDAK LULUS`; tanpa finding aktif menghasilkan `LULUS`.
- Endpoint media tetap menolak traversal path dan tipe preview yang tidak didukung.
- Detail path health tetap disamarkan untuk pengguna non-admin.
- Akuisisi ZIP lama tidak diubah menjadi kontrak handoff Android-agent. Handoff agent akan menjadi jalur terpisah tanpa arsip ZIP.
