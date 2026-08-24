import { WORKFLOW_STEPS, workflowStepStates, type WorkflowSessionLike } from "@/shared/lib/workflow";

export function PipelineTrack({
  status,
  session,
}: {
  status?: string | null;
  session?: WorkflowSessionLike | null;
}) {
  const like: WorkflowSessionLike = session ?? { status };
  const states = workflowStepStates(like);

  return (
    <div className="pipeline" aria-label="Alur kerja SATRIA">
      {WORKFLOW_STEPS.map((step, i) => (
        <div key={step.id} className={`pipeline-step ${states[i]}`}>
          <span className="pipeline-node" />
          <span className="pipeline-label">{step.label}</span>
          {i < WORKFLOW_STEPS.length - 1 && <span className="pipeline-wire" />}
        </div>
      ))}
    </div>
  );
}
