import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type ToastKind = "error" | "ok" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  msg: string;
}
interface ToastCtx {
  push: (msg: string, kind?: ToastKind) => void;
  error: (e: unknown) => void;
}

const Ctx = createContext<ToastCtx>({ push: () => {}, error: () => {} });
let seq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((msg: string, kind: ToastKind = "info") => {
    const id = ++seq;
    setToasts((t) => {
      // collapse duplicates
      if (t.some((x) => x.msg === msg && x.kind === kind)) return t;
      return [...t.slice(-4), { id, kind, msg }];
    });
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 3500);
  }, []);
  const error = useCallback(
    (e: unknown) => push(e instanceof Error ? e.message : String(e), "error"),
    [push],
  );
  return (
    <Ctx.Provider value={{ push, error }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast toast-${t.kind}`}
            onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}
          >
            {t.msg}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
