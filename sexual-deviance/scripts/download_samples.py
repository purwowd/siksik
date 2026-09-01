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

# Portrait Commons untuk synthesize meme viral
_PORTRAIT = {
    "jokowi": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/Joko_Widodo_2019_official_portrait.jpg/960px-Joko_Widodo_2019_official_portrait.jpg",
    "prabowo": "https://upload.wikimedia.org/wikipedia/commons/b/bd/Prabowo_Subianto_official_portrait_%282019%29.jpg",
    "gibran": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/Gibran_Rakabuming_2024_official_portrait.jpg/960px-Gibran_Rakabuming_2024_official_portrait.jpg",
    "ganjar": "https://upload.wikimedia.org/wikipedia/commons/e/e3/Eudia_Isabelle_meets_Ganjar_Pranowo_%282%29_%28cropped%29.jpg",
    "anies": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/Anies_Baswedan_at_Fatmawati_MRT_Station.jpg/960px-Anies_Baswedan_at_Fatmawati_MRT_Station.jpg",
    "bahlil": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ac/Bahlil_Lahadalia_Official_Portrait.png/960px-Bahlil_Lahadalia_Official_Portrait.png",
    "megawati": "https://upload.wikimedia.org/wikipedia/commons/7/76/Megawati_Sukarnoputri_Oval.png",
    "mahfud": "https://thumb.wikimedia.org/wikipedia/commons/thumb/f/f9/Mahfud_MD%2C_Candidate_for_Indonesia%27s_Vice_President_in_2024.jpg/960px-Mahfud_MD%2C_Candidate_for_Indonesia%27s_Vice_President_in_2024.jpg",
    "luhut": "https://upload.wikimedia.org/wikipedia/commons/1/13/Luhut_B._Pandjaitan%2C_Ketua_Dewan_Ekonomi_Nasional_%28cropped%29.png",
    "sri_mulyani": "https://thumb.wikimedia.org/wikipedia/commons/thumb/c/cd/Indrawati%2C_Sri_Mulyani_%28IMF%29.jpg/960px-Indrawati%2C_Sri_Mulyani_%28IMF%29.jpg",
    "erick": "https://upload.wikimedia.org/wikipedia/commons/e/ed/Erick_Thohir%2C_Menteri_BUMN.png",
    "puan": "https://thumb.wikimedia.org/wikipedia/commons/thumb/c/cd/Puan_Maharani%2C_Ketua_DPR-RI_%282024%E2%80%932029%29.jpg/960px-Puan_Maharani%2C_Ketua_DPR-RI_%282024%E2%80%932029%29.jpg",
    "emil": "https://upload.wikimedia.org/wikipedia/commons/d/dc/Governor_of_West_Java_Ridwan_Kamil.png",
}


