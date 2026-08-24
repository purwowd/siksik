import { useCallback, useRef, useState } from "react";
import { TOAST_MAX_VISIBLE, type ToastItem, type ToastTone } from "@/shared/ui/Toast";

export function useToastStack() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastTimers = useRef<Map<string, number>>(new Map());

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

  return { toasts, dismissToast, pushToast };
}

export type ToastPush = ReturnType<typeof useToastStack>["pushToast"];
