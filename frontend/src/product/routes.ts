export type StepKey = "v1" | "review" | "v2";
export type ProductRoute =
  | { page: "home" | "verify" | "developers" | "workspace" | "missing" }
  | { page: "case"; step: StepKey };

export function parseRoute(hash: string, search = ""): ProductRoute {
  const legacy = new URLSearchParams(search);
  if ((!hash || hash === "#workspace") && (hash === "#workspace" || legacy.has("investigation") || legacy.has("view") || legacy.has("client"))) return { page: "workspace" };
  const [path, query = ""] = hash.replace(/^#/, "").split("?", 2);
  if (path === "" || path === "/") return { page: "home" };
  if (path === "/cases/uniswap93") {
    const step = new URLSearchParams(query).get("step");
    return { page: "case", step: step === "review" || step === "v2" ? step : "v1" };
  }
  if (path === "/verify" || path === "/developers" || path === "/workspace") return { page: path.slice(1) as "verify" | "developers" | "workspace" };
  return { page: "missing" };
}

export const caseHref = (step: StepKey = "v1") => `#/cases/uniswap93?step=${step}`;
export const isLocalHost = (hostname: string) => ["127.0.0.1", "localhost", "[::1]"].includes(hostname);
