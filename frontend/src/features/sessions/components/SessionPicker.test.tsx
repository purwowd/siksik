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
});
