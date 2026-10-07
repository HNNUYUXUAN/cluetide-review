import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useBackend } from "./backend";
import { CaseExplorer } from "./CaseExplorer";
import { Developers } from "./Developers";
import { Home, Footer } from "./Home";
import { Brand, Icon } from "./icons";
import { caseHref, parseRoute } from "./routes";

const Verify = lazy(() => import("./Verify"));
const Workspace = lazy(() => import("../App"));
const navigation = [{ label: "Product", href: "#/", page: "home" }, { label: "Case explorer", href: caseHref(), page: "case" }, { label: "Verify", href: "#/verify", page: "verify" }, { label: "Developers", href: "#/developers", page: "developers" }];

export default function ProductApp() {
  const [route, setRoute] = useState(() => parseRoute(location.hash, location.search));
  const [menu, setMenu] = useState(false);
  const main = useRef<HTMLElement>(null);
  const mounted = useRef(false);
  const { backend, retry } = useBackend();
  useEffect(() => {
    const update = () => { setRoute(parseRoute(location.hash, location.search)); setMenu(false); };
    window.addEventListener("hashchange", update); window.addEventListener("popstate", update);
    return () => { window.removeEventListener("hashchange", update); window.removeEventListener("popstate", update); };
  }, []);
  useEffect(() => {
    const title = route.page === "case" ? "UNI case explorer" : route.page === "home" ? "Every claim. A traceable history." : route.page === "missing" ? "Page not found" : route.page[0].toUpperCase() + route.page.slice(1);
    document.title = `ClueTide · ${title}`;
    if (mounted.current) { main.current?.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "instant" }); }
    mounted.current = true;
  }, [route.page]);
  return <div className="ct-product"><a className="ct-skip" href="#main" onClick={event => { event.preventDefault(); main.current?.focus(); }}>Skip to content</a>
    <header className="ct-header"><Brand /><nav className={menu ? "is-open" : ""} aria-label="Product navigation">{navigation.map(item => <a key={item.page} href={item.href} aria-current={route.page === item.page ? "page" : undefined}>{item.label}</a>)}</nav><a className="ct-button ct-header-action" href="#/workspace">Open workspace <Icon name="arrow" /></a><button className="ct-menu" aria-label={menu ? "Close navigation" : "Open navigation"} aria-expanded={menu} onClick={() => setMenu(!menu)}><Icon name={menu ? "close" : "menu"} /></button></header>
    <main ref={main} id="ct-main" className={route.page === "workspace" ? "ct-main ct-workspace-main" : "ct-main"} tabIndex={-1}>
      <Suspense fallback={<div className="ct-page ct-loading" role="status">Loading workspace…</div>}>
        {route.page === "home" ? <Home /> : route.page === "case" ? <CaseExplorer step={route.step} backend={backend} /> : route.page === "verify" ? <Verify /> : route.page === "developers" ? <Developers /> : route.page === "workspace" ? <>
          <div className="ct-workspace-intro"><div><h1>Your investigation workspace.</h1><p>Investigate, review a saved version, and register evidence through your wallet.</p></div><span className={`ct-service-state ${backend === "available" ? "ct-positive" : ""}`}><i />{backend === "available" ? "Local service connected" : backend === "checking" ? "Checking local service" : "Local service required"}</span></div>
          {backend === "available" ? <div className="ct-workspace"><Workspace /></div> : <section className="ct-workspace-connect ct-panel"><Icon name="layers" /><h2>{backend === "checking" ? "Connecting to your local workspace…" : "Continue with your local workspace."}</h2><p>The public demo includes the recorded UNI case and browser evidence checks. Investigations, saved reports and wallet preparation run in your local ClueTide service.</p><div className="ct-actions"><a className="ct-button" href="http://127.0.0.1:8765/bot.html#/workspace" target="_blank" rel="noreferrer">Open local workspace <Icon name="external" /></a>{backend !== "static" ? <button className="ct-button ct-outline" onClick={retry} disabled={backend === "checking"}>Retry connection</button> : null}<a className="ct-text-link" href={caseHref()}>Explore the recorded case <Icon name="arrow" /></a></div><details><summary>Start the local application</summary><p>From your ClueTide BOT project directory, run:</p><pre>powershell -File scripts/run-local.ps1</pre><p>The service runs at http://127.0.0.1:8765 and preserves your saved investigation and public transaction context on that origin.</p></details></section>}
        </> : <div className="ct-page ct-missing"><h1>This page could not be found.</h1><p>Continue with the recorded case or return to the product.</p><a className="ct-button" href="#/">Back to ClueTide <Icon name="arrow" /></a></div>}
      </Suspense>
    </main><Footer />
  </div>;
}
