import { useMemo, useSyncExternalStore } from "react";
import { appRouter } from "./browser-router";
import { parseRoute } from "./routes";

export function useAppRoute() {
  const router = appRouter();
  const href = useSyncExternalStore(router.subscribe, router.getSnapshot);
  const route = useMemo(() => {
    const queryAt = href.indexOf("?");
    return queryAt === -1 ? parseRoute(href) : parseRoute(href.slice(0, queryAt), href.slice(queryAt));
  }, [href]);
  return { route, navigate: router.navigate };
}
