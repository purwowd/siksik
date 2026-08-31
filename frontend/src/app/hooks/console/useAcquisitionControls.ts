import { useCallback, useMemo, useState } from "react";
import {
  api,
  type AcquisitionMode,
  type AnalysisScope,
  type DeviceInfo,
  type SessionSummary,
} from "@/shared/api/client";
import { ACTIVE } from "@/shared/constants";
import { SESSION_STORAGE_KEY } from "@/app/hooks/console/constants";
import { analysisPlanReady } from "@/features/operator/analysisScope";
import { useIosSetup } from "@/features/operator/useIosSetup";
import type { Tab } from "@/shared/types";
import type { ParticipantForm } from "@/features/operator/OperatorPage";

type Params = {
  selected: DeviceInfo | null;
  session: SessionSummary | null;
  setSession: React.Dispatch<React.SetStateAction<SessionSummary | null>>;
  mode: AcquisitionMode;
  setMode: React.Dispatch<React.SetStateAction<AcquisitionMode>>;
  analysisScope: AnalysisScope;
  deviceSources: string[];
  socialTargets: string[];
  fileCount: number;
  acqSource: "live" | "zip";
  setAcqSource: React.Dispatch<React.SetStateAction<"live" | "zip">>;
  zipFile: File | null;
  setZipFile: React.Dispatch<React.SetStateAction<File | null>>;
  zipMaxMb: number;
  zipEnabled: boolean;
  participant: ParticipantForm;
  authorizeNote: string;
  setAuthorizeNote: React.Dispatch<React.SetStateAction<string>>;
  teleRef: React.RefObject<HTMLElement | null>;
  goToTab: (tab: Tab, opts?: { sesi?: string | null }) => void;
  refreshSessionList: (opts?: { soft?: boolean }) => Promise<SessionSummary[] | undefined>;
  setError: (e: string | null) => void;
  clearQueryPages: () => void;
  clearFindingsData: () => void;
  clearReportData: () => void;
};

function participantPayload(form: ParticipantForm) {
  const full_name = form.fullName.trim();
  const registration_no = form.registrationNo.trim();
  const nik = form.nik.trim() || null;
  const organization = form.organization.trim() || null;
  return { full_name, registration_no, nik, organization };
}

export function useAcquisitionControls(p: Params) {
  const [busy, setBusy] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);

  const identityReady = useMemo(
    () =>
      p.participant.fullName.trim().length > 0 &&
      p.participant.registrationNo.trim().length > 0 &&
      (!p.participant.nik.trim() || /^\d{16}$/.test(p.participant.nik.trim())),
    [p.participant.fullName, p.participant.registrationNo, p.participant.nik],
  );

  const effectiveDeviceSources = useMemo(
    () =>
      p.acqSource === "live" && p.selected?.device_type === "ios"
        ? p.deviceSources.filter((source) => source !== "notes")
        : p.deviceSources,
    [p.acqSource, p.deviceSources, p.selected?.device_type],
  );

  const planReady = useMemo(
    () => analysisPlanReady(p.analysisScope, effectiveDeviceSources, p.socialTargets),
    [p.analysisScope, effectiveDeviceSources, p.socialTargets],
  );

  const iosSetup = useIosSetup({
    selected: p.selected,
    acqSource: p.acqSource,
    analysisScope: p.analysisScope,
    activeSession: !!(p.session && ACTIVE.has(p.session.status)),
  });

  const canStartLive = useMemo(
    () =>
      identityReady &&
      planReady &&
      p.acqSource === "live" &&
      !!p.selected &&
      !p.selected.simulated &&
      !busy &&
      !(p.session && ACTIVE.has(p.session.status)) &&
      iosSetup.readyForAcquire,
    [identityReady, planReady, p.acqSource, p.selected, busy, p.session, iosSetup.readyForAcquire],
  );

  const canStartZip = useMemo(
    () =>
      identityReady &&
      planReady &&
      p.acqSource === "zip" &&
      !!p.zipFile &&
      !busy &&
      !(p.session && ACTIVE.has(p.session.status)),
    [identityReady, planReady, p.acqSource, p.zipFile, busy, p.session],
  );

  const start = useCallback(async () => {
    if (!p.selected || p.selected.simulated || !identityReady || !planReady) return;
    p.setError(null);
    setBusy(true);
    try {
      const s = await api.startSession({
        device_id: p.selected.device_id,
        device_type: p.selected.device_type === "simulated" ? "android" : p.selected.device_type,
        mode: p.mode,
        analysis_scope: p.analysisScope,
        device_sources: effectiveDeviceSources,
        social_targets: p.socialTargets,
        scenario: "lulus",
        file_count: p.fileCount,
        participant: participantPayload(p.participant),
        force_simulated: false,
      });
      p.setSession(s);
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, s.id);
      } catch {
        /* ignore */
      }
      p.clearFindingsData();
      p.clearQueryPages();
      p.clearReportData();
      p.goToTab("operator", { sesi: s.id });
      void p.refreshSessionList();
      requestAnimationFrame(() => {
        p.teleRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      p.setError(e instanceof Error ? e.message : "Gagal memulai sesi");
    } finally {
      setBusy(false);
    }
  }, [p, identityReady, planReady]);

  const startZip = useCallback(async () => {
    if (!p.zipFile || !identityReady || !planReady) return;
    const maxBytes = p.zipMaxMb * 1024 * 1024;
    if (p.zipFile.size > maxBytes) {
      p.setError(
        `ZIP ${(p.zipFile.size / (1024 * 1024)).toFixed(1)} MB melebihi batas server ${p.zipMaxMb} MB`,
      );
      return;
    }
    p.setError(null);
    setBusy(true);
    setUploadPct(0);
    try {
      const s = await api.startSessionFromZip(p.zipFile, {
        mode: p.mode,
        analysis_scope: p.analysisScope,
        device_sources: effectiveDeviceSources,
        social_targets: p.socialTargets,
        participant: participantPayload(p.participant),
        onUploadProgress: (pct) => setUploadPct(pct),
      });
      p.setSession(s);
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, s.id);
      } catch {
        /* ignore */
      }
      p.clearFindingsData();
      p.clearQueryPages();
      p.clearReportData();
      p.goToTab("operator", { sesi: s.id });
      void p.refreshSessionList();
      requestAnimationFrame(() => {
        p.teleRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      p.setError(e instanceof Error ? e.message : "Gagal analisa ZIP");
    } finally {
      setBusy(false);
      setUploadPct(null);
    }
  }, [p, identityReady, planReady]);

  const cancel = useCallback(async () => {
    if (!p.session) return;
    setBusy(true);
    try {
      const s = await api.cancelSession(p.session.id);
      p.setSession(s);
      void p.refreshSessionList();
    } catch (e) {
      p.setError(e instanceof Error ? e.message : "Gagal membatalkan sesi");
    } finally {
      setBusy(false);
    }
  }, [p]);

  return { busy, uploadPct, canStartLive, canStartZip, start, startZip, cancel, iosSetup };
}
