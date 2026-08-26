import type { ReactNode } from "react";
import { PanelTitle } from "@/shared/ui/PanelTitle";
import type { FeaturePageMeta } from "@/shared/lib/featurePages";

type Props = {
  meta: FeaturePageMeta;
  threat?: boolean;
  className?: string;
  panelRef?: React.RefObject<HTMLElement | null>;
  children: ReactNode;
};

/** Panel dalam halaman (mis. operator: intake + telemetri). */
export function FeaturePanel({ meta, threat, className, panelRef, children }: Props) {
  return (
    <section
      ref={panelRef}
      className={`panel ent-panel${threat ? " threat" : ""}${className ? ` ${className}` : ""}`}
    >
      <div className="ent-panel-head">
        <PanelTitle title={meta.title} />
        {meta.copy ? <p className="ent-panel-copy">{meta.copy}</p> : null}
      </div>
      {children}
    </section>
  );
}
