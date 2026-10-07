export type ClientRole = "primary" | "second";
export type CaseTab = "overview" | "evidence" | "trace" | "versions";

interface RouteContext {
  client: ClientRole;
}
export type AppRoute =
  | (RouteContext & { page: "landing" })
  | (RouteContext & { page: "demo"; caseId: "uniswap93" | "euler-20230313" })
  | (RouteContext & { page: "home" })
  | (RouteContext & { page: "case"; caseId: string; tab: CaseTab; evidenceId?: string })
  | (RouteContext & { page: "tools"; caseId?: string })
  | (RouteContext & { page: "not-found"; path: string; reason: "unknown-path" | "invalid-case-id" });
export type NavigableRoute = Exclude<AppRoute, { page: "not-found" }>;

export const isCaseId = (value: unknown): value is string =>
  typeof value === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(value);

const tabs: readonly CaseTab[] = ["overview", "evidence", "trace", "versions"];
export function parseRoute(pathname: string, search = ""): AppRoute {
  const params = new URLSearchParams(search);
  const client: ClientRole = params.get("client") === "second" ? "second" : "primary";
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  const missing = (reason: "unknown-path" | "invalid-case-id"): AppRoute =>
    ({ page: "not-found", client, path: pathname, reason });
  if (path === "/") return { page: "landing", client };
  if (path === "/app") return { page: "home", client };
  if (path === "/demo/uniswap93" || path === "/demo/euler-20230313")
    return { page: "demo", client, caseId: path.slice(6) as "uniswap93" | "euler-20230313" };
  if (path === "/tools") {
    const caseId = params.get("case");
    if (caseId !== null && !isCaseId(caseId)) return missing("invalid-case-id");
    return { page: "tools", client, ...(caseId ? { caseId } : {}) };
  }
  if (!path.startsWith("/cases/")) return missing("unknown-path");
  let caseId: string;
  try {
    caseId = decodeURIComponent(path.slice("/cases/".length));
  } catch {
    return missing("invalid-case-id");
  }
  if (!isCaseId(caseId)) return missing("invalid-case-id");
  const requestedTab = params.get("tab");
  const tab: CaseTab = tabs.includes(requestedTab as CaseTab) ? requestedTab as CaseTab : "overview";
  const evidenceId = params.get("evidence");
  return {
    page: "case", client, caseId, tab,
    ...(tab === "evidence" && evidenceId && evidenceId.length <= 240 && !/[\u0000-\u001f\u007f]/.test(evidenceId)
      ? { evidenceId } : {}),
  };
}

export function routeHref(route: NavigableRoute): string {
  const params = new URLSearchParams();
  if (route.client === "second") params.set("client", "second");
  let path: string;
  switch (route.page) {
    case "landing": path = "/"; break;
    case "home": path = "/app"; break;
    case "demo": path = `/demo/${route.caseId}`; break;
    case "tools":
      path = "/tools";
      if (route.caseId !== undefined) {
        if (!isCaseId(route.caseId)) throw new Error("调查 ID 无效。");
        params.set("case", route.caseId);
      }
      break;
    case "case":
      if (!isCaseId(route.caseId)) throw new Error("调查 ID 无效。");
      path = `/cases/${encodeURIComponent(route.caseId)}`;
      if (route.tab !== "overview") params.set("tab", route.tab);
      if (route.tab === "evidence" && route.evidenceId) params.set("evidence", route.evidenceId);
      break;
  }
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}
