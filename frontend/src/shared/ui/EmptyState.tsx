type Props = {
  title: string;
  body: string;
  hint?: string;
  tone?: "default" | "ok" | "warn";
};

export function EmptyState({ title, body, hint, tone = "default" }: Props) {
  return (
    <div className={`ent-empty tone-${tone}`} role="status">
      <div className="ent-empty-mark" aria-hidden />
      <h3>{title}</h3>
      <p>{body}</p>
      {hint && <p className="ent-empty-hint">{hint}</p>}
    </div>
  );
}
