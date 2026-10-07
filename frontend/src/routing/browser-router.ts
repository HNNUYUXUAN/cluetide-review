import { routeHref } from "./routes.ts";
import type { NavigableRoute } from "./routes.ts";

type Listener = () => void;
export interface RouterWindow {
  location: Pick<Location, "pathname" | "search"> & { hash?: string };
  history: Pick<History, "pushState" | "replaceState">;
  addEventListener(type: "popstate" | "hashchange", listener: Listener): void;
  removeEventListener(type: "popstate" | "hashchange", listener: Listener): void;
}

export function createBrowserRouter(host: RouterWindow, hashMode = false) {
  const listeners = new Set<Listener>();
  const getSnapshot = () => hashMode ? (host.location.hash?.slice(1) || "/") : `${host.location.pathname}${host.location.search}`;
  const notify = () => listeners.forEach((listener) => listener());
  const subscribe = (listener: Listener) => {
    if (!listeners.size) { host.addEventListener("popstate", notify); if (hashMode) host.addEventListener("hashchange", notify); }
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) { host.removeEventListener("popstate", notify); if (hashMode) host.removeEventListener("hashchange", notify); }
    };
  };
  const navigate = (route: NavigableRoute, options: { replace?: boolean } = {}) => {
    const href = routeHref(route);
    if (href === getSnapshot()) return;
    const destination = hashMode ? `#${href}` : href;
    if (options.replace) host.history.replaceState(null, "", destination);
    else host.history.pushState(null, "", destination);
    notify();
  };
  return { getSnapshot, subscribe, navigate };
}

let router: ReturnType<typeof createBrowserRouter> | undefined;
export function appRouter() {
  router ??= createBrowserRouter(window, true);
  return router;
}
