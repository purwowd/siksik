#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

ADB = "/opt/homebrew/Caskroom/android-platform-tools/37.0.0/platform-tools/adb"
SERIAL = "RRCW506F8MM"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_DIR = f"/Users/macbook/Documents/Product1/siksik/temp_crawl/manual_mapping_{TS}"
os.makedirs(OUT_DIR, exist_ok=True)

def run_adb(args, timeout=30):
    cmd = [ADB, "-s", SERIAL] + args
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return res.stdout.strip(), res.stderr.strip(), res.returncode

def parse_bounds(bounds_str):
    m = re.findall(r"\[(\d+),(\d+)\]", bounds_str)
    if len(m) == 2:
        x1, y1 = int(m[0][0]), int(m[0][1])
        x2, y2 = int(m[1][0]), int(m[1][1])
        return x1, y1, x2, y2, (x1 + x2) // 2, (y1 + y2) // 2
    return 0, 0, 0, 0, 0, 0

def dump_ui(name):
    dev_xml = f"/sdcard/dump_{name}.xml"
    dev_png = f"/sdcard/dump_{name}.png"
    run_adb(["shell", "uiautomator", "dump", "--compressed", dev_xml])
    run_adb(["pull", dev_xml, f"{OUT_DIR}/{name}.xml"])
    run_adb(["shell", "screencap", "-p", dev_png])
    run_adb(["pull", dev_png, f"{OUT_DIR}/{name}.png"])
    
    xml_path = f"{OUT_DIR}/{name}.xml"
    elements = []
    if os.path.exists(xml_path):
        try:
            tree = ET.parse(xml_path)
            for node in tree.iter("node"):
                text = node.attrib.get("text", "")
                desc = node.attrib.get("content-desc", "")
                res_id = node.attrib.get("resource-id", "")
                cls = node.attrib.get("class", "")
                bounds = node.attrib.get("bounds", "")
                x1, y1, x2, y2, cx, cy = parse_bounds(bounds)
                if text or desc or res_id:
                    elements.append({
                        "text": text,
                        "desc": desc,
                        "res_id": res_id,
                        "class": cls,
                        "bounds": bounds,
                        "center": (cx, cy),
                        "clickable": node.attrib.get("clickable", "false") == "true"
                    })
        except Exception as e:
            print(f"Error parsing XML for {name}: {e}")
    print(f"[{name}] Dumped {len(elements)} elements")
    return elements

def tap(cx, cy, delay=2.0):
    print(f"  -> Tapping ({cx}, {cy})")
    run_adb(["shell", "input", "tap", str(cx), str(cy)])
    time.sleep(delay)

def swipe_up(delay=2.0):
    print("  -> Swiping up...")
    run_adb(["shell", "input", "swipe", "540", "1600", "540", "600", "300"])
    time.sleep(delay)

def back(delay=1.5):
    print("  -> Pressing Back...")
    run_adb(["shell", "input", "keyevent", "4"])
    time.sleep(delay)

print(f"Mapping output directory: {OUT_DIR}")

# Step 1: Wake up device
run_adb(["shell", "input", "keyevent", "224"])
run_adb(["shell", "input", "swipe", "500", "1500", "500", "500", "200"])
time.sleep(1.0)

