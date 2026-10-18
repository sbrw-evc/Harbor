import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { notify } from "./notify";
import { t } from "@/i18n";

export function useAction<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>, opts: {
  success?: string | ((r: TResult) => string);
  invalidate?: QueryKey[];
  onSuccess?: (r: TResult) => void;
} = {}) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      if (opts.success) notify.success(typeof opts.success === "function" ? opts.success(r) : opts.success);
      opts.invalidate?.forEach((k) => qc.invalidateQueries({ queryKey: k }));
      opts.onSuccess?.(r);
    },
    onError: (e: Error) => notify.error(t("Action failed"), e.message),
  });
}

export async function copyText(text: string, label = t("Copied")) {
  try {
    await navigator.clipboard.writeText(text);
    notify.success(label);
  } catch {
    notify.error(t("Clipboard is not available"), t("HTTPS or localhost is required"));
  }
}

/** Puts the focus on the element when it appears, and again whenever `when` changes to a truthy value: the input a
 * dialog or a form step is about. */
export function useFocus<T extends HTMLElement>(when: unknown = true) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (when) ref.current?.focus();
  }, [when]);
  return ref;
}

/** Hands the user a text file to save (the browser's download). */
export function saveTextFile(name: string, text: string, type = "text/plain;charset=utf-8") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
