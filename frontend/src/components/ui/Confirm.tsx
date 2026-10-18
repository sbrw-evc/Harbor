import { create } from "zustand";
import { AlertTriangle } from "lucide-react";
import { t } from "@/i18n";
import { useFocus } from "@/lib/hooks";
import { Modal } from "./Primitives";

interface ConfirmRequest {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  resolve: (ok: boolean) => void;
}

const useConfirmStore = create<{ req: ConfirmRequest | null; set: (r: ConfirmRequest | null) => void }>((set) => ({
  req: null,
  set: (req) => set({ req }),
}));

export function confirm(opts: Omit<ConfirmRequest, "resolve">): Promise<boolean> {
  return new Promise((resolve) => useConfirmStore.getState().set({ ...opts, resolve }));
}

export function ConfirmHost() {
  const { req, set } = useConfirmStore();
  const close = (ok: boolean) => { req?.resolve(ok); set(null); };
  const confirm = useFocus<HTMLButtonElement>(req);
  return (
    <Modal open={!!req} onClose={() => close(false)} title={req?.title ?? ""}>
      <div className="flex gap-3">
        {req?.danger && <AlertTriangle size={20} className="mt-0.5 shrink-0 text-red-300" />}
        <p className="select-text whitespace-pre-line text-sm leading-relaxed text-slate-300">{req?.message}</p>
      </div>
      <div className="mt-6 flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={() => close(false)}>{t("Cancel")}</button>
        <button type="button" className={req?.danger ? "btn-danger" : "btn-primary"} onClick={() => close(true)} ref={confirm}>
          {req?.confirmLabel ?? t("Confirm")}
        </button>
      </div>
    </Modal>
  );
}