def _viral_meme(
    num: int,
    slug: str,
    figure: str,
    top: str,
    bottom: str,
    *,
    portrait: Optional[str] = None,
    satire: Optional[list[str]] = None,
) -> dict:
    sat = satire or ["political_satire"]
    pic = portrait or figure
    return {
        "file": f"{num:02d}_meme_{slug}.jpg",
        "synthesize_meme": {
            "base_url": _PORTRAIT[pic],
            "top_text": top,
            "bottom_text": bottom,
        },
        "expected": {
            "severity": "safe",
            "nudity": "none",
            "orientation": "none",
            "flagged": False,
            "meme_present": True,
            "meme_is_meme": True,
            "meme_figure_any": [figure],
            "meme_satire_any": sat,
        },
    }


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
    # --- Meme Indonesia (31–36) ---
    {"file": "31_safe_jokowi_official.jpg", "query": "Joko Widodo official portrait",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/Joko_Widodo_2019_official_portrait.jpg/960px-Joko_Widodo_2019_official_portrait.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": False}},
    {"file": "32_safe_prabowo_official.jpg", "query": "Prabowo Subianto official portrait",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/b/bd/Prabowo_Subianto_official_portrait_%282019%29.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": False}},
    {"file": "33_meme_jokowi_caption.jpg",
     "synthesize_meme": {
         "base_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/be/Joko_Widodo_2019_official_portrait.jpg/960px-Joko_Widodo_2019_official_portrait.jpg",
         "top_text": "KOK HARGA NAIK TERUS?",
         "bottom_text": "YA NAMANYA JUGA MAU JADI PRESIDEN LAGI",
     },
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True, "meme_figure_any": ["jokowi"],
                 "meme_satire_any": ["political_satire"]}},
    {"file": "34_meme_prabowo_caption.jpg",
     "synthesize_meme": {
         "base_url": "https://upload.wikimedia.org/wikipedia/commons/b/bd/Prabowo_Subianto_official_portrait_%282019%29.jpg",
         "top_text": "PAJAK NAIK LAGI",
         "bottom_text": "SANTUY BRO, NEGARA BUTUH UANG",
     },
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True, "meme_figure_any": ["prabowo"],
                 "meme_satire_any": ["political_satire", "sarcasm"]}},
    {"file": "35_meme_political_cartoon.jpg", "query": "political caricature cartoon satire",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/Caricature_gillray_plumpudding.jpg/960px-Caricature_gillray_plumpudding.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True,
                 "meme_satire_any": ["political_satire", "caricature"]}},
    {"file": "36_meme_ganjar_caption.jpg",
     "synthesize_meme": {
         "base_url": "https://upload.wikimedia.org/wikipedia/commons/e/e3/Eudia_Isabelle_meets_Ganjar_Pranowo_%282%29_%28cropped%29.jpg",
         "top_text": "WAKTU NYERAH JADI GUBERNUR",
         "bottom_text": "SURVEI NYATANYA MASIH OKE KOK",
     },
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True, "meme_figure_any": ["ganjar"],
                 "meme_satire_any": ["political_satire", "humor"]}},
    # --- Meme Indonesia batch 2 (37–42) — topik 2025–2026 ---
    {"file": "37_safe_gibran_official.jpg",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/Gibran_Rakabuming_2024_official_portrait.jpg/960px-Gibran_Rakabuming_2024_official_portrait.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": False}},
    {"file": "38_meme_gibran_caption.jpg",
     "synthesize_meme": {
         "base_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/Gibran_Rakabuming_2024_official_portrait.jpg/960px-Gibran_Rakabuming_2024_official_portrait.jpg",
         "top_text": "WAPRES TERMUDA SE-ASIA",
         "bottom_text": "SUHU BILANG GAPAPA KOK",
     },
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True, "meme_figure_any": ["gibran"],
                 "meme_satire_any": ["political_satire", "sarcasm"]}},
    {"file": "39_safe_anies_official.jpg",
     "fallback_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/Anies_Baswedan_at_Fatmawati_MRT_Station.jpg/960px-Anies_Baswedan_at_Fatmawati_MRT_Station.jpg",
     "prefer_fallback": True,
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": False}},
    {"file": "40_meme_anies_caption.jpg",
     "synthesize_meme": {
         "base_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8e/Anies_Baswedan_at_Fatmawati_MRT_Station.jpg/960px-Anies_Baswedan_at_Fatmawati_MRT_Station.jpg",
         "top_text": "JAKARTA MODEL DUNIA",
         "bottom_text": "NETIZEN: MEME APA SERIUS?",
     },
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True, "meme_figure_any": ["anies"],
                 "meme_satire_any": ["political_satire", "humor"]}},
    {"file": "41_meme_bahlil_caption.jpg",
     "synthesize_meme": {
         "base_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ac/Bahlil_Lahadalia_Official_Portrait.png/960px-Bahlil_Lahadalia_Official_Portrait.png",
         "top_text": "MEME DOANG KOK",
         "bottom_text": "KENAPA DIPIDANA?",
     },
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True, "meme_figure_any": ["bahlil"],
                 "meme_satire_any": ["political_satire", "criticism"]}},
    {"file": "42_meme_ppn_naik.jpg",
     "synthesize_meme": {
         "base_url": "https://upload.wikimedia.org/wikipedia/commons/b/bd/Prabowo_Subianto_official_portrait_%282019%29.jpg",
         "top_text": "PPN NAIK LAGI 2025",
         "bottom_text": "RAKYAT HARUS IKHLAS BRO",
     },
     "expected": {"severity": "safe", "nudity": "none", "orientation": "none", "flagged": False,
                 "meme_present": True, "meme_is_meme": True, "meme_figure_any": ["prabowo"],
                 "meme_satire_any": ["political_satire", "sarcasm"]}},
    # --- Meme viral Indonesia batch 3 (43–77) — slang Pilpres 2024 & viral 2025–2026 ---
    _viral_meme(43, "prabowo_gemoy", "prabowo", "PRESIDEN GEMOY", "JOGET GEMOY TABRAK TABRAK",
                satire=["political_satire", "populist_branding", "humor"]),
    _viral_meme(44, "prabowo_omon", "prabowo", "OMON-OMON TAK BISA", "KERJA NYATA DONG",
                satire=["political_satire", "criticism"]),
    _viral_meme(45, "prabowo_ok_gas", "prabowo", "OK GAS KAN", "GAS POL",
                satire=["political_satire", "populist_branding"]),
    _viral_meme(46, "prabowo_hidup_sawit", "prabowo", "HIDUP SAWIT", "KELAPA SAWIT NKRI",
                satire=["political_satire"]),
    _viral_meme(47, "prabowo_jangan_dikasitau", "prabowo", "JANGAN DIKASITAU", "TANGAN DI SHRUGS",
                satire=["political_satire", "humor"]),
    _viral_meme(48, "prabowo_ap_peduli", "prabowo", "AP PEDULI GWE?", "IKHLAS BRO",
                satire=["political_satire", "sarcasm"]),
    _viral_meme(49, "prabowo_mbg", "prabowo", "PROGRAM MBG", "MAKAN BERGIZI GRATIS",
                satire=["political_satire"]),
    _viral_meme(50, "prabowo_kabinet_gemuk", "prabowo", "KABINET GEMUK", "KABINET GEMOY",
                satire=["political_satire", "criticism"]),
    _viral_meme(51, "prabowo_ndasmu", "prabowo", "NDASMU BRO", "OMON-OMON",
                satire=["political_satire", "impoliteness"]),
    _viral_meme(52, "prabowo_pajak", "prabowo", "PAJAK NAIK LAGI", "PPH NAIK TERUS",
                satire=["political_satire", "criticism"]),
    _viral_meme(53, "prabowo_gas_pol", "prabowo", "GAS POL", "GASKEUN",
                satire=["political_satire", "populist_branding"]),
    _viral_meme(54, "jokowi_termul", "jokowi", "TERNAK MULYONO", "TERMUL BUZZER",
                satire=["political_satire", "criticism"]),
    _viral_meme(55, "jokowi_gubernur", "jokowi", "GUBERNUR DKI JAKARTA", "CAL JAKARTA",
                satire=["political_satire"]),
    _viral_meme(56, "jokowi_rakyat", "jokowi", "RAKYAT ANJING", "MEME DOANG",
                satire=["political_satire", "impoliteness"]),
    _viral_meme(57, "jokowi_presiden_lagi", "jokowi", "NAMANYA JUGA MAU", "JADI PRESIDEN LAGI",
                satire=["political_satire", "sarcasm"]),
    _viral_meme(58, "jokowi_uu", "jokowi", "SAYA TIDAK TANDA TANGAN UU", "UU ITU",
                satire=["political_satire"]),
    _viral_meme(59, "jokowi_harga", "jokowi", "KOK HARGA NAIK", "NAIK TERUS",
                satire=["political_satire", "criticism"]),
    _viral_meme(60, "gibran_wapres", "gibran", "WAPRES TERMUDA", "SEPANJANG SEJARAH",
                satire=["political_satire", "sarcasm"]),
    _viral_meme(61, "gibran_suhu", "gibran", "SUHU BILANG", "SUHU KATA GAPAPA",
                satire=["political_satire"]),
    _viral_meme(62, "gibran_nyerah", "gibran", "WAPRES TERMUDA", "SUHU BILANG GAPAPA",
                satire=["political_satire", "humor"]),
    _viral_meme(63, "anies_jakarta", "anies", "JAKARTA MODEL DUNIA", "NETIZEN: MEME APA SERIUS?",
                satire=["political_satire", "humor"]),
    _viral_meme(64, "anies_omon_target", "anies", "CUAP-CUAP TEORI", "MODEL DUNIA JAKARTA",
                satire=["political_satire", "criticism"]),
    _viral_meme(65, "ganjar_survei", "ganjar", "SURVEI MASIH OKE KOK", "NYANTANYA OKE",
                satire=["political_satire", "humor"]),
    _viral_meme(66, "ganjar_oke", "ganjar", "MASIH OKE KOK", "SURVEI NYATANYA",
                satire=["political_satire"]),
    _viral_meme(67, "bahlil_meme_doang", "bahlil", "MEME DOANG KOK", "KENAPA DIPIDANA?",
                satire=["political_satire", "criticism"]),
    _viral_meme(68, "bahlil_pidana", "bahlil", "KENA PIDANA?", "MEME DOANG",
                satire=["political_satire"]),
    _viral_meme(69, "megawati_mega", "megawati", "BU MEGA BILANG", "PDIP JAGA NEGERI",
                satire=["political_satire"]),
    _viral_meme(70, "mahfud_bukti", "mahfud", "GAK ADA BUKTI", "TIDAK ADA BUKTI",
                satire=["political_satire"]),
    _viral_meme(71, "luhut_investasi", "luhut", "INVESTASI ASING", "OMNIBUS LAW",
                satire=["political_satire"]),
    _viral_meme(72, "sri_apbn", "sri_mulyani", "DEFISIT APBN", "PAJAK NAIK",
                satire=["political_satire", "criticism"], portrait="sri_mulyani"),
    _viral_meme(73, "erick_bumn", "erick_thohir", "MENTERI BUMN", "GARUDA INDONESIA",
                satire=["political_satire"], portrait="erick"),
    _viral_meme(74, "puan_dpr", "puan", "MBAK PUAN BILANG", "KETUA DPR",
                satire=["political_satire"]),
    _viral_meme(75, "emil_jabar", "ridwan_kamil", "KANG EMIL", "JAWA BARAT JUARA",
                satire=["political_satire", "humor"], portrait="emil"),
    _viral_meme(76, "prabowo_ikhlas", "prabowo", "RAKYAT HARUS IKHLAS BRO", "SANTUY BRO",
                satire=["political_satire", "sarcasm"]),
    _viral_meme(77, "prabowo_ppn_2026", "prabowo", "PPN NAIK LAGI 2026", "NEGARA BUTUH WANG",
                satire=["political_satire", "criticism"]),
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


