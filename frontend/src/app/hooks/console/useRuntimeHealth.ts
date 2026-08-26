import { useCallback, useState } from "react";
import { api, saveAuth, type AuthSession, type DeviceInfo, type VisionHealth } from "@/shared/api/client";

export function useRuntimeHealth(_auth: AuthSession | null, setAuth: (a: AuthSession | null) => void, setError: (e: string | null) => void) {
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(true);
  const [selected, setSelected] = useState<DeviceInfo | null>(null);
  const [gpu, setGpu] = useState(false);
  const [toolchain, setToolchain] = useState<Record<string, boolean>>({});
  const [vision, setVision] = useState<VisionHealth>({});
  const [runtimeEnv, setRuntimeEnv] = useState<string>("host");
  const [imageCapQuick, setImageCapQuick] = useState(800);
  const [imageCapFull, setImageCapFull] = useState(3000);
  const [zipMaxMb, setZipMaxMb] = useState(512);
  const [zipEnabled, setZipEnabled] = useState(true);

  const refreshDevices = useCallback(async () => {
    setDevicesLoading(true);
    try {
      const [h, d] = await Promise.all([api.health(), api.devices()]);
      setGpu(h.gpu_available);
      setToolchain(h.extras?.toolchain || {});
      setVision(h.extras?.vision || {});
      if (h.extras?.runtime_env) setRuntimeEnv(String(h.extras.runtime_env));
      if (typeof h.extras?.image_cap_quick === "number") setImageCapQuick(h.extras.image_cap_quick);
      if (typeof h.extras?.image_cap_full === "number") setImageCapFull(h.extras.image_cap_full);
      if (typeof h.extras?.zip_max_mb === "number") setZipMaxMb(h.extras.zip_max_mb);
      if (typeof h.extras?.zip_enabled === "boolean") setZipEnabled(h.extras.zip_enabled);
      const live = d.filter((x) => !x.simulated);
      setDevices(live);
      setSelected((prev) => live.find((x) => x.device_id === prev?.device_id) ?? live[0] ?? null);
    } finally {
      setDevicesLoading(false);
    }
  }, []);

  const bootstrapHealth = useCallback(async () => {
    try {
      await refreshDevices();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Gagal memuat API";
      setError(msg);
      if (String(msg).toLowerCase().includes("autentikasi")) {
        saveAuth(null);
        setAuth(null);
      }
    }
  }, [refreshDevices, setAuth, setError]);

  const liveDevices = devices.filter((d) => !d.simulated);
  const mediaTextOn = !!vision.media_text?.enabled;
  const ocrEngineOn = !!(vision.ocr?.enabled && vision.ocr?.available);
  const whisperOn = !!(vision.media_text?.whisper || vision.gpu_stack?.backends?.whisper?.available);
  const gpuStackOn = !!vision.gpu_stack?.enabled;

  return {
    devices,
    selected,
    setSelected,
    gpu,
    toolchain,
    vision,
    runtimeEnv,
    imageCapQuick,
    imageCapFull,
    zipMaxMb,
    zipEnabled,
    refreshDevices,
    bootstrapHealth,
    devicesLoading,
    liveDevices,
    mediaTextOn,
    ocrEngineOn,
    whisperOn,
    gpuStackOn,
  };
}
