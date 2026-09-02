# Setup WDA — path utama lab (CI + Linux)

Ini jalur **default** untuk develop di Linux. Tidak perlu Mac di tangan.

```
[GitHub Actions macOS]  build unsigned .ipa
        ↓ download artifact
[Linux + AltServer]     sign + install (Apple ID gratis)
        ↓ USB
[iPhone]                Trust → WDA siap → go-ios / Appium
```

Apple ID **gratis** = cert ~**7 hari**, max ~**3 app** sideload. Setelah expire: ulang sign/install (langkah 3).

IPA unsigned dibuild di GitHub Actions (macOS runner) dan di-download sebagai artifact — **tidak** di-commit ke git.

---

## 0. Prasyarat Linux

```bash
# Debian/Ubuntu
sudo apt update
sudo apt install -y usbmuxd libimobiledevice-utils curl unzip wget

# HP colok USB, unlock, tap Trust
idevice_id -l          # harus muncul UDID
idevicepair pair       # kalau belum pair
```

---

## 1. IPA (dari GitHub Actions)

1. Buka workflow di monorepo:  
   https://github.com/purwowd/siksik/actions/workflows/build-wda.yml  
2. Pilih run sukses untuk commit SIKSIK yang akan dipakai. Push ke branch mana pun
   memicu build; `Run workflow` tetap tersedia untuk rebuild manual.
3. Download artifact **`WebDriverAgentRunner-ipa`**. Artifact berisi IPA unsigned,
   `SHA256SUMS`, dan `build-provenance.json`.
4. Verifikasi checksum dan pastikan `siksik.revision` pada provenance sama dengan
   SHA commit dari run GitHub Actions tersebut.
5. Simpan hasil yang sudah diverifikasi sebagai
   `~/wda/WebDriverAgentRunner.ipa`; lokasi ini diprioritaskan oleh runtime.
   IPA yang sudah ada di root `ios-media-puller` adalah fallback lama, bukan
   bukti bahwa commit SIKSIK terbaru sudah melewati build GitHub Actions.

```bash
sha256sum -c SHA256SUMS
unzip -p WebDriverAgentRunner.ipa \
  Payload/WebDriverAgentRunner-Runner.app/siksik-build-provenance.json \
  | cmp - build-provenance.json
python3 -m json.tool build-provenance.json
```

IPA hanya memuat WebDriverAgent sebagai jembatan UI. Flow, selector, batas crawl,
dan ingestion terbaru tetap dijalankan dari kode host SIKSIK; provenance mengikat
IPA ke revisi host yang divalidasi saat build. Toolchain CI dipin ke macOS 15,
Xcode 16.4, dan WDA 16.2.0 agar rebuild tidak diam-diam berubah versi.

---

## 2. Pasang AltServer-Linux

Binary release: https://github.com/NyaMisty/AltServer-Linux/releases  

```bash
cd ~/wda
# contoh: unduh asset linux x86_64 dari release terbaru, rename jadi AltServer
chmod +x AltServer
./AltServer -h
```

### Kalau error anisette / `-36607`

Apple sering tolak anisette default. Jalankan server anisette sendiri (atau pakai yang shared), lalu:

```bash
export ALTSERVER_ANISETTE_SERVER="http://127.0.0.1:6969"
# sesuaikan URL server kamu
```

Detail: README [NyaMisty/AltServer-Linux](https://github.com/NyaMisty/AltServer-Linux).

---

## 3. Sign + install ke iPhone

```bash
UDID=$(idevice_id -l | head -1)
echo "UDID=$UDID"

# Jangan commit password. Pakai app-specific password kalau Apple ID pakai 2FA.
./AltServer \
  -u "$UDID" \
  -a "APPLE_ID@email.com" \
  -p "APPLE_ID_PASSWORD_OR_APP_SPECIFIC" \
  ./WebDriverAgentRunner.ipa
```

Atau pakai helper di repo ini.

**WSL (lab SATRIA):** jangan AltServer langsung di usbipd. Path yang berhasil:

```bash
bash ios_automator/scripts/install_wda_windows.sh
```

UAC Windows → Yes. Kode 6 digit diketik di jendela PowerShell. Setelah sukses USB kembali ke WSL.

**Linux native USB:**

```bash
export APPLE_ID='kamu@email.com'
export APPLE_ID_PASSWORD='...'   # app-specific password jika 2FA
./ios_automator/scripts/install_wda_altserver.sh ./WebDriverAgentRunner.ipa
```

Di iPhone:

1. **Settings → General → VPN & Device Management** → Trust Apple ID kamu  
2. **Settings → Privacy & Security → Developer Mode** ON (iOS 16+), restart kalau diminta  

Cek terpasang:

```bash
ideviceinstaller -l | grep -i -E 'webdriver|xctrunner' || true
# atau:
ios list  # kalau sudah ada go-ios
```

Catat **bundle id** yang terpasang (sering masih `com.facebook.WebDriverAgentRunner.xctrunner`, kadang berubah setelah resign).

```bash
export WDA_BUNDLE=com.facebook.WebDriverAgentRunner.xctrunner
```

---

## 4. Start WDA + IG flow (harian)

**Cara termudah — satu perintah** (tunnel + WDA + IG Profile → Archive):

```bash
cd ~/ios-media-puller
./ios_automator/scripts/run_ig_profile.sh
```

Panduan setup dari nol: [README.md § Setup pertama kali](../README.md#setup-pertama-kali--ig-profile--archive-linux).

**Manual / debug stack saja:**

```bash
export PATH="$HOME/.local/bin:$PATH"
bash ios_automator/scripts/run_stack.sh
curl -sf http://127.0.0.1:8100/status
```

Stack Appium (opsional, legacy): [`appium/README.md`](./appium/README.md)

---

## Troubleshooting

| Gejala | Fix |
|--------|-----|
| `idevice_id` kosong | Cable data, unlock, Trust; cek `usbmuxd` |
| `Could not connect to lockdownd` / Mux `-8` | USB session wedged (sering setelah AltServer `Failed to write`). Unlock HP, lalu `bash ios_automator/scripts/recover_ios_lockdown.sh`. Bukan bukti WDA hilang. |
| AltServer `Failed to write app data` di WSL | `usbmuxd` 1.1.1 drop paket 65536. Jangan `ideviceinstaller -i` / AltServer di usbipd. Pasang lewat `bash ios_automator/scripts/install_wda_windows.sh`, lalu kembalikan USB ke WSL. |
| AltServer anisette / `-36607` | Set `ALTSERVER_ANISETTE_SERVER` ke server sendiri |
| Untrusted developer | Settings → VPN & Device Management → Trust |
| App hilang / tidak jalan setelah ~7 hari | Ulang langkah 3 (resign + install) |
| `Number: 3` / WDA tidak listen | `ios runwda` belum jalan, atau bundle id salah |
| Limit 3 apps | Hapus sideload lain, atau pakai Apple Developer berbayar |
| Wi‑Fi refresh | Butuh netmuxd + AltServer daemon (opsional; USB lebih sederhana) |

---

## Alternatif: Mac + Xcode lokal

Kalau ada Mac: [`SETUP_WDA.md`](./SETUP_WDA.md) (Product → Test). Hasil akhirnya sama: WDA terpasang + trusted di iPhone.
