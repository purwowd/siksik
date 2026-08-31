# Sample Uji (Internet) — 18 kategori

Ground truth: `expected.json`  
Validasi: `python scripts/validate_samples.py` → **target 100%**

## Download

```bash
python scripts/download_samples.py
```

Script pakai Wikimedia API (delay 3.5s antar file). File yang sudah ada di-skip (cache).

## Kategori (18 sample)

| # | File | Kategori |
|---|------|----------|
| 01–05 | safe_* | Aman: landscape, office, food, cat, city |
| 06–07 | suggestive_bikini* | Suggestive: bikini |
| 08–09 | suggestive_kiss_* | Suggestive: ciuman hetero |
| 10–11 | suggestive_gay_* | Suggestive: ciuman gay/pride |
| 12–13 | suggestive_lesbian_* | Suggestive: ciuman lesbian/pride |
| 14–15 | nudity_art_* | Partial nudity: seni klasik |
| 16 | suggestive_lingerie | Suggestive: lingerie |
| 17 | safe_family | Aman: keluarga |
| 18 | suggestive_hug | Suggestive: pelukan hetero |

## Uji

```bash
source .venv/bin/activate
python scripts/validate_samples.py   # benchmark otomatis
python main.py samples/internet/*.jpg
```

Laporan detail: `validation_report.json`
