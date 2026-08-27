import type { DeviceType } from "./common";

export interface DeviceInfo {
  device_id: string;
  device_type: DeviceType;
  label: string;
  os_version?: string | null;
  connected: boolean;
  simulated: boolean;
  wda_state?: string | null;
}
