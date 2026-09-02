import { describe, expect, it } from "vitest";
import {
  iosAcquisitionReady,
  iosSetupPanelVisible,
  iosSetupStartLabel,
  type IosSetupStatus,
} from "@/features/operator/iosSetupReady";

function status(state: IosSetupStatus["state"]): IosSetupStatus {
  return {
    state,
    message: state,
    paired: state !== "usb_unpaired" && state !== "awaiting_usb_trust",
    wda_installed: state === "ready" || state === "awaiting_developer_trust",
    ready: state === "ready",
    code_required: state === "awaiting_apple_id_code",
  };
}

describe("ios setup gating", () => {
  it("does not block Android or simulated iPhone", () => {
    expect(iosAcquisitionReady("android", false, "social", null)).toBe(true);
    expect(iosAcquisitionReady("ios", true, "social", null)).toBe(true);
    expect(iosSetupPanelVisible("android", false, "live")).toBe(false);
    expect(iosSetupPanelVisible("ios", true, "live")).toBe(false);
  });

  it("shows the iOS live panel and waits for WDA on social/combined", () => {
    expect(iosSetupPanelVisible("ios", false, "live")).toBe(true);
    expect(iosAcquisitionReady("ios", false, "social", null)).toBe(false);
    expect(iosAcquisitionReady("ios", false, "social", status("needs_wda"))).toBe(false);
    expect(iosAcquisitionReady("ios", false, "combined", status("awaiting_apple_id_code"))).toBe(
      false,
    );
    expect(iosAcquisitionReady("ios", false, "social", status("ready"))).toBe(true);
  });

  it("allows device-only acquire after USB trust without WDA", () => {
    expect(iosAcquisitionReady("ios", false, "device", status("usb_unpaired"))).toBe(false);
    expect(iosAcquisitionReady("ios", false, "device", status("needs_wda"))).toBe(true);
    expect(iosAcquisitionReady("ios", false, "device", status("ready"))).toBe(true);
  });

  it("labels the start button Pasang WDA until the phone is ready", () => {
    expect(iosSetupStartLabel("needs_wda")).toBe("Pasang WDA");
    expect(iosSetupStartLabel("usb_unpaired")).toBe("Pasang WDA");
    expect(iosSetupStartLabel(undefined)).toBe("Pasang WDA");
    expect(iosSetupStartLabel("ready")).toBe("Siapkan iPhone");
  });
});
