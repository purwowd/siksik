import { PIPELINE } from "../constants";

function pipelineStep(status?: string | null): number {
  if (!status) return -1;
  if (status === "failed" || status === "cancelled") return -1;
  const idx = PIPELINE.findIndex((s) => (s.match as readonly string[]).includes(status));
  if (idx >= 0) return idx;
  if (status === "completed") return PIPELINE.length - 1;
  return -1;
}

export function PipelineTrack({ status }: { status?: string | null }) {
  const active = pipelineStep(status);
  const failed = status === "failed" || status === "cancelled";
  return (
    <div className="pipeline" aria-label="Pipeline akuisisi">
      {PIPELINE.map((step, i) => {
        let state = "idle";
        if (failed) state = i <= Math.max(active, 0) ? "fail" : "idle";
        else if (active < 0) state = "idle";
        else if (i < active) state = "done";
        else if (i === active) state = status === "completed" ? "done" : "live";
        return (
          <div key={step.id} className={`pipeline-step ${state}`}>
            <span className="pipeline-node" />
            <span className="pipeline-label">{step.label}</span>
            {i < PIPELINE.length - 1 && <span className="pipeline-wire" />}
          </div>
        );
      })}
    </div>
  );
}
