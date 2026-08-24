import { useEffect, useState } from "react";

/** Thin top progress bar — YouTube/NProgress style. */
export function TopLoadingBar({ active }: { active: boolean }) {
  const [visible, setVisible] = useState(false);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    if (active) {
      setClosing(false);
      setVisible(true);
      return;
    }
    if (!visible) return;
    setClosing(true);
    const t = window.setTimeout(() => {
      setVisible(false);
      setClosing(false);
    }, 280);
    return () => window.clearTimeout(t);
  }, [active, visible]);

  if (!visible) return null;

  return (
    <div
      className={`top-loading-bar${closing ? " is-done" : " is-active"}`}
      role="progressbar"
      aria-busy={!closing}
      aria-valuetext={closing ? "Selesai" : "Memuat"}
    >
      <div className="top-loading-bar-peg" />
    </div>
  );
}
