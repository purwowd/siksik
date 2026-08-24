export type ToastTone = "ok" | "warn" | "info";

export type ToastItem = {
  id: string;
  message: string;
  tone: ToastTone;
  action?: { label: string; onClick: () => void };
};

/** Max toast visible at once — older ones drop silently. */
export const TOAST_MAX_VISIBLE = 2;

export function ToastStack({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: string) => void }) {
  const visible = items.slice(-TOAST_MAX_VISIBLE);
  if (visible.length === 0) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite" aria-relevant="additions">
      {visible.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone} toast-in`}>
          <span>{t.message}</span>
          {t.action && (
            <button type="button" className="btn btn-ghost toast-action" onClick={t.action.onClick}>
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            className="toast-dismiss"
            onClick={() => onDismiss(t.id)}
            aria-label="Tutup"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
