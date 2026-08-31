import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SessionSummary } from "@/shared/api/client";
import { SessionPicker } from "@/features/sessions/components/SessionPicker";

describe("SessionPicker", () => {
  it("keeps the picker disabled while an acquisition is active", () => {
    const sessions = [
      { id: "completed", status: "completed", label: "Sesi selesai" },
      { id: "running", status: "acquiring", label: "Akuisisi aktif" },
    ] as SessionSummary[];

    const markup = renderToStaticMarkup(
      <SessionPicker
        sessions={sessions}
        value="running"
        onChange={() => undefined}
      />,
    );

    expect(markup).toMatch(
      /<select id="sadt-session-pick" disabled=""/,
    );
    expect(markup).toContain("Sesi berjalan dikunci sampai akuisisi selesai");
  });

  it("does not show the acquisition-method pipeline on the session card", () => {
    const sessions = [
      {
        id: "done",
        status: "completed",
        label: "Sesi selesai",
        mode: "quick",
        recommendation: "MENUNGGU REVIEW",
        progress: {
          acquisition_method:
            "android_agent_inventory_complete+preprocessing_partial+chrome_cdp",
          findings_count: 17,
        },
      },
    ] as SessionSummary[];

    const markup = renderToStaticMarkup(
      <SessionPicker sessions={sessions} value="done" onChange={() => undefined} />,
    );

    expect(markup).not.toContain("Inventaris Android");
    expect(markup).not.toContain("Pra-pemrosesan");
    expect(markup).not.toContain("Riwayat browser Chrome");
    expect(markup).toContain("17 temuan");
  });
});
