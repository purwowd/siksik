# SATRIA — Laporan Progress Aplikasi

**Dokumen:** SATRIA-PR-2026-08  
**Produk:** SATRIA — Sistem Analisis Terpadu Resiko & Integritas Aparatur  
**Versi status:** PoC Lab / Pilot Panitia  
**Tanggal laporan:** 24 Agustus 2026  
**Status uji fungsional:** **Lulus (lab)** — alur end-to-end berjalan  
**Fokus perbaikan berikutnya:** **Optimasi reasoning / akurasi analisa**

---

## 1. Ringkasan eksekutif

SATRIA telah mencapai tahap **aplikasi lab yang dapat diuji dan dioperasikan end-to-end**:

1. Operator mengisi identitas peserta → akuisisi (simulasi / ZIP / live sesuai host)
2. Pipeline analisa menghasilkan temuan
3. Analis mereview (konfirmasi / tolak, termasuk bulk)
4. Pimpinan melihat laporan → mengekspor → mengesahkan keputusan
5. Dasbor & timeline risiko menampilkan ringkasan kasus

**Kesimpulan uji:** aplikasi **sudah bisa dipakai** untuk demo, latihan panitia, dan validasi alur seleksi.  
**Catatan kritis:** kualitas **reasoning analisa** (ketepatan flag, konteks, pengurangan false positive/negative) masih perlu **dioptimasi** agar rekomendasi lebih akurat untuk keputusan ASN.

| Dimensi | Skor | Keterangan |
|---------|------|------------|
| Kesiapan lab / demo panitia | **~88%** | Flow UI + RBAC + laporan + export siap |
| Backend & logika bisnis | **~82%** | Tes API solid; edge case minor tersisa |
| Frontend konsol | **~80%** | 5 tab lengkap; cakupan E2E tipis |
| Desktop (Tauri) | **~75%** | Export native OK; butuh Chrome untuk PDF |
| Akurasi reasoning analisa | **~65%** | **Prioritas optimasi berikutnya** |
| Acceptance perangkat fisik | **~45%** | Kode agent ada; gate fisik belum dijalankan |
| Siap produksi seleksi ASN | **~62%** | Butuh reasoning + device acceptance + hardening |

---

## 2. Tujuan produk (yang sudah diwujudkan)

| Tujuan | Status |
|--------|--------|
| Identitas peserta wajib sebelum akuisisi | ✅ Selesai |
| Satu workstation, satu sesi aktif | ✅ Selesai |
| Analisa bertingkat (L1–L4) + temuan untuk review manusia | ✅ Selesai (akurasi masih ditingkatkan) |
| RBAC: operator / analis / pimpinan / admin | ✅ Selesai |
| Laporan resmi + ekspor HTML / JSON / PDF | ✅ Selesai |
| Dasbor tren & timeline risiko | ✅ Selesai |
| Web browser + shell desktop | ✅ Selesai |
| Dokumentasi setup & running | ✅ Selesai (`docs/SETUP.md`) |

---

## 3. Progress per modul

### 3.1 Alur panitia (business flow)

| Tahap | Status | Bukti uji |
|-------|--------|-----------|
| Login & RBAC | ✅ Lulus | Role land di tab yang benar; operator tanpa Temuan |
| Identitas peserta | ✅ Lulus | Nama + no. peserta wajib; NIK 16 digit opsional |
| Akuisisi sim / ZIP | ✅ Lulus | Pipeline selesai → rekomendasi muncul |
| Akuisisi live Android/iOS | ⚠️ Parsial | Toolchain host; agent path **belum acceptance fisik** |
| Review temuan | ✅ Lulus | Single + bulk; keyboard; audit `reviewed_by/at` |
| Rekomendasi LULUS / MENUNGGU / TIDAK LULUS | ✅ Lulus | Gate pengesahan sampai pending habis |
| Pengesahan pimpinan | ✅ Lulus | Laporan disk di-refresh setelah sahkan |
| Ekspor laporan | ✅ Lulus | Nama file: `SATRIA_{no}_{nama}_{datetime}` |

