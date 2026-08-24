import { NavLink } from "react-router-dom";
import type { SessionSummary } from "@/shared/api/client";
import { pathFromTab, tabFromPath } from "@/app/routes";
import type { Tab } from "@/shared/types";

const TAB_LABELS: Record<Tab, string> = {
  operator: "Pengambilan Data",
  findings: "Temuan",
  gallery: "Galeri",
  report: "Hasil Akhir",
  dashboard: "Dasbor Petugas",
};

export function Breadcrumb({ pathname, session }: { pathname: string; session: SessionSummary | null }) {
  const tab = tabFromPath(pathname);
  if (!tab) return null;

  const crumbs: { label: string; to?: string }[] = [{ label: "SATRIA", to: pathFromTab(tab) }];
  crumbs.push({ label: TAB_LABELS[tab] });

  if (session && (tab === "findings" || tab === "gallery" || tab === "report" || tab === "dashboard")) {
    crumbs.push({
      label: `${
        session.participant?.full_name
          ? `${session.participant.full_name}${
              session.participant.registration_no
                ? ` · ${session.participant.registration_no}`
                : ""
            }`
          : session.label || session.device_id
      } (${session.id.slice(0, 8)})`,
    });
  }

  return (
    <nav className="breadcrumb" aria-label="Lokasi">
      {crumbs.map((c, i) => (
        <span key={`${c.label}-${i}`} className="breadcrumb-seg">
          {i > 0 && <span className="breadcrumb-sep">/</span>}
          {c.to && i < crumbs.length - 1 ? (
            <NavLink to={c.to}>{c.label}</NavLink>
          ) : (
            <span className={i === crumbs.length - 1 ? "breadcrumb-current" : undefined}>{c.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
