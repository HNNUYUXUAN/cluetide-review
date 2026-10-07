import type { Investigation } from "../types";
import { isCaseId } from "../routing/routes.ts";

export type CaseSnapshot = Readonly<{
  caseId: string | null;
  status: "idle" | "loading" | "ready" | "error";
  investigation: Investigation | null;
  error: Error | null;
}>;
export type CaseLoader = (id: string, signal: AbortSignal) => Promise<Investigation>;

export class CaseResource {
  private snapshot: CaseSnapshot = { caseId: null, status: "idle", investigation: null, error: null };
  private readonly listeners = new Set<() => void>();
  private readonly loader: CaseLoader;
  private controller: AbortController | null = null;
  private task: Promise<void> | null = null;
  private generation = 0;

  constructor(loader: CaseLoader) {
    this.loader = loader;
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private publish(snapshot: CaseSnapshot) {
    this.snapshot = snapshot;
    this.listeners.forEach((listener) => listener());
  }
  cancelRead = () => {
    this.generation += 1;
    this.controller?.abort();
    this.controller = null;
    this.task = null;
  };
  select = (caseId: string | null): Promise<void> => {
    if (caseId === this.snapshot.caseId) {
      if (this.task) return this.task;
      if (this.snapshot.status === "ready" || this.snapshot.status === "idle") return Promise.resolve();
    }
    return this.read(caseId);
  };
  refresh = (caseId: string | null = this.snapshot.caseId): Promise<void> => {
    if (caseId !== this.snapshot.caseId) return Promise.resolve();
    return this.task ?? this.read(caseId);
  };

  private read(caseId: string | null): Promise<void> {
    this.cancelRead();
    if (caseId === null) {
      this.publish({ caseId, status: "idle", investigation: null, error: null });
      return Promise.resolve();
    }
    if (!isCaseId(caseId)) {
      this.publish({ caseId, status: "error", investigation: null, error: new Error("调查 ID 无效。") });
      return Promise.resolve();
    }
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    const previous = caseId === this.snapshot.caseId ? this.snapshot.investigation : null;
    this.publish({ caseId, status: "loading", investigation: previous, error: null });
    const task = Promise.resolve().then(() => this.loader(caseId, controller.signal)).then((investigation) => {
      if (generation !== this.generation || controller.signal.aborted) return;
      if (investigation.id !== caseId) throw new Error("调查响应的案件身份不匹配。");
      this.publish({ caseId, status: "ready", investigation, error: null });
    }).catch((error: unknown) => {
      if (generation !== this.generation || controller.signal.aborted) return;
      this.publish({ caseId, status: "error", investigation: previous,
        error: error instanceof Error ? error : new Error("调查读取失败。") });
    }).finally(() => {
      if (generation === this.generation) {
        this.controller = null;
        this.task = null;
      }
    });
    this.task = task;
    return task;
  }
}
