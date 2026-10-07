import test from "node:test";
import assert from "node:assert/strict";
import { parseRoute, routeHref } from "../src/routing/routes.ts";
import { createBrowserRouter } from "../src/routing/browser-router.ts";

test("routes keep public entry, home, case identity and tools context separate", () => {
  assert.equal(parseRoute("/").page, "landing");
  assert.equal(parseRoute("/app/").page, "home");
  assert.deepEqual(parseRoute("/cases/saved-7", "?tab=evidence&evidence=tx%3Alog%2B1&client=second"), {
    page: "case", client: "second", caseId: "saved-7", tab: "evidence", evidenceId: "tx:log+1",
  });
  assert.deepEqual(parseRoute("/tools", "?case=saved-7&client=second"), {
    page: "tools", client: "second", caseId: "saved-7",
  });
});

test("malformed or unsafe case identifiers cannot become API case targets", () => {
  for (const path of ["/cases/", "/cases/%", "/cases/%2f", "/cases/../bad", "/cases/" + "a".repeat(65)]) {
    assert.equal(parseRoute(path).page, "not-found", path);
  }
  assert.equal(parseRoute("/tools", "?case=").page, "not-found");
  assert.equal(parseRoute("/cases").page, "not-found");
  assert.throws(() => routeHref({ page: "case", client: "primary", caseId: "../../other", tab: "overview" }));
});

test("case URLs round trip a real identity, selected tab, evidence and client role", () => {
  for (const route of [
    { page: "landing", client: "primary" },
    { page: "home", client: "second" },
    { page: "case", client: "primary", caseId: "saved-7", tab: "overview" },
    { page: "case", client: "second", caseId: "saved-7", tab: "evidence", evidenceId: "transfer:0x1&log=3" },
    { page: "tools", client: "second", caseId: "saved-7" },
  ]) {
    const href = routeHref(route);
    const url = new URL(href, "http://127.0.0.1:5186");
    assert.deepEqual(parseRoute(url.pathname, url.search), route);
  }
  assert.equal(parseRoute("/cases/saved-7", "?tab=invalid").tab, "overview");
  assert.equal(parseRoute("/cases/saved-7", "?tab=trace&evidence=E1").evidenceId, undefined);
});

function historyHost(initial = "/app") {
  const entries = [initial];
  let index = 0;
  const listeners = new Set();
  const location = () => new URL(entries[index], "http://127.0.0.1:5186");
  return {
    host: {
      get location() { return location(); },
      history: {
        pushState(_state, _title, url) { entries.splice(++index); entries.push(String(url)); },
        replaceState(_state, _title, url) { entries[index] = String(url); },
      },
      addEventListener(_type, listener) { listeners.add(listener); },
      removeEventListener(_type, listener) { listeners.delete(listener); },
    },
    back() { if (index) { index -= 1; listeners.forEach((listener) => listener()); } },
    forward() { if (index < entries.length - 1) { index += 1; listeners.forEach((listener) => listener()); } },
    get entries() { return entries; },
    get listenerCount() { return listeners.size; },
  };
}

test("navigation publishes URL state and browser back/forward without duplicate entries", () => {
  const fixture = historyHost();
  const router = createBrowserRouter(fixture.host);
  const snapshots = [];
  const unsubscribe = router.subscribe(() => snapshots.push(router.getSnapshot()));
  const caseRoute = { page: "case", client: "primary", caseId: "saved-7", tab: "overview" };
  router.navigate(caseRoute);
  router.navigate(caseRoute);
  router.navigate({ page: "tools", client: "primary", caseId: "saved-7" });
  fixture.back();
  fixture.forward();
  assert.deepEqual(snapshots, ["/cases/saved-7", "/tools?case=saved-7", "/cases/saved-7", "/tools?case=saved-7"]);
  assert.equal(fixture.entries.length, 3);
  unsubscribe();
  assert.equal(fixture.listenerCount, 0);
});

test("replace navigation and subscriptions have one shared popstate listener", () => {
  const fixture = historyHost();
  const router = createBrowserRouter(fixture.host);
  const first = router.subscribe(() => {});
  const second = router.subscribe(() => {});
  assert.equal(fixture.listenerCount, 1);
  router.navigate({ page: "tools", client: "second" }, { replace: true });
  assert.deepEqual(fixture.entries, ["/tools?client=second"]);
  first();
  assert.equal(fixture.listenerCount, 1);
  second();
  assert.equal(fixture.listenerCount, 0);
});

test("public demos round trip while unsupported presets remain missing", () => {
  for (const caseId of ["uniswap93", "euler-20230313"]) {
    const route = {page: "demo", client: "primary", caseId};
    assert.deepEqual(parseRoute(routeHref(route)), route);
  }
  assert.equal(parseRoute("/demo/unlisted").page, "not-found");
});

test("hash routes preserve the static deployment directory and reload identity", () => {
  const entries = ["http://localhost/gcc/#/app"];
  let index = 0;
  const listeners = new Map();
  const host = {
    get location() { return new URL(entries[index]); },
    history: {
      pushState(_s,_t,href) { const next = new URL(href, entries[index]).href; entries.splice(++index); entries.push(next); },
      replaceState(_s,_t,href) { entries[index] = new URL(href, entries[index]).href; },
    },
    addEventListener(type, fn) { listeners.set(type, fn); },
    removeEventListener(type) { listeners.delete(type); },
  };
  const router = createBrowserRouter(host, true);
  const unsubscribe = router.subscribe(() => {});
  router.navigate({page:"case",client:"second",caseId:"saved-7",tab:"evidence",evidenceId:"E:1"});
  assert.equal(host.location.pathname, "/gcc/");
  assert.equal(router.getSnapshot(), "/cases/saved-7?client=second&tab=evidence&evidence=E%3A1");
  assert.equal(createBrowserRouter(host, true).getSnapshot(), router.getSnapshot());
  index--; listeners.get("popstate")();
  assert.equal(router.getSnapshot(), "/app");
  unsubscribe(); assert.equal(listeners.size, 0);
});