### 3.2 Platform teknis

| Komponen | Status | Catatan |
|----------|--------|---------|
| Backend FastAPI + SQLite | ✅ Stabil lab | ~325 tes lulus; 1 fail pre-existing (recovery layout) |
| Frontend React (5 tab) | ✅ Stabil lab | Operator, Temuan, Galeri, Laporan, Dasbor |
| Desktop Tauri | ✅ Stabil lab | Share UI/backend dengan web |
| Seed / setup panitia | ✅ | `setup_lab_panitia.py`, rotate password, dummy ASN |
| Docker smoke | ✅ | UI demo tanpa ADB |

### 3.3 Pipeline analisa (reasoning)

| Lapisan | Fungsi | Status akurasi |
|---------|--------|----------------|
| **L1** | Lexicon / keyword boundary | Berjalan; rawan FP jika konteks lemah |
| **L2** | Boost / korelasi teks | Berjalan; perlu kalibrasi skor |
| **L3** | OCR / visual / media text | Berjalan; kualitas OCR mempengaruhi hasil |
| **L4** | Model / stack GPU opsional | Opsional; belum jadi andalan lab CPU |

**Temuan uji lapangan (ringkas):** aplikasi menghasilkan temuan dan alur review berjalan, tetapi **beberapa indikasi kurang tepat** (terlalu agresif atau kurang konteks). Ini yang menjadi agenda optimasi berikutnya — bukan bug alur UI.

---

## 4. Yang sudah diperbaiki di iterasi terakhir

| Area | Perbaikan |
|------|-----------|
| Identitas & retry | No. peserta gagal (`failed`) tidak memblokir ulang di hari yang sama |
| Otorisasi | Laporan JSON/HTML di disk di-update setelah sahkan |
| Review | Bulk review API + jejak `reviewed_by` / `reviewed_at` |
| Timeline risiko | Hanya temuan **dikonfirmasi**; label fokus = nama · no. peserta |
| Session picker | Pagination lebih luas; sesi aktif tidak “hilang” saat filter |
| Desktop export | Dialog async (hindari freeze); nama file arsip panitia |
| Dokumentasi | `docs/SETUP.md` — web, desktop, generate user admin |

---

## 5. Hasil pengujian (lab)

| Jenis uji | Hasil |
|-----------|-------|
| Backend unit/API | **325 passed**, 7 skipped, 1 failed (layout recovery — non-blokir alur utama) |
| Frontend smoke (routes/RBAC) | **Lulus** |
| Alur manual: login → akuisisi → review → sahkan → ekspor | **Lulus** |
| Desktop PDF/HTML/JSON | **Lulus** (butuh Chrome untuk PDF) |
| Acceptance Android agent fisik | **Belum dijalankan** |

**Verdict uji:** **Go untuk lab & demo.**  
**Belum Go** untuk keputusan seleksi ASN skala penuh tanpa peningkatan akurasi reasoning + validasi perangkat.

---

## 6. Gap terbuka (prioritas)

### P0 — Akurasi reasoning (fokus utama berikutnya)

| Item | Masalah | Arah perbaikan |
|------|---------|----------------|
| False positive lexicon | Kata ambigu / konteks netral ikut terflag | Word-boundary + daftar exclude; skor berbasis konteks |
| Konteks sosial / screenshot | OCR noise → keyword salah | Pre-filter OCR; minimal confidence; dedupe screenshot |
| Bobot multi-lapisan | L1–L4 belum terkalibrasi seragam | Threshold per kategori + fusion score |
| Penjelasan temuan | Evidence terlalu generik | Evidence template: kutipan + lokasi + alasan skor |
| Evaluasi akurasi | Belum ada gold-set ASN | Dataset berlabel + precision/recall per kategori |

### P1 — Operasional & kualitas

- Fix tes recovery report layout  
- Align env seed `SATRIA_*` vs `SADT_*`  
- E2E otomatis: operator → bulk review → sahkan → export  
- Deteksi Chrome hilang di desktop (pesan jelas)  
- Dashboard loading skeleton  

