# SATRIA Rebrand — Catatan Kompatibilitas

Produk user-facing: **SATRIA** (Sistem Analisis Terpadu Resiko & Integritas Aparatur).  
Nama internal legacy (SIKSIK / SADT) tetap didukung agar lab dan agent yang sudah terpasang tidak putus.

## Identitas visual (tone TNI / BAIS)

Palet UI mengacu merah–emas–hitam–putih institusional (bukan logo resmi). Token CSS di `frontend/src/styles.css`.


1. `SATRIA_*` (preferensi)
2. `SADT_*` (fallback)

Jika keduanya di-set untuk kunci yang sama, `SATRIA_*` menang (`app.core.branding.promote_satria_env`).

## MIME crawl-record

| MIME | Peran |
|------|--------|
| `application/vnd.satria.crawl-record+json` | Kanonikal baru (diterima) |
| `application/vnd.siksik.crawl-record+json` | Legacy agent — **tetap diterima** |

Suffix file: `.satria-record.json` dan `.siksik-record.json` keduanya valid.  
Jalur transfer Android agent masih menulis suffix legacy agar kompatibel dengan APK terpasang.

## Field session id (JSON agent)

- Input: `satria_session_id` **atau** `siksik_session_id`
- Output wire: tetap `siksik_session_id` (non-breaking)

## Android package

`applicationId` / package **tetap** `com.siksik.agent`.  
Hanya string tampilan (notifikasi / label) yang memakai merek SATRIA.

## API

Path tetap `/api/v1/...`. Tidak diganti di fase ini.

## Dasbor SPD

Alur lima tahap di UI dipetakan ke status sesi existing. Modul WhatsApp database penuh dan platform TikTok/YouTube/Threads ditandai jujur sebagai belum aktif / direncanakan — bukan angka 0 palsu.
