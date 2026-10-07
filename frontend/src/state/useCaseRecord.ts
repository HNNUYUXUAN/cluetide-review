import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { api } from "../api";
import { CaseResource } from "./case-resource";

export function useCaseRecord(caseId: string | null) {
  const [resource] = useState(() => new CaseResource(api.get));
  const snapshot = useSyncExternalStore(resource.subscribe, resource.getSnapshot);
  useEffect(() => {
    void resource.select(caseId);
    return resource.cancelRead;
  }, [caseId, resource]);
  useEffect(() => {
    if (snapshot.caseId !== caseId || snapshot.status === "loading" ||
        !["running", "pending", "stopping"].includes(snapshot.investigation?.status ?? "")) return;
    const timer = setTimeout(() => void resource.refresh(caseId), snapshot.status === "error" ? 3000 : 1000);
    return () => clearTimeout(timer);
  }, [caseId, resource, snapshot]);
  const refresh = useCallback(() => resource.refresh(caseId), [caseId, resource]);
  const visible = snapshot.caseId === caseId ? snapshot : {
    caseId, status: caseId === null ? "idle" as const : "loading" as const,
    investigation: null, error: null,
  };
  return { ...visible, refresh };
}