# Step 2: Facebook Mapping
print("\n=== STEP 1: FACEBOOK MAPPING ===")
run_adb(["shell", "am", "force-stop", "com.facebook.katana"])
time.sleep(1.0)
run_adb(["shell", "am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "-n", "com.facebook.katana/.LoginActivity"])
time.sleep(3.5)

fb_home = dump_ui("fb_01_home")
# Find profile tab or button
prof_btn = next((e for e in fb_home if "Profil" in e["desc"] or "Profile" in e["desc"] or "tab 6" in e["desc"].lower() or "tab 5" in e["desc"].lower()), None)
if not prof_btn:
    prof_btn = next((e for e in fb_home if "Lihat profil" in e["text"] or "See your profile" in e["text"] or "Menu" in e["desc"]), None)

if prof_btn:
    print(f"Found FB Profile button: {prof_btn['desc'] or prof_btn['text']} at {prof_btn['center']}")
    tap(*prof_btn["center"], delay=3.0)
else:
    print("WARNING: Could not find FB Profile button on Home!")

fb_prof = dump_ui("fb_02_profile")
print("\nFB Profile Elements Summary:")
for e in fb_prof:
    txt = e["text"] or e["desc"]
    if any(k in txt.lower() for k in ["saipul", "edit", "cerita", "story", "teman", "friend", "pengaturan", "setelan", "opsi", "more", "..."]):
        print(f"  * text='{e['text']}' desc='{e['desc']}' res='{e['res_id']}' bounds={e['bounds']} center={e['center']}")

# Look for the 3-dots button or settings on Profile
# In Facebook profile, beside 'Edit profile', there is a 3-dots button
three_dots = next((e for e in fb_prof if e["desc"] in ["Lainnya", "More", "Pengaturan Profil", "Profile Settings", "Opsi lainnya", "More options", "Action menu"] or "..." in e["text"] or "..." in e["desc"]), None)
if not three_dots:
    # Look for button next to Edit profile (center Y around edit profile button)
    edit_btn = next((e for e in fb_prof if "Edit" in (e["text"] or e["desc"]) or "Sunting" in (e["text"] or e["desc"])), None)
    if edit_btn:
        print(f"Found Edit button at {edit_btn['bounds']}. Searching for adjacent 3-dots button...")
        ey = edit_btn["center"][1]
        for e in fb_prof:
            if abs(e["center"][1] - ey) < 80 and e["center"][0] > edit_btn["center"][0]:
                three_dots = e
                print(f"Found candidate 3-dots button near Edit: {e}")
                break

if three_dots:
    print(f"Tapping 3-dots button at {three_dots['center']}")
    tap(*three_dots["center"], delay=3.0)
    fb_menu = dump_ui("fb_03_profile_settings")
    print("\nFB Profile Settings Menu Elements:")
    for e in fb_menu:
        txt = e["text"] or e["desc"]
        print(f"  * text='{e['text']}' desc='{e['desc']}' res='{e['res_id']}' bounds={e['bounds']}")
        
    # Look for Activity Log / Log Aktivitas
    act_log = next((e for e in fb_menu if any(k in (e["text"] or e["desc"]).lower() for k in ["log aktivitas", "activity log", "aktivitas anda", "your activity"])), None)
    if act_log:
        print(f"\nFound Activity Log item: '{act_log['text'] or act_log['desc']}' at {act_log['center']}. Tapping...")
        tap(*act_log["center"], delay=3.5)
        fb_act = dump_ui("fb_04_activity_log")
        print("\nFB Activity Log Elements:")
        for e in fb_act:
            txt = e["text"] or e["desc"]
            if any(k in txt.lower() for k in ["komentar", "comment", "reaksi", "reaction", "suka", "like", "kelola", "manage", "interaksi", "postingan", "arsip", "sampah"]):
                print(f"  * text='{e['text']}' desc='{e['desc']}' bounds={e['bounds']} center={e['center']}")
    else:
        print("WARNING: Activity Log item not found in Settings menu!")
else:
    print("WARNING: 3-dots / settings button not found on Profile!")

# Step 3: X (Twitter) Mapping
print("\n=== STEP 2: X (TWITTER) MAPPING ===")
run_adb(["shell", "am", "force-stop", "com.twitter.android"])
time.sleep(1.0)
run_adb(["shell", "am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "-n", "com.twitter.android/com.twitter.app.main.MainActivity"])
time.sleep(3.5)

x_home = dump_ui("x_01_home")
# Find drawer / profile icon in top left
x_avatar = next((e for e in x_home if "Show navigation drawer" in e["desc"] or "Tampilkan panel navigasi" in e["desc"] or "Account information" in e["desc"] or "Informasi akun" in e["desc"] or ("drawer" in e["desc"].lower()) or (e["center"][0] < 200 and e["center"][1] < 300)), None)
if x_avatar:
    print(f"Found X Avatar / Drawer button: {x_avatar['desc']} at {x_avatar['center']}")
    tap(*x_avatar["center"], delay=2.5)
    x_drawer = dump_ui("x_02_drawer")
    print("\nX Drawer Elements:")
    for e in x_drawer:
        txt = e["text"] or e["desc"]
        if any(k in txt.lower() for k in ["profil", "profile", "@", "lapar", "pengikut", "follower"]):
            print(f"  * text='{e['text']}' desc='{e['desc']}' res='{e['res_id']}' center={e['center']}")
    
    # Tap Profile
    x_prof_btn = next((e for e in x_drawer if (e["text"] in ["Profile", "Profil"] or e["desc"] in ["Profile", "Profil"]) or "@" in e["text"]), None)
    if x_prof_btn:
        print(f"Tapping X Profile item: '{x_prof_btn['text'] or x_prof_btn['desc']}' at {x_prof_btn['center']}")
        tap(*x_prof_btn["center"], delay=3.5)
        x_prof = dump_ui("x_03_profile")
        print("\nX Profile Elements:")
        for e in x_prof:
            txt = e["text"] or e["desc"]
            if any(k in txt.lower() for k in ["postingan", "posts", "balasan", "replies", "sorotan", "highlights", "media", "suka", "likes", "@", "mengikuti", "following", "pengikut", "followers", "edit"]):
                print(f"  * text='{e['text']}' desc='{e['desc']}' res='{e['res_id']}' bounds={e['bounds']} center={e['center']}")
        
        # Look for Replies / Balasan tab
        replies_tab = next((e for e in x_prof if e["text"] in ["Replies", "Balasan"] or e["desc"] in ["Replies", "Balasan"]), None)
        if replies_tab:
            print(f"\nFound Replies tab: '{replies_tab['text'] or replies_tab['desc']}' at {replies_tab['center']}. Tapping...")
            tap(*replies_tab["center"], delay=3.0)
            x_replies = dump_ui("x_04_replies")
            print("\nX Replies Elements:")
            for e in x_replies:
                txt = e["text"] or e["desc"]
                if txt and len(txt) > 3:
                    print(f"  * text='{e['text']}' desc='{e['desc']}' bounds={e['bounds']}")
        else:
            print("WARNING: Replies tab not found on X Profile!")
    else:
        print("WARNING: Profile button not found in X drawer!")
else:
    print("WARNING: X Avatar / Drawer button not found on Home!")

print(f"\nMapping completed! Artifacts saved to: {OUT_DIR}")
