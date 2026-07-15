import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { DEFAULT_PAGE_SIZE } from "./Pagination";
import {
  api,
  can,
  loadAuth,
  saveAuth,
  type AcquisitionMode,
  type AuthSession,
  type DashboardStats,
  type DeviceInfo,
  type Finding,
  type Paginated,
  type ReviewStatus,
  type SessionSummary,
  type VisionHealth,
} from "./api";
import { Breadcrumb } from "./components/Breadcrumb";
import { LoginScreen } from "./components/LoginScreen";
import { ToastStack, TOAST_MAX_VISIBLE, type ToastItem, type ToastTone } from "./components/Toast";
import { TopLoadingBar } from "./components/TopLoadingBar";
import { ACTIVE, preferredLandingTab, TAB_DEFS, TAB_PERMS } from "./constants";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
import { DashboardPage } from "./pages/DashboardPage";
import { FindingsPage } from "./pages/FindingsPage";
import { OperatorPage } from "./pages/OperatorPage";
import { ReportPage } from "./pages/ReportPage";
import {
  buildTabUrl,
  parseTabSearch,
  pathFromTab,
  resolveSessionId,
  tabFromPath,
  type ReviewFilterParam,
} from "./routes";
import type { Tab } from "./types";

const QUICK_VIDEO_CAP = 80;
const SESSION_STORAGE_KEY = "sadt_active_session_id";

type ReviewSummary = {
  pending: number;
  confirmed: number;
  rejected: number;
  total: number;
};

