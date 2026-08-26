import { expect, test, type Page } from "@playwright/test";
import { DEMO } from "./helpers";

const LANDING = {
  operator: /\/penerimaan/,
  analis: /\/temuan/,
  pimpinan: /\/laporan/,
  admin: /\/penerimaan/,
} as const;

const TABS = {
  operator: [/Penerimaan/],
  analis: [/Temuan/, /Galeri/, /Laporan/, /Ikhtisar/],
  pimpinan: [/Temuan/, /Galeri/, /Laporan/, /Ikhtisar/],
  admin: [/Penerimaan/, /Temuan/, /Galeri/, /Laporan/, /Ikhtisar/],
} as const;

const FORBIDDEN = {
  operator: ["/temuan", "/galeri", "/laporan", "/dasbor", "/ikhtisar"],
  analis: ["/operator", "/penerimaan"],
  pimpinan: ["/operator", "/penerimaan"],
  admin: [] as string[],
} as const;

const ENGLISH_KICKERS = /Intake|Live ops|Command console|Authenticate|NEXT\b|Evidence\b|Decision\b/;

async function loginAs(page: Page, role: keyof typeof DEMO) {
  const cred = DEMO[role];
  await page.goto("/");
  await page.getByLabel(/Nama pengguna/i).fill(cred.user);
  await page.getByLabel(/Kata sandi/i).fill(cred.pass);
  await page.getByRole("button", { name: /Lanjutkan/i }).click();
  await expect(page).toHaveURL(LANDING[role], { timeout: 20_000 });
  await expect(page.getByRole("button", { name: /Keluar/i })).toBeVisible();
}

async function tabNames(page: Page) {
  return page.getByRole("tab").allTextContents();
}

async function pickFirstSession(page: Page) {
  const select = page.locator("#sadt-session-pick");
  await expect(select).toBeVisible({ timeout: 15_000 });
  const values = await select.locator("option").evaluateAll((opts) =>
    opts.map((o) => (o as HTMLOptionElement).value).filter(Boolean),
  );
  if (values.length === 0) return false;
  await select.selectOption(values[0]);
  await expect(select).toHaveValue(values[0]);
  return true;
}

async function assertNoEnglishKickers(page: Page) {
  await expect(page.getByText(ENGLISH_KICKERS)).toHaveCount(0);
}

