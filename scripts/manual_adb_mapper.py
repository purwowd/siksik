#!/usr/bin/env python3
"""
Manual ADB UI Mapper for Facebook and X (Twitter).
Captures XML hierarchies and screenshots at each navigation step and stores them in temp_crawl/manual_mapping_adb/.
"""
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ADB = "/opt/homebrew/Caskroom/android-platform-tools/37.0.0/platform-tools/adb"
SERIAL = "RRCW506F8MM"
OUTPUT_DIR = "/Users/macbook/Documents/Product1/siksik/temp_crawl/manual_mapping_adb"

def run_adb(cmd: str, check: bool = True) -> str:
    full_cmd = f"{ADB} -s {SERIAL} {cmd}"
    res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"[ADB ERROR] cmd: {cmd}\nstderr: {res.stderr.strip()}", file=sys.stderr)
    return res.stdout.strip()

def wake_and_unlock():
    print("Waking and unlocking device...")
    run_adb("shell input keyevent 224")
    time.sleep(0.5)
    run_adb("shell input swipe 500 1500 500 500 200")
    time.sleep(1)

def dump_ui(step_name: str) -> tuple[str, str]:
    xml_remote = f"/sdcard/{step_name}.xml"
    png_remote = f"/sdcard/{step_name}.png"
    xml_local = os.path.join(OUTPUT_DIR, f"{step_name}.xml")
    png_local = os.path.join(OUTPUT_DIR, f"{step_name}.png")
    
    # Retry uiautomator dump up to 3 times
    dump_ok = False
    for attempt in range(1, 4):
        run_adb(f"shell rm -f {xml_remote}")
        out = run_adb(f"shell uiautomator dump --compressed {xml_remote}", check=False)
        if "UI hierchary dumped" in out:
            dump_ok = True
            break
        time.sleep(1.0)
    
    if dump_ok:
        run_adb(f"pull {xml_remote} {xml_local}")
        print(f"  [XML] Saved {xml_local} ({os.path.getsize(xml_local)} bytes)")
    else:
        print(f"  [XML WARNING] uiautomator dump failed on device for {step_name}")
    
    # Screencap
    run_adb(f"shell screencap -p {png_remote}")
    run_adb(f"pull {png_remote} {png_local}")
    print(f"  [PNG] Saved {png_local} ({os.path.getsize(png_local)} bytes)")
    
    return xml_local, png_local

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    wake_and_unlock()

    print("\n=======================================================")
    print("MAPPING FACEBOOK (com.facebook.katana)")
    print("=======================================================")
    
    # Step FB 1: Fresh Launch
    print("\n[FB 1] Fresh Launch Facebook...")
    run_adb("shell am force-stop com.facebook.katana")
    time.sleep(1)
    run_adb("shell am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n com.facebook.katana/.LoginActivity")
    time.sleep(4)
    dump_ui("fb_01_launch")

    # Step FB 2: Open Menu Drawer from Kabar (tap avatar top left at 80, 170)
    print("\n[FB 2] Open Menu Drawer (tap avatar at 80, 170)...")
    run_adb("shell input tap 80 170")
    time.sleep(3)
    dump_ui("fb_02_drawer")

    # Step FB 3: Open Profile from Drawer (tap user name card at 400, 220)
    print("\n[FB 3] Open Own Profile (tap user card at 400, 220)...")
    run_adb("shell input tap 400 220")
    time.sleep(3)
    dump_ui("fb_03_profile_header")

    # Step FB 4: Scroll down profile for own posts
    print("\n[FB 4] Scroll Profile for Posts...")
    run_adb("shell input swipe 540 1800 540 800 300")
    time.sleep(2)
    dump_ui("fb_04_profile_posts")

    # Step FB 5: Navigate to Settings & Privacy
    print("\n[FB 5] Navigate to Settings & Privacy...")
    run_adb("shell am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n com.facebook.katana/.LoginActivity")
    time.sleep(2)
    run_adb("shell input tap 80 170")
    time.sleep(2)
    run_adb("shell input tap 400 1570") # Expand Pengaturan & privasi
    time.sleep(2)
    run_adb("shell input tap 400 260") # Tap Pengaturan
    time.sleep(3)
    dump_ui("fb_05_settings")

    # Step FB 6: Open Activity Log (Log Aktivitas) from Settings
    print("\n[FB 6] Open Activity Log from Settings...")
    run_adb("shell input swipe 540 1800 540 600 300")
    time.sleep(1.5)
    run_adb("shell input tap 350 960") # Tap Log aktivitas
    time.sleep(3)
    # Dismiss banner if present
    run_adb("shell input tap 930 110")
    time.sleep(1)
    dump_ui("fb_06_activity_log_hub")

    # Step FB 7: Expand Aktivitas Facebook Anda
    print("\n[FB 7] Open Activity Log -> Aktivitas Facebook Anda...")
    run_adb("shell input tap 940 510") # Expand Aktivitas Facebook Anda
    time.sleep(2)
    dump_ui("fb_07_activity_expanded")

    print("\n=======================================================")
    print("MAPPING X / TWITTER (com.twitter.android)")
    print("=======================================================")
    
    # Step X 1: Fresh Launch
    print("\n[X 1] Fresh Launch X...")
    run_adb("shell am force-stop com.twitter.android")
    time.sleep(1)
    run_adb("shell am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n com.twitter.android/com.twitter.app.main.MainActivity")
    time.sleep(4)
    dump_ui("x_01_launch")

    # Step X 2: Open Navigation Drawer (tap top left avatar at 80, 120)
    print("\n[X 2] Open Nav Drawer (tap top left avatar at 80, 120)...")
    run_adb("shell input tap 80 120")
    time.sleep(3)
    dump_ui("x_02_drawer")

    # Step X 3: Tap Profile in Drawer (at 300, 420)
    print("\n[X 3] Open Profile (tap Profile at 300, 420)...")
    run_adb("shell input tap 300 420")
    time.sleep(3)
    dump_ui("x_03_profile_header")

    # Step X 4: Posts Tab
    print("\n[X 4] Posts Tab & Timeline...")
    run_adb("shell input swipe 540 1800 540 900 300")
    time.sleep(2)
    dump_ui("x_04_posts_timeline")

    # Step X 5: Replies Tab (Balasan)
    print("\n[X 5] Replies Tab (tap Balasan)...")
    run_adb("shell input swipe 540 900 540 1800 300")
    time.sleep(1)
    run_adb("shell input tap 330 1120") # Tap Balasan tab
    time.sleep(2)
    dump_ui("x_05_replies_tab")
    
    # Step X 6: Replies Timeline scroll
    print("\n[X 6] Replies Timeline Scroll...")
    run_adb("shell input swipe 540 1800 540 900 300")
    time.sleep(2)
    dump_ui("x_06_replies_timeline")

    print("\nMapping complete. All XMLs and PNGs saved in:", OUTPUT_DIR)

if __name__ == "__main__":
    main()
