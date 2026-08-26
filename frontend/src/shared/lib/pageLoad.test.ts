import { describe, expect, it } from "vitest";
import { isContentLoading } from "@/shared/lib/pageLoad";

describe("isContentLoading", () => {
  it("shows loading only when a fetch is in flight and there is no data yet", () => {
    expect(isContentLoading(true, null)).toBe(true);
    expect(isContentLoading(true, undefined)).toBe(true);
    expect(isContentLoading(true, { total: 0 })).toBe(false);
    expect(isContentLoading(false, null)).toBe(false);
    expect(isContentLoading(false, { total: 3 })).toBe(false);
  });
});
