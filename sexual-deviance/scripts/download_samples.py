#!/usr/bin/env python3
"""Download sample uji kurasi dari Wikimedia Commons + ground truth labels."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
API = "https://commons.wikimedia.org/w/api.php"

SAMPLES = [
    {"file": "01_safe_landscape.jpg", "query": "Everest mountain landscape",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    {"file": "02_safe_office.jpg", "query": "Amiga computer museum office",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    {"file": "03_safe_food.jpg", "query": "healthy food display vegetables",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    {"file": "04_safe_cat.jpg", "query": "cat portrait",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    {"file": "05_safe_city.jpg", "query": "city skyline architecture",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    {"file": "06_suggestive_bikini.jpg", "query": "woman bikini kneeling beach Nicaragua",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/e/ef/A_woman_in_a_bikini_kneeling_at_a_beach_in_Nicaragua.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "none", "flagged": True}},
    {"file": "07_suggestive_bikini2.jpg", "query": "bikini beach woman swimming",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/84/A_woman_in_blue_bikini_at_Palauea_Beach_in_South_Maui%2C_Hawaii.jpg/960px-A_woman_in_blue_bikini_at_Palauea_Beach_in_South_Maui%2C_Hawaii.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "none", "flagged": True}},
    {"file": "08_suggestive_kiss_hetero.jpg", "query": "clothed woman kissing nude man",
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "heterosexual", "flagged": True}},
    {"file": "09_suggestive_kiss_street.jpg", "query": "couple kissing street Paris",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/4/4c/French_Kiss_%288300079970%29.jpg",
     "expected": {"severity": "suggestive", "nudity": "none", "orientation": "heterosexual", "flagged": True}},
    {"file": "10_suggestive_gay_kiss.jpg", "query": "two men kissing pride",
     "expected": {"severity": "suggestive", "nudity": "none", "orientation": "gay", "flagged": True}},
    {"file": "11_suggestive_gay_pride.jpg", "query": "gay pride parade men kissing",
     "expected": {"severity": "suggestive", "nudity": "none", "orientation": "gay", "flagged": True}},
    {"file": "12_suggestive_lesbian_kiss.jpg", "query": "lesbian couple kissing",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8d/Lesbian_Wedding_1.jpg/960px-Lesbian_Wedding_1.jpg",
     "expected": {"severity": "suggestive", "nudity": "none", "orientation": "lesbian", "flagged": True}},
    {"file": "13_suggestive_lesbian_pride.jpg", "query": "two women kissing pride parade",
     "expected": {"severity": "suggestive", "nudity": "none", "orientation": "lesbian", "flagged": True}},
    {"file": "14_nudity_art_venus.jpg", "query": "Birth of Venus Botticelli painting",
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "none", "flagged": True}},
    {"file": "15_nudity_art_david.jpg", "query": "Michelangelo David sculpture",
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "none", "flagged": True}},
    {"file": "16_suggestive_swimwear.jpg", "query": "woman blue bikini beach",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cc/A_woman_in_bikini_at_a_beach_in_Kapalua%2C_Hawaii.jpg/960px-A_woman_in_bikini_at_a_beach_in_Kapalua%2C_Hawaii.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "suggestive", "nudity": "none", "orientation": "none", "flagged": True}},
    {"file": "17_safe_family.jpg", "query": "family portrait clothed outdoor",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/f/f5/COLLECTIE_TROPENMUSEUM_Portret_van_de_familie_Kikkert_in_de_tuin_TMnr_60006234.jpg",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    {"file": "18_suggestive_hug.jpg", "query": "couple embracing hugging romantic",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/5/5c/4-couple-embrace-tenderly-moments-after-making-love-together-forever.jpg",
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "heterosexual", "flagged": True}},
    {"file": "19_safe_sports.jpg", "query": "soccer football match stadium",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5a/Championship-Soccer-Stadium-SE-Field-View-2024-08-03.jpg/960px-Championship-Soccer-Stadium-SE-Field-View-2024-08-03.jpg",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    {"file": "20_suggestive_shirtless.jpg", "query": "shirtless man beach volleyball",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/d/d5/Mexico_Red_Glasses_Sun_Beach_Tattoo_Shorts_Towel_Sand_Ryan.jpg",
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "none", "flagged": True}},
    {"file": "21_safe_wedding.jpg", "query": "wedding bride groom church clothed",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/87/A_bride_and_groom_from_Kisa%C4%8Da.jpg/960px-A_bride_and_groom_from_Kisa%C4%8Da.jpg",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False}},
    # --- LGBT & edge-case suite (22–30) ---
    {"file": "22_safe_rainbow_sky.jpg", "query": "rainbow sky landscape after rain",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Rainbow_in_the_sky.jpg/960px-Rainbow_in_the_sky.jpg",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "lgbt_present": False}},
    {"file": "23_lgbt_flag_only.jpg", "query": "rainbow LGBT pride flag waving",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/48/Gay_Pride_Flag.svg/800px-Gay_Pride_Flag.svg.png",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "lgbt_present": True, "lgbt_flag_any": ["rainbow"]}},
    {"file": "24_trans_flag.jpg", "query": "transgender pride flag",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b0/Transgender_Pride_flag.svg/800px-Transgender_Pride_flag.svg.png",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "lgbt_present": True, "lgbt_flag_any": ["trans", "rainbow"]}},
    {"file": "25_bisexual_flag.jpg", "query": "bisexual pride flag",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/The_bisexual_pride_flag_%283673713584%29.jpg/960px-The_bisexual_pride_flag_%283673713584%29.jpg",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "lgbt_present": True, "lgbt_flag_any": ["bisexual", "rainbow"]}},
    {"file": "26_pride_rainbow_shirt.jpg", "query": "person wearing rainbow pride t-shirt",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1f/Pride_parade_attendee_with_a_colorful_Pried_t-shirt_and_rainbow_flag_%2851263371790%29.jpg/960px-Pride_parade_attendee_with_a_colorful_Pried_t-shirt_and_rainbow_flag_%2851263371790%29.jpg",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "lgbt_present": True, "lgbt_flag_any": ["rainbow", "pride_merch", "rainbow_clothing"]}},
    {"file": "27_hetero_at_pride.jpg", "query": "man woman couple kissing pride parade",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/4/4c/French_Kiss_%288300079970%29.jpg",
     "expected": {"severity": "suggestive", "nudity": "none", "orientation": "heterosexual", "flagged": True,
                 "lgbt_present": False}},
    {"file": "28_suggestive_nude_beach.jpg", "query": "nude beach people sunbathing",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/3/31/02019_0007_%282%29_naturist_river_beach.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "suggestive", "nudity": "partial", "orientation": "none", "flagged": True,
                 "lgbt_present": True, "lgbt_flag_any": ["rainbow"]}},
    {"file": "29_safe_medical.jpg", "query": "doctor patient examination clothed hospital",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/0/07/Patient%2C_doctor%2C_and_nurses%2C_Satbarwa_Hospital%2C_Bihar%2C_India%2C_1967_%2816825236020%29.jpg",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "lgbt_present": False}},
    {"file": "30_sample_video.webm", "query": "short nature waterfall video",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/3/31/A_Short_glimps_of_zanskar_sheela_waterfall.webm",
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "lgbt_present": False, "media_type": "video"}},
]


def search_thumb(query: str) -> Optional[str]:
    params = {
        "action": "query", "format": "json",
        "generator": "search", "gsrsearch": query,
        "gsrnamespace": "6", "gsrlimit": "8",
        "prop": "imageinfo",
        "iiprop": "url|mime|size|thumburl",
        "iiurlwidth": "800",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.load(r)
    for page in data.get("query", {}).get("pages", {}).values():
        for info in page.get("imageinfo", []):
            if info.get("mime", "").startswith("image/") and info.get("size", 0) < 6_000_000:
                return info.get("thumburl") or info.get("url")
    return None


def synthesize_trans_flag(dest: Path) -> None:
    """Generate trans pride flag JPEG when Commons thumbnail is too small."""
    from PIL import Image

    w, h = 960, 640
    img = Image.new("RGB", (w, h))
    stripes = [
        (91, 206, 250),
        (245, 169, 184),
        (255, 255, 255),
        (245, 169, 184),
        (91, 206, 250),
    ]
    sh = h // len(stripes)
    for i, color in enumerate(stripes):
        y0 = i * sh
        y1 = h if i == len(stripes) - 1 else (i + 1) * sh
        for y in range(y0, y1):
            for x in range(w):
                img.putpixel((x, y), color)
    img.save(dest, "JPEG", quality=92)


def download(url: str, dest: Path, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                dest.write_bytes(r.read())
            return True
        except Exception as e:
            if attempt == retries - 1:
                print(f"  FAIL: {e}")
            time.sleep(3 * (attempt + 1))
    return False


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out = root / "samples" / "internet"
    out.mkdir(parents=True, exist_ok=True)

    manifest = []
    expected = {}
    ok = 0

    for i, sample in enumerate(SAMPLES):
        fname = sample["file"]
        dest = out / fname
        if dest.exists() and dest.stat().st_size > 10_000:
            print(f"=> {fname} (cached)")
            ok += 1
            manifest.append({"file": fname, "cached": True})
            expected[fname] = sample["expected"]
            continue

        print(f"=> {fname}")
        time.sleep(3.5 if i > 0 else 0)
        url = sample.get("fallback_url") if sample.get("prefer_fallback") else None
        if not url:
            for attempt in range(3):
                try:
                    url = search_thumb(sample["query"])
                    if url:
                        break
                except Exception as e:
                    print(f"  search retry {attempt + 1}: {e}")
                    time.sleep(5 * (attempt + 1))
        if not url:
            url = sample.get("fallback_url")
        if not url:
            print("  MISS search")
            expected[fname] = sample["expected"]
            continue
        time.sleep(2.0)
        if download(url, dest):
            if dest.stat().st_size < 10_000 and fname == "24_trans_flag.jpg":
                print("  small SVG thumb — synthesizing trans flag")
                synthesize_trans_flag(dest)
            ok += 1
            manifest.append({"file": fname, "url": url, "query": sample["query"]})
            expected[fname] = sample["expected"]
            print(f"  OK ({dest.stat().st_size // 1024} KB)")
        else:
            expected[fname] = sample["expected"]

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    (out / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False))
    print(f"\n{ok}/{len(SAMPLES)} ready -> {out}")


if __name__ == "__main__":
    main()
