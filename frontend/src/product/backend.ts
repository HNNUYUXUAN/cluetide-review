import { useEffect, useState } from "react";
import { isLocalHost } from "./routes";
export type Backend = "checking" | "available" | "unavailable" | "static";

export function useBackend(): { backend: Backend; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [backend, setBackend] = useState<Backend>(() => isLocalHost(location.hostname) ? "checking" : "static");
  useEffect(() => {
    if (!isLocalHost(location.hostname)) return;
    const controller = new AbortController();
    setBackend("checking");
    void fetch("/api/health", { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]) })
      .then(async response => {
        if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) throw new Error("unavailable");
        const data = await response.json();
        if (!data || typeof data !== "object" || !("status" in data)) throw new Error("unavailable");
        if (!controller.signal.aborted) setBackend("available");
      }).catch(() => { if (!controller.signal.aborted) setBackend("unavailable"); });
    return () => controller.abort();
  }, [attempt]);
  return { backend, retry: () => setAttempt(value => value + 1) };
}
