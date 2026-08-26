import { describe, expect, it } from "vitest";
import { displayPath } from "@/shared/lib/displayPath";
import { sessionStatusLabel } from "@/shared/lib/sessionStatus";
import { humanApiError } from "@/shared/api/http";
import { humanProgressMessage } from "@/shared/lib/humanProgress";

describe("panitia display helpers", () => {
  it("shortens filesystem paths to the last folders", () => {
    expect(displayPath("/data/staging/abc/DCIM/foto.jpg")).toBe("DCIM/foto.jpg");
    expect(displayPath("foto.jpg")).toBe("foto.jpg");
    expect(displayPath("")).toBe("—");
    expect(displayPath("/data/staging/abc/DCIM/799c80e00b477d50a4aeb9b88c71324a.jpg")).toBe(
      "DCIM/Foto",
    );
  });

  it("formats ISO timestamps without the T/Z noise", async () => {
    const { displayStamp } = await import("@/shared/lib/displayPath");
    expect(displayStamp("2026-07-17T12:30:00Z")).toBe("2026-07-17 12:30");
    expect(displayStamp("")).toBe("—");
  });

  it("rewrites hashed staging names into album labels", async () => {
    const { displayMediaName, isOpaqueFileName } = await import("@/shared/lib/displayPath");
    expect(isOpaqueFileName("799c80e00b477d50a4aeb9b88c71324a.jpg")).toBe(true);
    expect(displayMediaName("799c80e00b477d50a4aeb9b88c71324a.jpg", "", "Pratinjau cache")).toBe(
      "Foto · Pratinjau cache",
    );
    expect(displayMediaName("IMG-20260717-WA0066.jpg")).toBe("IMG-20260717-WA0066.jpg");
  });

  it("maps live session statuses to Indonesian labels", () => {
    expect(sessionStatusLabel("acquiring")).toBe("Mengambil data");
    expect(sessionStatusLabel("awaiting_access")).toBe("Menunggu izin HP");
    expect(sessionStatusLabel("failed")).toBe("Gagal");
  });

  it("does not dump raw JSON API details to operators", () => {
    expect(humanApiError(422, [{ loc: ["body"], msg: "x" }])).toBe(
      "Data tidak valid. Periksa isian lalu coba lagi.",
    );
    expect(humanApiError(409, "Sesi Ahmad masih berjalan. Batalkan atau tunggu selesai — satu pemeriksaan per mesin.")).toContain(
      "masih berjalan",
    );
  });

  it("rewrites lab progress jargon for operators", () => {
    expect(humanProgressMessage("Build APK Android agent terbaru")).toBe(
      "Menyiapkan aplikasi SATRIA di HP",
    );
    expect(humanProgressMessage("Membuat koneksi ADB lokal")).toBe("Menghubungkan HP ke konsol");
  });
});

describe("mission next-action", () => {
  it("tells operators to fill the case when idle", async () => {
    const { missionNextAction } = await import("@/shared/lib/missionNext");
    expect(missionNextAction("operator", null).action).toMatch(/Isi kasus|HP/);
    expect(missionNextAction("operator", null).kicker).toBe("Lanjut");
    expect(missionNextAction("analis", null).action).toMatch(/Pilih kasus/i);
    expect(missionNextAction("pimpinan", null).action).not.toMatch(/stasiun/i);
  });
});

describe("method summary", () => {
  it("collapses the acquisition chain for chrome pills", async () => {
    const { methodSummary } = await import("@/features/dashboard/lib/dashboardLabels");
    expect(
      methodSummary(
        "android_agent_inventory_partial+preprocessing_partial+selection_confirmed+android_agent_direct_manifest+android_recovery_quick_partial",
      ),
    ).toBe("Transfer dari HP · Recovery sampah Android (sebagian)");
    expect(methodSummary("zip_upload")).toBe("Unggah arsip perangkat");
  });
});
