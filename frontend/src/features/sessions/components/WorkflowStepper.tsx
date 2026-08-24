import { WORKFLOW_STEPS, workflowStepStates, type WorkflowSessionLike } from "@/shared/lib/workflow";

export function WorkflowStepper({ session }: { session: WorkflowSessionLike | null }) {
  const states = workflowStepStates(session);
  return (
    <ol className="satria-stepper" aria-label="Alur kerja lima tahap SATRIA">
      {WORKFLOW_STEPS.map((step, i) => (
        <li key={step.id} className={`satria-step ${states[i]}`}>
          <span className="satria-step-num" aria-hidden>
            {step.num}
          </span>
          <span className="satria-step-label">{step.label}</span>
          {i < WORKFLOW_STEPS.length - 1 && <span className="satria-step-wire" aria-hidden />}
        </li>
      ))}
    </ol>
  );
}
