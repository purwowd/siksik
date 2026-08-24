import { expect, test } from "@playwright/test";
import { DEMO } from "./helpers";

const LANDING = {
  operator: /\/operator/,
  analis: /\/temuan/,
  pimpinan: /\/laporan/,
  admin: /\/operator/,
} as const;

async function loginAs(page: import("@playwright/test").Page, role: keyof typeof DEMO) {
  const cred = DEMO[role];
  await page.goto("/");
  await page.getByLabel(/Nama pengguna/i).fill(cred.user);
  await page.getByLabel(/Kata sandi/i).fill(cred.pass);
  await page.getByRole("button", { name: /Lanjutkan/i }).click();
  await expect(page).toHaveURL(LANDING[role], { timeout: 15_000 });
}

test.describe("SATRIA console smoke", () => {
  test("login screen renders", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: /SATRIA/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Lanjutkan/i })).toBeVisible();
  });

  test("operator sees only acquisition tab", async ({ page }) => {
    await loginAs(page, "operator");
    await expect(page.getByRole("tab", { name: /Pengambilan/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Temuan/i })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: /Dasbor/i })).toHaveCount(0);
  });

  test("analis sees findings and session picker full width", async ({ page }) => {
    await loginAs(page, "analis");
    await expect(page.getByRole("tab", { name: /Temuan/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Galeri/i })).toBeVisible();
    await expect(page.getByLabel(/Cari sesi/i)).toBeVisible();
    const picker = page.locator(".session-picker");
    await expect(picker).toBeVisible();
    const box = await picker.boundingBox();
    const main = page.locator(".ent-panel.findings-panel");
    const mainBox = await main.boundingBox();
    if (box && mainBox) {
      expect(box.width).toBeGreaterThan(mainBox.width * 0.85);
    }
  });

  test("pimpinan lands on report tab", async ({ page }) => {
    await loginAs(page, "pimpinan");
    await expect(page.getByRole("tab", { name: /Hasil Akhir|Laporan/i })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Pengambilan/i })).toHaveCount(0);
  });

  test("admin navigates all primary tabs", async ({ page }) => {
    await loginAs(page, "admin");
    for (const label of [/Temuan/i, /Galeri/i, /Hasil Akhir|Laporan/i, /Dasbor/i]) {
      const tab = page.getByRole("tab", { name: label });
      await tab.click();
      await expect(tab).toHaveAttribute("aria-selected", "true");
    }
    await page.getByRole("tab", { name: /Pengambilan/i }).click();
    await expect(page).toHaveURL(/\/operator/);
  });
});
