import type { AnalysisScope } from "@/shared/api/types/common";

export type IosSetupState =
  | "usb_unpaired"
  | "awaiting_usb_trust"
  | "developer_mode_off"
  | "needs_wda"
  | "installing_wda"
  | "awaiting_apple_id_code"
  | "awaiting_developer_trust"
  | "ready"
  | "failed";

export interface IosSetupStatus {
  state: IosSetupState;
  message: string;
  paired: boolean;
  developer_mode?: boolean | null;
  wda_installed: boolean;
  wda_trusted?: boolean | null;
  apple_id_hint?: string | null;
  ready: boolean;
  code_required: boolean;
}

const USB_BLOCKING: ReadonlySet<IosSetupState> = new Set([
  "usb_unpaired",
  "awaiting_usb_trust",
]);

export function iosAcquisitionReady(
  deviceType: string | undefined,
  simulated: boolean | undefined,
  analysisScope: AnalysisScope,
  setup: IosSetupStatus | null,
): boolean {
  if (deviceType !== "ios" || simulated) return true;
  if (!setup) return false;
  if (setup.state === "ready") return true;
  if (analysisScope === "device") return !USB_BLOCKING.has(setup.state);
  return false;
}

export function iosSetupPanelVisible(
  deviceType: string | undefined,
  simulated: boolean | undefined,
  acqSource: "live" | "zip",
): boolean {
  return acqSource === "live" && deviceType === "ios" && !simulated;
}
