import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { useStore } from "../store";

export function Toasts() {
  const { toasts, dismiss } = useStore();
  return (
    <div className="toasts" aria-live="assertive">
      {toasts.map((t) => {
        const Icon = t.kind === "error" ? CircleAlert : t.kind === "success" ? CircleCheck : Info;
        return (
          <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
            <Icon size={16} /> <span>{t.message}</span>
            <button className="icon-btn" aria-label="Dismiss" onClick={() => dismiss(t.id)}><X size={14} /></button>
          </div>
        );
      })}
    </div>
  );
}
