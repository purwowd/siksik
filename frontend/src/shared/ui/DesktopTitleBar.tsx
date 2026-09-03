import { useCallback, useEffect, useRef, useState } from "react";

function isTauriShell(): boolean {
  return typeof window !== "undefined" && ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);
}

const SHOW_EDGE_PX = 6;
const HIDE_DELAY_MS = 450;

/** Autohide title bar for borderless Tauri window — appear when cursor hits top edge. */
export function DesktopTitleBar() {
  const [active, setActive] = useState(false);
  const [visible, setVisible] = useState(false);
  const [maximized, setMaximized] = useState(true);
  const hideTimer = useRef<number | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);

  const clearHide = useCallback(() => {
    if (hideTimer.current != null) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  }, []);

  const scheduleHide = useCallback(() => {
    clearHide();
    hideTimer.current = window.setTimeout(() => setVisible(false), HIDE_DELAY_MS);
  }, [clearHide]);

  const showBar = useCallback(() => {
    clearHide();
    setVisible(true);
  }, [clearHide]);

  useEffect(() => {
    if (!isTauriShell()) return;
    setActive(true);
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void (async () => {
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        if (cancelled) return;
        const win = getCurrentWindow();
        setMaximized(await win.isMaximized());
        unlisten = await win.onResized(async () => {
          try {
            setMaximized(await win.isMaximized());
          } catch {
            /* ignore */
          }
        });
      } catch {
        /* not in Tauri runtime */
      }
    })();
    return () => {
      cancelled = true;
      clearHide();
      unlisten?.();
    };
  }, [clearHide]);

  useEffect(() => {
    if (!active) return;
    const onMove = (e: MouseEvent) => {
      if (e.clientY <= SHOW_EDGE_PX) {
        showBar();
        return;
      }
      const bar = barRef.current;
      if (bar && e.clientY <= bar.getBoundingClientRect().bottom) {
        showBar();
        return;
      }
      if (visible) scheduleHide();
    };
    window.addEventListener("mousemove", onMove, { passive: true });
    return () => window.removeEventListener("mousemove", onMove);
  }, [active, visible, showBar, scheduleHide]);

  const runWindow = useCallback(async (action: "minimize" | "toggleMaximize" | "close") => {
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const win = getCurrentWindow();
      if (action === "minimize") await win.minimize();
      else if (action === "toggleMaximize") {
        await win.toggleMaximize();
        setMaximized(await win.isMaximized());
      } else await win.close();
    } catch {
      /* window API unavailable */
    }
  }, []);

  if (!active) return null;

  return (
    <>
      <div className="satria-titlebar-edge" aria-hidden />
      <div
        ref={barRef}
        className={`satria-titlebar${visible ? " is-visible" : ""}`}
        onMouseEnter={showBar}
        onMouseLeave={scheduleHide}
        role="banner"
      >
        <div className="satria-titlebar-drag" data-tauri-drag-region>
          <span className="satria-titlebar-title" data-tauri-drag-region>
            SATRIA - Sistem Analisis Terpadu
          </span>
        </div>
        <div className="satria-titlebar-controls">
          <button
            type="button"
            className="satria-titlebar-btn"
            aria-label="Minimize"
            onClick={() => void runWindow("minimize")}
          >
            <span aria-hidden>—</span>
          </button>
          <button
            type="button"
            className="satria-titlebar-btn"
            aria-label={maximized ? "Restore" : "Maximize"}
            onClick={() => void runWindow("toggleMaximize")}
          >
            <span aria-hidden>{maximized ? "❐" : "□"}</span>
          </button>
          <button
            type="button"
            className="satria-titlebar-btn satria-titlebar-btn--close"
            aria-label="Tutup"
            onClick={() => void runWindow("close")}
          >
            <span aria-hidden>×</span>
          </button>
        </div>
      </div>
    </>
  );
}
