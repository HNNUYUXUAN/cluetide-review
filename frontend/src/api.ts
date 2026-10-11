import type {
  Health,
  ExplanationAssessment,
  HistoryItem,
  Investigation,
  Preset,
  Scope,
  VerifiedImport,
} from "./types";

export function uiError(error: unknown, fallback = "The request could not be completed. Please try again."): string {
  const detail = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  if (!detail || /[\u3400-\u9fff]/u.test(detail)) return fallback;
  if (/^[a-z][a-z0-9_]+$/.test(detail)) return `${fallback} Code: ${detail}.`;
  return detail;
}

function errorMessage(payload: unknown, status: number): string {
  const fallback = `Request could not be completed (HTTP ${status}). Check the request and try again.`;
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return /[\u3400-\u9fff]/u.test(detail) ? fallback : detail;
    if (Array.isArray(detail))
      return detail
        .map((item) => uiError(item?.msg, "Invalid parameters"))
        .join("; ");
  }
  return fallback;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new ApiError(errorMessage(payload, response.status), response.status);
  }
  return response.json() as Promise<T>;
}

const jsonPost = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
  signal,
});
export const api = {
  health: () => request<Health>("/health"),
  preset: (id = "uniswap93") =>
    request<Preset>(`/cases/${encodeURIComponent(id)}`),
  catalog: () => request<{ cases: Preset[] }>("/cases"),
  history: async (): Promise<HistoryItem[]> => {
    const result = await request<
      HistoryItem[] | { investigations: HistoryItem[] }
    >("/investigations");
    return Array.isArray(result) ? result : (result.investigations ?? []);
  },
  get: (id: string, signal?: AbortSignal) =>
    request<Investigation>(`/investigations/${encodeURIComponent(id)}`, {
      signal,
    }),
  investigate: (scope: Scope, signal?: AbortSignal) =>
    request<Investigation>("/investigations", jsonPost(scope, signal)),
  stop: (id: string) =>
    request<Investigation>(`/investigations/${encodeURIComponent(id)}/stop`, {
      method: "POST",
    }),
  review: (
    id: string,
    reviewer: string,
    comment: string,
    version_id?: number,
  ) =>
    request<Investigation>(
      `/investigations/${encodeURIComponent(id)}/reviews`,
      jsonPost({ reviewer, comment, ...(version_id ? { version_id } : {}) }),
    ),
  version: (
    id: string,
    author: string,
    correction: string,
    parent_version_id: number,
    corrected_summary?: string,
    claim_replacements?: Record<number, string>,
    assessment_replacements?: Record<number, ExplanationAssessment>,
    corrected_classification?: string,
  ) =>
    request<Investigation>(
      `/investigations/${encodeURIComponent(id)}/versions`,
      jsonPost({
        author,
        correction,
        parent_version_id,
        ...(corrected_summary ? { corrected_summary } : {}),
        ...(claim_replacements ? { claim_replacements } : {}),
        ...(assessment_replacements ? { assessment_replacements } : {}),
        ...(corrected_classification ? { corrected_classification } : {}),
      }),
    ),
  import: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<VerifiedImport>("/bundles/import", { method: "POST", body });
  },
  bundleUrl: (id: string) =>
    `/api/investigations/${encodeURIComponent(id)}/bundle`,
};
