# Sample Uji (Internet)

Ground truth: `expected.json`  
Validasi konten: `python scripts/validate_samples.py`  
Validasi meme: `python scripts/validate_meme_benchmark.py` → **target 52/52**

## Download

```bash
python scripts/download_samples.py
```

Script pakai Wikimedia API + **synthesize meme viral** (portrait Commons + teks overlay). File yang sudah ada di-skip (cache).

## Kategori (~77 sample)

| # | File | Kategori |
|---|------|----------|
| 01–05 | safe_* | Aman: landscape, office, food, cat, city |
| 06–07 | suggestive_bikini* | Suggestive: bikini |
| 08–09 | suggestive_kiss_* | Suggestive: ciuman hetero |
| 10–13 | suggestive_gay/lesbian_* | Suggestive: ciuman gay/lesbian + pride |
| 14–15 | nudity_art_* | Partial nudity: seni klasik |
| 16–20 | suggestive_* | Swimwear, hug, shirtless, nude beach |
| 17, 21–22 | safe_* | Family, wedding, rainbow sky |
| 23–27 | lgbt_* / pride | Bendera LGBT, pride shirt, hetero at pride |
| 29 | safe_medical | Aman: medis |
| 31–32, 37, 39 | *_official | Portrait resmi Jokowi, Prabowo, Gibran, Anies (safe) |
| 33–42 | meme_* | Meme politik early set (caption + cartoon + PPN) |
| 43–77 | meme_* | Meme viral sintetis: gemoy, ndasmu, omon-omon, hidup sawit, MBG, termul, megawati, mahfud, luhut, sri mulyani, erick, puan, emil, … |

User meme asli (PNG): `samples/user_memes/` (9 file).

## Uji

```bash
source .venv/bin/activate
./scripts/start_sidecar.sh   # terminal 1 — untuk meme benchmark

python scripts/validate_samples.py           # konten, mode FULL
python scripts/validate_meme_benchmark.py  # meme Indonesia, mode BALANCED

python main.py samples/internet/*meme*.jpg --mode balanced --json
```

Laporan:
- `validation_report.json` — konten umum
- `../meme_benchmark_report.json` — meme Indonesia
