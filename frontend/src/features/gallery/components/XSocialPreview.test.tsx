import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { XSocialPreview } from "./XSocialPreview";

describe("XSocialPreview", () => {
  it("renders a clean post without detached engagement chrome", () => {
    const html = renderToStaticMarkup(
      <XSocialPreview
        preview={{
          platform: "x",
          kind: "post",
          display_name: "Saya Lapar",
          username: "@LaparSaya92719",
          body: "Makar",
          published_label: "5 hari",
        }}
        flagged
        findingBadges={["Incitement / ajakan provokatif"]}
      />,
    );

    expect(html).toContain("Saya Lapar");
    expect(html).toContain("@LaparSaya92719 · 5 hari");
    expect(html).toContain("Makar");
    expect(html).toContain("Temuan");
    expect(html).not.toContain("Suka");
    expect(html).not.toContain(">9<");
  });

  it("does not invent profile metrics when they are unavailable", () => {
    const html = renderToStaticMarkup(
      <XSocialPreview
        preview={{
          platform: "x",
          kind: "profile",
          username: "@LaparSaya92719",
          birth_date: "Lahir 15 Desember 1998",
          following: null,
          followers: null,
        }}
      />,
    );

    expect(html).toContain("@LaparSaya92719");
    expect(html).toContain("Lahir 15 Desember 1998");
    expect(html).not.toContain("Mengikuti");
    expect(html).not.toContain("Pengikut");
  });
});