export default function App() {
  const [auth, setAuth] = useState<AuthSession | null>(() => loadAuth());
  const [loginUser, setLoginUser] = useState("operator");
  const [loginPass, setLoginPass] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [selected, setSelected] = useState<DeviceInfo | null>(null);
  const [mode, setMode] = useState<AcquisitionMode>("quick");
  const [fileCount] = useState(1200);
  const [acqSource, setAcqSource] = useState<"live" | "zip">("live");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [zipMaxMb, setZipMaxMb] = useState(512);
  const [zipEnabled, setZipEnabled] = useState(true);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [authorizeNote, setAuthorizeNote] = useState("");
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [sessionList, setSessionList] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [dash, setDash] = useState<DashboardStats | null>(null);
  const [dashLoading, setDashLoading] = useState(false);
  const [findingsData, setFindingsData] = useState<Paginated<Finding> | null>(null);
  const [findingsLoading, setFindingsLoading] = useState(false);
  const [findingsPage, setFindingsPage] = useState(1);
  const [reviewFilter, setReviewFilter] = useState<"all" | ReviewStatus>("all");
  const [reportFindings, setReportFindings] = useState<Paginated<Finding> | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportPage, setReportPage] = useState(1);
  const [reviewSummary, setReviewSummary] = useState<ReviewSummary | null>(null);
  const [dashSessions, setDashSessions] = useState<Paginated<SessionSummary> | null>(null);
  const [dashSessionsPage, setDashSessionsPage] = useState(1);
  const [dashFindings, setDashFindings] = useState<Paginated<Finding> | null>(null);
  const [dashFindingsPage, setDashFindingsPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [globalPending, setGlobalPending] = useState(0);
  const [gpu, setGpu] = useState(false);
  const [toolchain, setToolchain] = useState<Record<string, boolean>>({});
  const [vision, setVision] = useState<VisionHealth>({});
  const [imageCapQuick, setImageCapQuick] = useState(800);
  const [imageCapFull, setImageCapFull] = useState(3000);
  const [busy, setBusy] = useState(false);
  const [reviewBusyId, setReviewBusyId] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [expandedEvidence, setExpandedEvidence] = useState<string | null>(null);
  const [focusedFindingId, setFocusedFindingId] = useState<string | null>(null);
  const teleRef = useRef<HTMLElement | null>(null);
  const defaultSessionTried = useRef(false);
  const sessionIdRef = useRef<string | null>(null);
  const prevSessionStatusRef = useRef<string | null>(null);
  const completionToastIds = useRef<Set<string>>(new Set());
  const pollEpochRef = useRef(0);
  const toastTimers = useRef<Map<string, number>>(new Map());
  const intendedPathRef = useRef<string | null>(null);
  const urlFilterApplied = useRef(false);

  const tab = tabFromPath(location.pathname);

  const dismissToast = useCallback((id: string) => {
    const timer = toastTimers.current.get(id);
    if (timer) {
      window.clearTimeout(timer);
      toastTimers.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const pushToast = useCallback(
    (
      message: string,
      tone: ToastTone = "info",
      opts?: { action?: ToastItem["action"]; ttlMs?: number; dedupe?: boolean },
    ) => {
      const id = crypto.randomUUID();
      const ttl = opts?.ttlMs ?? 4500;
      setToasts((prev) => {
        let next = opts?.dedupe ? prev.filter((t) => t.message !== message) : prev;
        next = [...next, { id, message, tone, action: opts?.action }];
        if (next.length > TOAST_MAX_VISIBLE + 2) {
          next = next.slice(-(TOAST_MAX_VISIBLE + 2));
        }
        return next;
      });
      const timer = window.setTimeout(() => dismissToast(id), ttl);
      toastTimers.current.set(id, timer);
    },
    [dismissToast],
  );

  useEffect(() => {
    sessionIdRef.current = session?.id ?? null;
  }, [session?.id]);

  // Reset tracking saat ganti sesi (hindari toast beruntun & status macet)
  useEffect(() => {
    prevSessionStatusRef.current = null;
    pollEpochRef.current += 1;
  }, [session?.id]);

  useEffect(() => {
    if (!auth && location.pathname !== "/") {
      intendedPathRef.current = location.pathname + location.search;
    }
  }, [auth, location.pathname, location.search]);

  const allowedTabs = useMemo(
    () => TAB_DEFS.filter((t) => can(auth, TAB_PERMS[t.id])),
    [auth],
  );

  const landingTab = useMemo((): Tab => {
    if (!auth || allowedTabs.length === 0) return "operator";
    return preferredLandingTab(auth, allowedTabs) ?? allowedTabs[0].id;
  }, [auth, allowedTabs]);

  const goToTab = useCallback(
    (next: Tab, opts?: { sesi?: string | null; filter?: ReviewFilterParam | null }) => {
      navigate(
        buildTabUrl(next, {
          sesi: opts?.sesi ?? session?.id ?? null,
          filter: opts?.filter ?? (next === "findings" ? reviewFilter : null),
        }),
      );
    },
    [navigate, session?.id, reviewFilter],
  );

  useEffect(() => {
    if (!auth || allowedTabs.length === 0) return;
    if (location.pathname === "/") return;
    const current = tabFromPath(location.pathname);
    if (!current || !allowedTabs.some((t) => t.id === current)) {
      navigate(pathFromTab(landingTab), { replace: true });
    }
  }, [auth, allowedTabs, landingTab, location.pathname, navigate]);

  useEffect(() => {
    if (!auth) return;
    if (!urlFilterApplied.current) {
      if (can(auth, "findings:review")) setReviewFilter("pending");
      urlFilterApplied.current = true;
    }
    const { filter } = parseTabSearch(location.search);
    if (tab === "findings" && filter) setReviewFilter(filter);
  }, [auth, location.search, tab]);

  useEffect(() => {
    if (!auth || !tab) return;
    if (tab !== "findings" && tab !== "report" && tab !== "dashboard") return;
    const url = buildTabUrl(tab, {
      sesi: session?.id ?? null,
      filter: tab === "findings" ? reviewFilter : null,
    });
    const current = `${location.pathname}${location.search}`;
    if (current !== url) navigate(url, { replace: true });
  }, [auth, tab, session?.id, reviewFilter, location.pathname, location.search, navigate]);

  const refreshSessionList = useCallback(async (opts?: { soft?: boolean }) => {
    if (!opts?.soft) setSessionsLoading(true);
    try {
      const res = await api.sessions(1, 50);
      setSessionList(res.items);
      return res.items;
    } finally {
      if (!opts?.soft) setSessionsLoading(false);
    }
  }, []);

  const refreshReviewSummary = useCallback(async (sessionId: string) => {
    const [pending, confirmed, rejected] = await Promise.all([
      api.findings(sessionId, 1, 1, { review_status: "pending" }),
      api.findings(sessionId, 1, 1, { review_status: "confirmed" }),
      api.findings(sessionId, 1, 1, { review_status: "rejected" }),
    ]);
    setReviewSummary({
      pending: pending.total,
      confirmed: confirmed.total,
      rejected: rejected.total,
      total: pending.total + confirmed.total + rejected.total,
    });
  }, []);

  const refreshGlobalPending = useCallback(async () => {
    if (!auth || !can(auth, "findings:read")) return;
    try {
      const d = await api.dashboard(session?.id);
      setGlobalPending(d.pending_reviews ?? 0);
    } catch {
      /* optional */
    }
  }, [auth, session?.id]);

  const selectSessionById = useCallback(async (id: string) => {
    const s = await api.session(id);
    setSession(s);
    setFindingsPage(1);
    setReportPage(1);
    try {
      localStorage.setItem(SESSION_STORAGE_KEY, id);
    } catch {
      /* ignore */
    }
    void refreshReviewSummary(id);
    return s;
  }, [refreshReviewSummary]);

  async function refreshDevices() {
    const [h, d] = await Promise.all([api.health(), api.devices()]);
    setGpu(h.gpu_available);
    setToolchain(h.extras?.toolchain || {});
    setVision(h.extras?.vision || {});
    if (typeof h.extras?.image_cap_quick === "number") setImageCapQuick(h.extras.image_cap_quick);
    if (typeof h.extras?.image_cap_full === "number") setImageCapFull(h.extras.image_cap_full);
    if (typeof h.extras?.zip_max_mb === "number") setZipMaxMb(h.extras.zip_max_mb);
    if (typeof h.extras?.zip_enabled === "boolean") setZipEnabled(h.extras.zip_enabled);
    const live = d.filter((x) => !x.simulated);
    setDevices(live);
    setSelected((prev) => live.find((x) => x.device_id === prev?.device_id) ?? live[0] ?? null);
  }

  useEffect(() => {
    if (!auth) return;
    defaultSessionTried.current = false;
    void refreshGlobalPending();
    refreshDevices().catch((e) => {
      const msg = e instanceof Error ? e.message : "Gagal memuat API";
      setError(msg);
      if (String(msg).toLowerCase().includes("autentikasi")) {
        saveAuth(null);
        setAuth(null);
      }
    });
    refreshSessionList()
      .then(async (items) => {
        if (defaultSessionTried.current) return;
        defaultSessionTried.current = true;
        if (sessionIdRef.current) return;

        const { sesi } = parseTabSearch(location.search);
        const fromUrl = sesi ? resolveSessionId(sesi, items) : null;
        let preferId: string | null = fromUrl;
        if (!preferId) {
          try {
            preferId = localStorage.getItem(SESSION_STORAGE_KEY);
          } catch {
            preferId = null;
          }
        }
        const fromStorage = preferId ? items.find((s) => s.id === preferId) : null;
        const preferred =
          fromStorage ||
          items.find((s) => s.status === "completed") ||
          items.find((s) => s.recommendation) ||
          items[0];
        if (preferred) {
          try {
            await selectSessionById(preferred.id);
          } catch {
            /* ignore bootstrap */
          }
        }
      })
      .catch(() => {
        /* list optional on first paint */
      });
  }, [auth, refreshSessionList, selectSessionById, refreshGlobalPending, location.search]);

  useEffect(() => {
    if (!auth || sessionList.length === 0) return;
    const { sesi } = parseTabSearch(location.search);
    if (!sesi) return;
    const resolved = resolveSessionId(sesi, sessionList);
    if (resolved && resolved !== session?.id) {
      void selectSessionById(resolved).catch(() => {
        setError("Sesi dari URL tidak ditemukan");
      });
    }
  }, [auth, location.search, sessionList, session?.id, selectSessionById]);

  useEffect(() => {
    if (!session) {
      prevSessionStatusRef.current = null;
      return;
    }
    const prev = prevSessionStatusRef.current;
    prevSessionStatusRef.current = session.status;
    if (
      prev &&
      ACTIVE.has(prev) &&
      session.status === "completed" &&
      !completionToastIds.current.has(session.id)
    ) {
      completionToastIds.current.add(session.id);
      const n = session.progress?.findings_count ?? 0;
      if (n > 0) {
        pushToast(`Analisa selesai · ${n} temuan`, "info", {
          ttlMs: 6000,
          action: {
            label: "Buka review",
            onClick: () => goToTab("findings", { sesi: session.id, filter: "pending" }),
          },
        });
        if (can(auth, "findings:read")) {
          goToTab("findings", { sesi: session.id, filter: "pending" });
        }
      } else {
        pushToast("Analisa selesai · tidak ada temuan", "ok", { ttlMs: 4000 });
      }
      void refreshGlobalPending();
    }
  }, [session, auth, pushToast, goToTab, refreshGlobalPending]);

  useEffect(() => {
    if (!session || !ACTIVE.has(session.status)) return;
    const sessionId = session.id;
    const epoch = ++pollEpochRef.current;
    let stopped = false;
    let inFlight = false;

    const applyPolled = (s: SessionSummary) => {
      setSession((curr) => {
        if (!curr || curr.id !== s.id) return curr;
        if (TERMINAL.has(curr.status) && ACTIVE.has(s.status)) return curr;
        return s;
      });
    };

    const tick = async () => {
      if (stopped || pollEpochRef.current !== epoch || inFlight) return;
      inFlight = true;
      try {
        const s = await api.session(sessionId);
        if (stopped || pollEpochRef.current !== epoch) return;
        applyPolled(s);
        if (!ACTIVE.has(s.status)) {
          stopped = true;
          const f = await api.findings(
            s.id,
            1,
            DEFAULT_PAGE_SIZE,
            reviewFilter === "all" ? undefined : { review_status: reviewFilter },
          );
          if (pollEpochRef.current !== epoch) return;
          setFindingsData(f);
          setFindingsPage(1);
          void refreshSessionList({ soft: true });
          void refreshReviewSummary(s.id);
          void refreshGlobalPending();
        }
      } catch {
        if (!stopped) {
          setError("Koneksi telemetri terputus — coba muat ulang atau pilih sesi ulang");
        }
      } finally {
        inFlight = false;
      }
    };

    const t = window.setInterval(() => void tick(), 500);
    return () => {
      stopped = true;
      window.clearInterval(t);
      if (pollEpochRef.current === epoch) {
        pollEpochRef.current += 1;
      }
    };
  }, [session?.id, session?.status, reviewFilter, refreshSessionList, refreshReviewSummary, refreshGlobalPending]);

  useEffect(() => {
    if (tab !== "dashboard") return;
    let cancelled = false;
    setDashLoading(true);
    Promise.all([
      api.dashboard(session?.id),
      api.sessions(dashSessionsPage, DEFAULT_PAGE_SIZE),
      api.findings(undefined, dashFindingsPage, DEFAULT_PAGE_SIZE),
    ])
      .then(([d, sessionsRes, findingsRes]) => {
        if (cancelled) return;
        setDash(d);
        setDashSessions(sessionsRes);
        setDashFindings(findingsRes);
        setGlobalPending(d.pending_reviews ?? 0);
        setSessionList((prev) => {
          const map = new Map(prev.map((s) => [s.id, s]));
          for (const s of sessionsRes.items) map.set(s.id, s);
          return Array.from(map.values());
        });
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e));
      })
      .finally(() => {
        if (!cancelled) setDashLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, dashSessionsPage, dashFindingsPage, session?.id]);

  useEffect(() => {
    if (tab !== "dashboard") return;
    void refreshSessionList({ soft: true });
  }, [tab, refreshSessionList]);

  useEffect(() => {
    if (tab !== "findings" && tab !== "report") return;
    if (sessionList.length === 0) void refreshSessionList();
  }, [tab, sessionList.length, refreshSessionList]);

  useEffect(() => {
    if (!session?.id) {
      setReviewSummary(null);
      return;
    }
    void refreshReviewSummary(session.id);
  }, [session?.id, session?.recommendation, refreshReviewSummary]);

  useEffect(() => {
    setReportPage((p) => (p === 1 ? p : 1));
  }, [session?.id]);

  useEffect(() => {
    if (tab !== "findings") return;
    if (!session?.id) {
      setFindingsData(null);
      setFindingsLoading(false);
      return;
    }
    let cancelled = false;
    setFindingsLoading(true);

    api
      .findings(
        session.id,
        findingsPage,
        DEFAULT_PAGE_SIZE,
        reviewFilter === "all" ? undefined : { review_status: reviewFilter },
      )
      .then((data) => {
        if (cancelled) return;
        setFindingsData(data);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e));
      })
      .finally(() => {
        if (!cancelled) setFindingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, session?.id, findingsPage, reviewFilter]);

  // Ganti sesi → buang list lama supaya tidak flash data sesi lain
  useEffect(() => {
    setFindingsData(null);
    setReportFindings(null);
  }, [session?.id]);

  useEffect(() => {
    if (tab !== "report" || !session?.id) return;
    let cancelled = false;
    setReportLoading(true);

    api
      .findings(session.id, reportPage, DEFAULT_PAGE_SIZE)
      .then((data) => {
        if (cancelled) return;
        setReportFindings(data);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e));
      })
      .finally(() => {
        if (!cancelled) setReportLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, session?.id, reportPage]);

  const liveDevices = devices.filter((d) => !d.simulated);
  const canStartLive = useMemo(
    () =>
      acqSource === "live" &&
      !!selected &&
      !selected.simulated &&
      !busy &&
      !(session && ACTIVE.has(session.status)),
    [acqSource, selected, busy, session],
  );
  const canStartZip = useMemo(
    () =>
      acqSource === "zip" &&
      !!zipFile &&
      !busy &&
      !(session && ACTIVE.has(session.status)),
    [acqSource, zipFile, busy, session],
  );

  const modeHint =
    mode === "quick"
      ? `≤${imageCapQuick} foto galeri · ≤${QUICK_VIDEO_CAP} video · OCR selektif (screenshot/dokumen/edge)`
      : `≤${imageCapFull} foto · semua video · OCR penuh gallery/documents (jika engine ada) · lebih lambat`;

  const mediaTextOn = !!vision.media_text?.enabled;
  const ocrEngineOn = !!(vision.ocr?.enabled && vision.ocr?.available);
  const whisperOn = !!(vision.media_text?.whisper || vision.gpu_stack?.backends?.whisper?.available);
  const gpuStackOn = !!vision.gpu_stack?.enabled;

  const changeReviewFilter = useCallback((next: "all" | ReviewStatus) => {
    setReviewFilter(next);
    setFindingsPage(1);
  }, []);

  async function onPickSession(id: string) {
    try {
      setError(null);
      await selectSessionById(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memuat sesi");
    }
  }

  async function openSession(id: string, nextTab: Tab) {
    try {
      setError(null);
      await selectSessionById(id);
      goToTab(nextTab, { sesi: id, filter: nextTab === "findings" ? reviewFilter : null });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memuat sesi");
    }
  }

  async function start() {
    if (!selected || selected.simulated) return;
    setError(null);
    setBusy(true);
    try {
      const s = await api.startSession({
        device_id: selected.device_id,
        device_type: selected.device_type === "simulated" ? "android" : selected.device_type,
        mode,
        scenario: "lulus",
        file_count: fileCount,
        label: selected.label,
        force_simulated: false,
      });
      setSession(s);
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, s.id);
      } catch {
        /* ignore */
      }
      setFindingsData(null);
      setFindingsPage(1);
      setReportFindings(null);
      setReportPage(1);
      goToTab("operator", { sesi: s.id });
      void refreshSessionList();
      requestAnimationFrame(() => {
        teleRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memulai sesi");
    } finally {
      setBusy(false);
    }
  }

  async function startZip() {
    if (!zipFile) return;
    const maxBytes = zipMaxMb * 1024 * 1024;
    if (zipFile.size > maxBytes) {
      setError(
        `ZIP ${(zipFile.size / (1024 * 1024)).toFixed(1)} MB melebihi batas server ${zipMaxMb} MB`,
      );
      return;
    }
    setError(null);
    setBusy(true);
    setUploadPct(0);
    try {
      const s = await api.startSessionFromZip(zipFile, {
        mode,
        label: `ZIP · ${zipFile.name}`,
        onUploadProgress: (pct) => setUploadPct(pct),
      });
      setSession(s);
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, s.id);
      } catch {
        /* ignore */
      }
      setFindingsData(null);
      setFindingsPage(1);
      setReportFindings(null);
      setReportPage(1);
      goToTab("operator", { sesi: s.id });
      void refreshSessionList();
      requestAnimationFrame(() => {
        teleRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal analisa ZIP");
    } finally {
      setBusy(false);
      setUploadPct(null);
    }
  }

  async function cancel() {
    if (!session) return;
    setBusy(true);
    try {
      setSession(await api.cancelSession(session.id));
      void refreshSessionList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal membatalkan");
    } finally {
      setBusy(false);
    }
  }

  async function review(id: string, review_status: "confirmed" | "rejected") {
    if (reviewBusyId || bulkBusy) return;
    setReviewBusyId(id);
    try {
      await api.reviewFinding(id, review_status);
      const patch = (prev: Paginated<Finding> | null) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((f) => (f.id === id ? { ...f, review_status } : f)),
            }
          : prev;
      if (reviewFilter === "pending") {
        setFindingsData((prev) =>
          prev
            ? {
                ...prev,
                items: prev.items.filter((f) => f.id !== id),
                total: Math.max(0, prev.total - 1),
              }
            : prev,
        );
      } else {
        setFindingsData(patch);
      }
      setReportFindings(patch);
      setDashFindings(patch);
      if (session?.id) {
        const refreshed = await api.session(session.id);
        setSession(refreshed);
        void refreshReviewSummary(session.id);
        void refreshSessionList({ soft: true });
        void refreshGlobalPending();
        pushToast(
          review_status === "confirmed" ? "Temuan dikonfirmasi" : "Temuan ditolak",
          review_status === "confirmed" ? "warn" : "ok",
          { ttlMs: 2200, dedupe: true },
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal menyimpan verifikasi");
    } finally {
      setReviewBusyId(null);
    }
  }

  async function bulkReview(review_status: "confirmed" | "rejected") {
    if (!session?.id || !reviewSummary?.pending) return;
    const verb = review_status === "confirmed" ? "konfirmasi" : "tolak";
    if (!window.confirm(`Yakin ingin ${verb} semua ${reviewSummary.pending} temuan pending?`)) {
      return;
    }
    setBulkBusy(true);
    try {
      const pending = await api.findings(session.id, 1, 500, { review_status: "pending" });
      for (const f of pending.items) {
        await api.reviewFinding(f.id, review_status);
      }
      const refreshed = await api.session(session.id);
      setSession(refreshed);
      setFindingsPage(1);
      void refreshReviewSummary(session.id);
      void refreshSessionList({ soft: true });
      void refreshGlobalPending();
      pushToast(`${pending.items.length} temuan di-${verb}`, review_status === "confirmed" ? "warn" : "ok", {
        ttlMs: 4000,
        dedupe: true,
      });
      if (tab === "findings") {
        const data = await api.findings(
          session.id,
          1,
          DEFAULT_PAGE_SIZE,
          reviewFilter === "all" ? undefined : { review_status: reviewFilter },
        );
        setFindingsData(data);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal bulk review");
    } finally {
      setBulkBusy(false);
    }
  }

  async function doLogin(e?: FormEvent) {
    e?.preventDefault();
    setLoginBusy(true);
    setError(null);
    try {
      const sessionAuth = await api.login(loginUser, loginPass);
      saveAuth(sessionAuth);
      setAuth(sessionAuth);
      urlFilterApplied.current = false;
      const dest = intendedPathRef.current;
      intendedPathRef.current = null;
      if (dest) {
        navigate(dest, { replace: true });
      } else {
        const allowed = TAB_DEFS.filter((t) => can(sessionAuth, TAB_PERMS[t.id]));
        const land = preferredLandingTab(sessionAuth, allowed) ?? allowed[0]?.id ?? "operator";
        navigate(pathFromTab(land));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login gagal");
    } finally {
      setLoginBusy(false);
    }
  }

  async function doLogout() {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    saveAuth(null);
    setAuth(null);
    setSession(null);
    setFindingsData(null);
    setReportFindings(null);
    setReviewSummary(null);
    setError(null);
    navigate("/");
  }

  if (!auth) {
    return (
      <>
        <TopLoadingBar active={loginBusy} />
        <LoginScreen
          loginUser={loginUser}
          loginPass={loginPass}
          loginBusy={loginBusy}
          error={error}
          onUserChange={setLoginUser}
          onPassChange={setLoginPass}
          onPickDemo={(user, pass) => {
            setLoginUser(user);
            setLoginPass(pass);
          }}
          onSubmit={doLogin}
        />
      </>
    );
  }

  const topBarActive =
    findingsLoading ||
    reportLoading ||
    dashLoading ||
    busy ||
    bulkBusy ||
    !!reviewBusyId ||
    (sessionsLoading && sessionList.length === 0) ||
    (!!session && ACTIVE.has(session.status));

  return (
    <div className="app-shell wide">
      <TopLoadingBar active={topBarActive} />
      <div className="classify-rail slim">
        <span>Internal · gallery focus</span>
        <span>PoC</span>
      </div>

      <header className="ops-topbar">
        <div className="ops-brand">
          <p className="brand-kicker">Sistem Analisis Digital Terpadu</p>
          <strong>SADT // OPS</strong>
        </div>
        <div className="user-chip compact">
          <div>
            <strong>{auth.display_name}</strong>
            <span>
              {auth.username} · {auth.role}
            </span>
          </div>
          <button className="btn btn-ghost" type="button" onClick={() => void doLogout()}>
            Keluar
          </button>
        </div>
      </header>

      <div className="nav-context">
        <nav className="tabs" role="tablist" aria-label="Navigasi konsol">
          {allowedTabs.map((t) => (
            <NavLink
              key={t.id}
              to={buildTabUrl(t.id, {
                sesi: session?.id,
                filter: t.id === "findings" ? reviewFilter : null,
              })}
              role="tab"
              aria-selected={tab === t.id}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {t.label}
              {t.id === "findings" && globalPending > 0 && (
                <span className="tab-badge" aria-label={`${globalPending} temuan pending`}>
                  {globalPending}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <Breadcrumb pathname={location.pathname} session={session} />
      </div>

      {error && (
        <div className="error-banner dismissible" role="alert">
          <span>{error}</span>
          <button
            type="button"
            className="error-dismiss"
            onClick={() => setError(null)}
            aria-label="Tutup"
          >
            Tutup
          </button>
        </div>
      )}

      <ToastStack items={toasts} onDismiss={dismissToast} />

      <Routes>
        <Route path="/" element={<Navigate to={pathFromTab(landingTab)} replace />} />

        {can(auth, "sessions:start") && (
          <Route
            path="/operator"
            element={
              <OperatorPage
                teleRef={teleRef}
                acqSource={acqSource}
                setAcqSource={setAcqSource}
                zipEnabled={zipEnabled}
                zipFile={zipFile}
                setZipFile={setZipFile}
                zipMaxMb={zipMaxMb}
                uploadPct={uploadPct}
                liveDevices={liveDevices}
                selected={selected}
                setSelected={setSelected}
                refreshDevices={refreshDevices}
                mode={mode}
                setMode={setMode}
                modeHint={modeHint}
                canStartLive={canStartLive}
                canStartZip={canStartZip}
                busy={busy}
                session={session}
                start={() => void start()}
                startZip={() => void startZip()}
                cancel={() => void cancel()}
                onNavigateTab={goToTab}
                canDashboard={can(auth, "dashboard")}
              />
            }
          />
        )}

        {can(auth, "dashboard") && (
          <Route
            path="/dasbor"
            element={
              <DashboardPage
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                onPickSession={(id) => void onPickSession(id)}
                dash={dash}
                dashSessions={dashSessions}
                dashFindings={dashFindings}
                setDashSessionsPage={setDashSessionsPage}
                setDashFindingsPage={setDashFindingsPage}
                openSession={(id, t) => void openSession(id, t)}
              />
            }
          />
        )}

        {can(auth, "findings:read") && (
          <Route
            path="/temuan"
            element={
              <FindingsPage
                auth={auth}
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                findingsLoading={findingsLoading}
                reviewSummary={reviewSummary}
                onPickSession={(id) => void onPickSession(id)}
                refreshSessionList={() => void refreshSessionList()}
                reviewFilter={reviewFilter}
                setReviewFilter={changeReviewFilter}
                findingsData={findingsData}
                expandedEvidence={expandedEvidence}
                setExpandedEvidence={setExpandedEvidence}
                reviewBusyId={reviewBusyId}
                bulkBusy={bulkBusy}
                onReview={(id, st) => void review(id, st)}
                onBulkReview={(st) => void bulkReview(st)}
                onPage={setFindingsPage}
                focusedFindingId={focusedFindingId}
                setFocusedFindingId={setFocusedFindingId}
              />
            }
          />
        )}

        {can(auth, "report:read") && (
          <Route
            path="/laporan"
            element={
              <ReportPage
                auth={auth}
                session={session}
                sessionList={sessionList}
                sessionsLoading={sessionsLoading && sessionList.length === 0}
                onPickSession={(id) => void onPickSession(id)}
                reportFindings={reportFindings}
                reportLoading={reportLoading}
                reviewSummary={reviewSummary}
                setReportPage={setReportPage}
                authorizeNote={authorizeNote}
                setAuthorizeNote={setAuthorizeNote}
                setSession={setSession}
                refreshSessionList={() => void refreshSessionList()}
                setError={setError}
                onToast={(msg, tone) => pushToast(msg, tone ?? "ok", { ttlMs: 4000, dedupe: true })}
              />
            }
          />
        )}

        <Route path="*" element={<Navigate to={pathFromTab(landingTab)} replace />} />
      </Routes>

      <footer className="ops-footer">
        <div className="ops-footer-left">
          <span className={`badge thin ${gpu ? "armed" : ""}`}>
            <span className="dot" />
            {gpu ? "GPU" : "CPU"}
          </span>
          <div className="tool-pills">
            <span className={`pill ${toolchain.adb ? "ok" : "muted"}`}>
              ADB {toolchain.adb ? "OK" : "N/A"}
            </span>
            <span className={`pill ${toolchain.idevice_id ? "ok" : "muted"}`}>
              iOS {toolchain.idevice_id ? "OK" : "N/A"}
            </span>
            <span className={`pill ${vision.pillow ? "ok" : "muted"}`}>
              CV {vision.pillow ? "OK" : "N/A"}
            </span>
            <span className={`pill ${mediaTextOn ? "ok" : "muted"}`}>
              Media-teks {mediaTextOn ? "ON" : "OFF"}
            </span>
            <span className={`pill ${ocrEngineOn ? "ok" : "muted"}`}>
              OCR {ocrEngineOn ? "ON" : "OFF"}
            </span>
            <span className={`pill ${whisperOn ? "ok" : "muted"}`}>
              Whisper {whisperOn ? "ON" : "OFF"}
            </span>
            <span className={`pill ${gpuStackOn ? "ok" : "muted"}`}>
              GPU-stack {gpuStackOn ? "ON" : "OFF"}
            </span>
          </div>
        </div>
        <span className="ops-footer-right">SADT · fokus galeri · PoC</span>
      </footer>
    </div>
  );
}