test.describe("role × page matrix (live lab, no mutate)", () => {
  test("login gate: copy, no demo chips, bad password", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: /SATRIA/i })).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Masuk$/i })).toBeVisible();
    await expect(page.getByText(/Penerimaan/)).toBeVisible();
    await expect(page.getByText(/Tinjauan/)).toBeVisible();
    await expect(page.getByText(/Keputusan/)).toBeVisible();
    await expect(page.getByLabel(/Akun lab/i)).toHaveCount(0);
    await expect(page.getByText(/PoC lab|Tur demo|Lab pengembangan/i)).toHaveCount(0);
    await assertNoEnglishKickers(page);

    await page.getByLabel(/Nama pengguna/i).fill("operator");
    await page.getByLabel(/Kata sandi/i).fill("salah-password");
    await page.getByRole("button", { name: /Lanjutkan/i }).click();
    await expect(page.locator(".error-banner")).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/\/$/);
  });

  test("operator: penerimaan only, form, USB scan, forbidden routes", async ({ page }) => {
    await loginAs(page, "operator");
    await expect(page.getByRole("tab", { name: /Penerimaan/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Temuan/i })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: /Galeri/i })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: /Laporan/i })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: /Ikhtisar/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Panduan singkat/i })).toHaveCount(0);

    await expect(page.getByRole("heading", { name: /^Penerimaan$/ })).toBeVisible();
    await expect(page.getByLabel(/Nama lengkap/i)).toBeVisible();
    await expect(page.getByLabel(/No\. peserta/i)).toBeVisible();
    await expect(page.getByLabel(/NIK/i)).toBeVisible();
    await expect(page.getByLabel(/Instansi/i)).toBeVisible();
    await expect(page.getByLabel(/Sumber analisa/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /Pindai ulang USB/i })).toBeVisible();
    await expect(page.getByText(/HP saja/)).toBeVisible();
    await expect(page.locator(".ent-case-checks")).toContainText("Identitas");
    await expect(page.locator(".ent-case-checks")).toContainText("Data masuk");
    await expect(page.locator(".ent-case-checks")).toContainText("Tinjauan");
    await expect(page.locator(".ent-case-checks")).toContainText("Pengesahan");
    await expect(page.getByRole("button", { name: /Jalankan pemeriksaan/i })).toBeDisabled();

    await page.getByLabel(/Nama lengkap/i).fill("Peserta Audit UI");
    await page.getByLabel(/No\. peserta/i).fill("AUDIT-0001");
    await page.getByLabel(/NIK/i).fill("123");
    await expect(page.getByText(/NIK harus 16 digit/)).toBeVisible();
    await page.getByRole("button", { name: /Pindai ulang USB/i }).click();
    await expect(page.locator(".error-banner")).toHaveCount(0);

    await assertNoEnglishKickers(page);
    await expect(page.getByText(/Pipeline|Live ops/i)).toHaveCount(0);

    for (const path of FORBIDDEN.operator) {
      await page.goto(path);
      await expect(page).toHaveURL(LANDING.operator);
    }

    await page.getByRole("button", { name: /Keluar/i }).click();
    await expect(page.getByRole("button", { name: /Lanjutkan/i })).toBeVisible();
  });

  test("analis: temuan queue, gallery, report read, dashboard, no start", async ({ page }) => {
    await loginAs(page, "analis");
    const tabs = await tabNames(page);
    expect(tabs.join(" ")).toMatch(/Temuan/);
    expect(tabs.join(" ")).not.toMatch(/Penerimaan/);
    for (const label of TABS.analis) {
      await expect(page.getByRole("tab", { name: label })).toBeVisible();
    }

    await expect(page.getByRole("heading", { name: /^Temuan$/ })).toBeVisible();
    await expect(page.getByText(/Sisa antrean/)).toBeVisible();
    const waiting = page.getByRole("button", { name: /^Menunggu$/ });
    await expect(waiting).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText(/Antrean:.*J.*K.*pindah/)).toBeVisible();

    const picked = await pickFirstSession(page);
    if (picked) {
      await page.getByRole("button", { name: /^Semua$/ }).click();
      await expect(page.getByRole("button", { name: /^Semua$/ })).toHaveAttribute("aria-pressed", "true");
      await page.getByRole("button", { name: /^Menunggu$/ }).click();
    } else {
      await expect(page.getByText(/Belum ada kasus dipilih/)).toBeVisible();
    }

    await page.getByRole("button", { name: /Bantuan \?/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByText(/Konfirmasi temuan terpilih/)).toBeVisible();
    await page.getByRole("dialog").locator(".ent-kbd-head").getByRole("button", { name: /^Tutup$/ }).click();

    await assertNoEnglishKickers(page);

    await page.getByRole("tab", { name: /Galeri/ }).click();
    await expect(page).toHaveURL(/\/galeri/);
    await expect(page.getByRole("heading", { name: /^Galeri$/ })).toBeVisible();
    await expect(page.getByText(/Bukan seluruh isi HP/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Muat ulang/i })).toBeVisible();
    await page.getByRole("button", { name: /Semua/ }).first().click();

    await page.getByRole("tab", { name: /Laporan/ }).click();
    await expect(page).toHaveURL(/\/laporan/);
    await expect(page.getByRole("heading", { name: /^Laporan$/ })).toBeVisible();
    if (picked) {
      await expect(page.getByRole("button", { name: /PDF|Cetak/i })).toBeVisible();
      await expect(page.getByRole("button", { name: /^HTML$/ })).toBeVisible();
      await expect(page.getByRole("heading", { name: /Identitas peserta/ })).toBeVisible();
      await expect(page.getByRole("button", { name: /^Ubah$/ })).toBeVisible();
    } else {
      await expect(page.getByText(/Belum ada kasus dipilih/)).toBeVisible();
    }
    await expect(page.getByRole("button", { name: /Ekspor teknis/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Sahkan$/ })).toHaveCount(0);

    await page.getByRole("tab", { name: /Ikhtisar/ }).click();
    await expect(page).toHaveURL(/\/ikhtisar/);
    await expect(page.getByRole("heading", { name: /^Ikhtisar$/ })).toBeVisible();
    await expect(page.getByText(/Rincian teknis/)).toHaveCount(0);

    for (const path of FORBIDDEN.analis) {
      await page.goto(path);
      await expect(page).toHaveURL(LANDING.analis);
    }
  });

  test("pimpinan: keputusan, no review actions, no JSON export", async ({ page }) => {
    await loginAs(page, "pimpinan");
    await expect(page.getByRole("tab", { name: /Penerimaan/i })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /^Laporan$/ })).toBeVisible();
    const picked = await pickFirstSession(page);
    if (picked) {
      await expect(page.getByRole("button", { name: /PDF|Cetak/i })).toBeVisible();
      await expect(page.getByRole("button", { name: /Ekspor teknis/i })).toHaveCount(0);
      const sahkan = page.getByRole("button", { name: /^Sahkan$/ });
      if ((await sahkan.count()) > 0) {
        await expect(sahkan.first()).toBeVisible();
      }
      await page.getByRole("tab", { name: /Temuan/ }).click();
      await expect(page).toHaveURL(/\/temuan/);
      await pickFirstSession(page);
      await page.getByRole("button", { name: /^Semua$/ }).click();
      await expect(page.getByRole("button", { name: /Konfirmasi semua/i })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Konfirmasi$/ })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Tolak$/ })).toHaveCount(0);
    } else {
      await expect(page.getByText(/Belum ada kasus dipilih/)).toBeVisible();
    }

    await page.getByRole("tab", { name: /Galeri/ }).click();
    await expect(page.getByRole("heading", { name: /^Galeri$/ })).toBeVisible();
    await page.getByRole("tab", { name: /Ikhtisar/ }).click();
    await expect(page.getByRole("heading", { name: /^Ikhtisar$/ })).toBeVisible();
    await expect(page.getByText(/Rincian teknis/)).toHaveCount(0);
    await assertNoEnglishKickers(page);

    await page.goto("/penerimaan");
    await expect(page).toHaveURL(LANDING.pimpinan);
  });

  test("admin: all tabs, review + authorize + lab diagnostics + JSON", async ({ page }) => {
    await loginAs(page, "admin");
    for (const label of TABS.admin) {
      await expect(page.getByRole("tab", { name: label })).toBeVisible();
    }

    await expect(page.getByRole("heading", { name: /^Penerimaan$/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Pindai ulang USB/i })).toBeVisible();
    await expect(page.getByLabel(/Nama lengkap/i)).toBeVisible();

    await page.getByRole("tab", { name: /Temuan/ }).click();
    await expect(page).toHaveURL(/\/temuan/);
    await pickFirstSession(page);
    await expect(page.getByText(/Sisa antrean/)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Menunggu$/ })).toBeVisible();

    await page.getByRole("tab", { name: /Galeri/ }).click();
    await expect(page.getByRole("heading", { name: /^Galeri$/ })).toBeVisible();

    await page.getByRole("tab", { name: /Laporan/ }).click();
    const picked = await pickFirstSession(page);
    if (picked) {
      await expect(page.getByRole("button", { name: /Ekspor teknis/i })).toBeVisible();
    } else {
      await expect(page.getByText(/Belum ada kasus dipilih/)).toBeVisible();
      await expect(page.getByRole("button", { name: /Ekspor teknis/i })).toHaveCount(0);
    }

    await page.getByRole("tab", { name: /Ikhtisar/ }).click();
    await expect(page.getByRole("heading", { name: /^Ikhtisar$/ })).toBeVisible();
    await expect(page.getByText(/Rincian teknis/)).toBeVisible();
    await page.getByText(/Rincian teknis/).click();
    await expect(page.getByText(/Kesiapan alat/)).toBeVisible();
    await assertNoEnglishKickers(page);
  });
});
