import type {
  Health,
  ExplanationAssessment,
  HistoryItem,
  Investigation,
  Preset,
  Scope,
  VerifiedImport,
} from "./types";

function errorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail
        .map((item) => (typeof item?.msg === "string" ? item.msg : "参数无效"))
        .join("；");
  }
  return `请求未完成 (HTTP ${status})`;
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
    throw new Error(errorMessage(payload, response.status));
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
  bundleUrl: (id: string, version?: number) =>
    `/api/investigations/${encodeURIComponent(id)}/bundle${version ? `?version=${version}` : ""}`,
};
