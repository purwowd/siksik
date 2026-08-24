import { expect, test } from "@playwright/test";
import {
  DEMO,
  prepareTidakLulusSession,
  reviewAllPending,
  type SessionSummary,
} from "./helpers";

async function loginAs(page: import("@playwright/test").Page, role: keyof typeof DEMO) {
  const cred = DEMO[role];
  await page.goto("/");
  await page.getByLabel(/Nama pengguna/i).fill(cred.user);
  await page.getByLabel(/Kata sandi/i).fill(cred.pass);
  await page.getByRole("button", { name: /Lanjutkan/i }).click();
  await expect(page).toHaveURL(/\/(operator|temuan|laporan|dasbor)/, { timeout: 20_000 });
}

test.describe.serial("SATRIA workflow hardening", () => {
  test.describe.configure({ timeout: 420_000 });

  let sessionId = "";
  let analisToken = "";

  test.beforeAll(async ({ request, baseURL }) => {
    test.setTimeout(420_000);
    const origin = baseURL || "http://127.0.0.1:5174";
    const prep = await prepareTidakLulusSession(request, origin);
    sessionId = prep.sessionId;
    analisToken = prep.analisToken;
  });

  test("analis confirms a pending finding from UI", async ({ page }) => {
    await loginAs(page, "analis");
    await page.goto(`/temuan?sesi=${sessionId}&filter=pending`);

    const confirmBtn = page.getByRole("button", { name: "Konfirmasi" }).first();
    await expect(confirmBtn).toBeVisible({ timeout: 20_000 });
    await confirmBtn.click();

    await expect(page.getByText("Dikonfirmasi").first()).toBeVisible({ timeout: 20_000 });
  });

  test("pimpinan sees authorize blocked while review pending", async ({ page, baseURL, request }) => {
    const origin = baseURL || "http://127.0.0.1:5174";
    // Pastikan masih ada pending (test sebelumnya hanya konfirmasi satu)
    const pendingRes = await request.get(
      `${origin}/api/v1/sessions/${sessionId}/findings?page=1&page_size=500&review_status=pending`,
      { headers: { Authorization: `Bearer ${analisToken}` } },
    );
    const pendingBody = (await pendingRes.json()) as { total: number };
    test.skip(pendingBody.total === 0, "no pending findings left after prior review");

    await loginAs(page, "pimpinan");
    await page.goto(`/laporan?sesi=${sessionId}`);

    await expect(page.locator(".authorize-block")).toBeVisible();
    await expect(page.getByRole("button", { name: /Sahkan/i })).toBeDisabled();
  });

  test("pimpinan authorizes after all findings reviewed", async ({ page, baseURL, request }) => {
    const origin = baseURL || "http://127.0.0.1:5174";
    await reviewAllPending(request, origin, analisToken, sessionId, "confirmed");

    await loginAs(page, "pimpinan");
    await page.goto(`/laporan?sesi=${sessionId}`);

    await expect(page.locator(".authorize-block")).toHaveCount(0);
    await page.getByLabel(/Catatan pengesahan/i).fill("Disahkan setelah review E2E");
    await page.getByRole("button", { name: /Sahkan/i }).click();

    await expect(page.getByText(/Disahkan · pimpinan/i)).toBeVisible({ timeout: 20_000 });
  });
});