### P2 — Produksi & perangkat

- Acceptance fisik Phase 08–10 (Android agent)  
- Hardening TLS / backup / rotate rutin  
- Modul WhatsApp / TikTok (masih stub jujur di UI)  

---

## 7. Rencana optimasi reasoning (usulan sprint)

**Tujuan:** menaikkan akurasi temuan & kepercayaan analis tanpa mengubah alur panitia.

| Sprint | Fokus | Deliverable |
|--------|-------|-------------|
| **R1** | Kalibrasi lexicon & threshold | Precision naik pada kategori inti; kurangi FP “noise” |
| **R2** | Evidence & scoring transparan | Setiap temuan punya alasan skor yang dibaca analis |
| **R3** | OCR / media text hygiene | Filter kualitas teks OCR sebelum match |
| **R4** | Gold-set & metrik | Skrip evaluasi precision/recall; baseline tercatat |
| **R5** | (Opsional) model L4 | Aktifkan stack GPU hanya jika R1–R4 sudah baseline |

Kriteria sukses R1–R4 (usulan):

- Precision kategori prioritas ≥ target yang disepakati panitia (mis. ≥ 0,80 pada gold-set lab)
- Analis melaporkan penurunan “temuan sampah” yang harus ditolak manual
- Rekomendasi sesi tidak berubah akibat noise yang seharusnya ditolak otomatis

---

## 8. Matriks kesiapan

```
Identitas & RBAC          ████████████████████░  90%
Alur review → sahkan      ██████████████████░░░  88%
Laporan & ekspor          █████████████████░░░░  86%
UI / UX konsol            ████████████████░░░░░  80%
Dokumentasi setup         █████████████████░░░░  85%
Backend tests             ███████████████░░░░░░  78%
Frontend / E2E tests      ███████████░░░░░░░░░░  58%
Reasoning / akurasi       █████████████░░░░░░░░  65%  ← fokus berikutnya
Perangkat fisik           █████████░░░░░░░░░░░░  45%
Hardening produksi        ████████████░░░░░░░░░  62%
```

---

## 9. Rekomendasi keputusan

| Pertanyaan | Jawaban |
|------------|--------|
| Boleh demo ke panitia / stakeholder? | **Ya** — pakai sim/ZIP + akun lab |
| Boleh dipakai latihan operasional lab? | **Ya** — dengan supervision analis |
| Boleh jadi satu-satunya dasar keputusan ASN? | **Belum** — tunggu optimasi reasoning + (idealnya) acceptance perangkat |
| Apa prioritas engineering berikutnya? | **Optimasi reasoning / akurasi analisa (bagian 7)** |

---

## 10. Referensi dokumen

| Dokumen | Isi |
|---------|-----|
| [`SETUP.md`](SETUP.md) | Instalasi web/desktop, user admin |
| [`RUNNING.md`](RUNNING.md) | Docker vs host |
| [`DESKTOP.md`](DESKTOP.md) | Shell Tauri & export |
| [`FLOW_PROGRESS.md`](FLOW_PROGRESS.md) | Tracker phase akuisisi / agent |
| [`PAGES.md`](PAGES.md) | Cek UI per tab |
| [`HARDENING.md`](HARDENING.md) | Menuju produksi |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Arsitektur sistem |

---

## 11. Riwayat status

| Tanggal | Milestone |
|---------|-----------|
| Jul 2026 | Phase akuisisi/agent diimplementasi; banyak gate fisik NOT RUN |
| 22 Agu 2026 | Rebrand UI SATRIA |
| 23–24 Agu 2026 | Identitas peserta, export desktop, audit fix P0/P1, SETUP.md |
| **24 Agu 2026** | **Laporan ini:** uji lab **lulus**; next = **optimasi reasoning untuk akurasi** |

---

*Dokumen ini merefleksikan status repositori & hasil uji lab. Status acceptance perangkat fisik tetap merujuk [`FLOW_PROGRESS.md`](FLOW_PROGRESS.md).*