def _meme_font(size: int):
    from PIL import ImageFont

    candidates = (
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def synthesize_meme_image(dest: Path, spec: dict) -> bool:
    """Buat meme format klasik: foto pejabat + teks atas/bawah."""
    import io
    from PIL import Image, ImageDraw

    base_url = spec["base_url"]
    top_text = spec.get("top_text", "")
    bottom_text = spec.get("bottom_text", "")

    req = urllib.request.Request(base_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        base = Image.open(io.BytesIO(r.read())).convert("RGB")

    w = 960
    scale = w / base.width
    h = max(540, int(base.height * scale))
    base = base.resize((w, h), Image.Resampling.LANCZOS)

    bar_h = max(100, h // 5)
    canvas = Image.new("RGB", (w, h + bar_h * 2), (0, 0, 0))
    canvas.paste(base, (0, bar_h))
    draw = ImageDraw.Draw(canvas)
    font = _meme_font(max(28, w // 22))

    for text, y_start in ((top_text, 8), (bottom_text, bar_h + h + 8)):
        if not text:
            continue
        lines = _wrap_text(text, font, w - 40, draw)
        y = y_start
        for line in lines:
            tw = draw.textlength(line, font=font)
            x = (w - tw) / 2
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))
            y += font.size + 8

    canvas.save(dest, "JPEG", quality=92)
    return dest.stat().st_size > 10_000


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

        if spec := sample.get("synthesize_meme"):
            try:
                if synthesize_meme_image(dest, spec):
                    ok += 1
                    manifest.append({"file": fname, "synthesized_meme": True, "spec": spec})
                    expected[fname] = sample["expected"]
                    print(f"  OK synthesized ({dest.stat().st_size // 1024} KB)")
                else:
                    print("  FAIL synthesize (too small)")
                    expected[fname] = sample["expected"]
            except Exception as e:
                print(f"  FAIL synthesize: {e}")
                expected[fname] = sample["expected"]
            continue

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
            manifest.append({"file": fname, "url": url, "query": sample.get("query")})
            expected[fname] = sample["expected"]
            print(f"  OK ({dest.stat().st_size // 1024} KB)")
        else:
            expected[fname] = sample["expected"]

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    (out / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False))
    print(f"\n{ok}/{len(SAMPLES)} ready -> {out}")


if __name__ == "__main__":
    main()
