import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AnalysisScope, type DeviceInfo } from "@/shared/api/client";
import {
  iosAcquisitionReady,
  iosSetupPanelVisible,
  type IosSetupStatus,
} from "@/features/operator/iosSetupReady";

type Params = {
  selected: DeviceInfo | null;
  acqSource: "live" | "zip";
  analysisScope: AnalysisScope;
  activeSession: boolean;
};

export function useIosSetup(p: Params) {
  const [status, setStatus] = useState<IosSetupStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const refreshInFlight = useRef(false);

  const visible = iosSetupPanelVisible(
    p.selected?.device_type,
    p.selected?.simulated,
    p.acqSource,
  );
  const deviceId = visible ? p.selected?.device_id ?? null : null;
  const readyForAcquire = iosAcquisitionReady(
    p.selected?.device_type,
    p.selected?.simulated,
    p.analysisScope,
    status,
  );

  const refresh = useCallback(async () => {
    if (!deviceId) {
      setStatus(null);
      return;
    }
    if (refreshInFlight.current || document.hidden) return;
    refreshInFlight.current = true;
    try {
      const next = await api.iosSetup(deviceId);
      setStatus(next);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Gagal membaca status iPhone");
    } finally {
      refreshInFlight.current = false;
    }
  }, [deviceId]);

  useEffect(() => {
    setCode("");
    setError(null);
    if (!deviceId) {
      setStatus(null);
      return;
    }
    void refresh();
  }, [deviceId, refresh]);

  useEffect(() => {
    if (!deviceId || p.activeSession) return;
    const timer = window.setInterval(() => {
      void refresh();
    }, 8000);
    return () => window.clearInterval(timer);
  }, [deviceId, p.activeSession, refresh]);

  const run = useCallback(
    async (action: () => Promise<IosSetupStatus>) => {
      if (!deviceId) return;
      setBusy(true);
      try {
        const next = await action();
        setStatus(next);
        setError(null);
        if (next.state !== "awaiting_apple_id_code") setCode("");
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Gagal menyiapkan iPhone");
      } finally {
        setBusy(false);
      }
    },
    [deviceId],
  );

  const start = useCallback(() => {
    if (!deviceId) return Promise.resolve();
    return run(() => api.startIosSetup(deviceId));
  }, [deviceId, run]);

  const submitCode = useCallback(() => {
    if (!deviceId || !/^\d{6}$/.test(code)) return Promise.resolve();
    return run(() => api.submitIosSetupCode(deviceId, code));
  }, [code, deviceId, run]);

  const ackTrust = useCallback(() => {
    if (!deviceId) return Promise.resolve();
    return run(() => api.ackIosSetupTrust(deviceId));
  }, [deviceId, run]);

  const cancel = useCallback(() => {
    if (!deviceId) return Promise.resolve();
    return run(() => api.cancelIosSetup(deviceId));
  }, [deviceId, run]);

  return {
    visible,
    status,
    busy,
    code,
    setCode,
    error,
    readyForAcquire,
    showWdaSteps: p.analysisScope !== "device",
    start,
    submitCode,
    ackTrust,
    cancel,
    refresh,
  };
}
