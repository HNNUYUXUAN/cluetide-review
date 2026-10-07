import { useEffect } from "react";
import { useAppRoute } from "./routing/useAppRoute";
import RouteLink from "./routing/RouteLink";
import StoryPage from "./gcc/StoryPage";
import ReplayPage from "./gcc/ReplayPage";
import Workspace from "./gcc/Workspace";
import BundleTools from "./gcc/BundleTools";
import "./gcc/gcc.css";

export default function App() {
  const { route, navigate } = useAppRoute();
  const client = route.client;
  const demo = (caseId: "uniswap93" | "euler-20230313" = "uniswap93") => navigate({ page: "demo", client, caseId });
  const workbench = () => navigate({ page: "home", client });
  const tools = () => navigate({ page: "tools", client });
  useEffect(() => {
    document.title = `${route.page === "landing" ? "带着证据，判断链上异动" : route.page === "demo" ? "公开案例回放" : "调查工作台"} | ClueTide`;
    const keyboard = (event: KeyboardEvent) => { if (["Tab", "Enter", " "].includes(event.key)) document.documentElement.dataset.input = "keyboard"; };
    const pointer = () => { document.documentElement.dataset.input = "pointer"; };
    window.addEventListener("keydown", keyboard); window.addEventListener("pointerdown", pointer);
    return () => { window.removeEventListener("keydown", keyboard); window.removeEventListener("pointerdown", pointer); };
  }, [route.page]);
  return <div className="gcc-app">
    <a className="gcc-skip" href="#gcc-main" onClick={event => { event.preventDefault(); document.getElementById("gcc-main")?.focus(); }}>跳到主要内容</a>
    <header className="gcc-header">
      <RouteLink className="gcc-brand" route={{ page: "landing", client }}>ClueTide</RouteLink>
      <nav aria-label="产品导航">
        <RouteLink route={{ page: "landing", client }} aria-current={route.page === "landing" ? "page" : undefined}>产品故事</RouteLink>
        <RouteLink route={{ page: "demo", client, caseId: "uniswap93" }} aria-current={route.page === "demo" ? "page" : undefined}>案例体验</RouteLink>
        <RouteLink route={{ page: "tools", client }} aria-current={route.page === "tools" ? "page" : undefined}>证据包复验</RouteLink>
      </nav>
      <RouteLink className="gcc-primary gcc-workspace-link" route={{ page: "home", client }}>打开工作台</RouteLink>
    </header>
    <main id="gcc-main" tabIndex={-1}>
      {route.page === "landing" ? <StoryPage onDemo={demo} onWorkbench={workbench} onTools={tools} /> :
        route.page === "demo" ? <ReplayPage key={route.caseId} caseId={route.caseId} onWorkbench={workbench} onTools={tools} /> :
        route.page === "not-found" ? <section className="gcc-empty"><h1>这个页面尚未找到</h1><p>从案例目录重新选择一份调查。</p><button className="gcc-primary" onClick={workbench}>打开工作台</button></section> :
        route.page === "tools" ? <BundleTools serverInitially={!!route.caseId}><Workspace route={route} navigate={navigate} onDemo={demo} /></BundleTools> : <Workspace route={route} navigate={navigate} onDemo={demo} />}
    </main>
    <footer className="gcc-footer"><span>ClueTide · Ethereum 事件调查与协作复核</span><a href="https://github.com/HNNUYUXUAN/cluetide-review/tree/GCC" target="_blank" rel="noreferrer">源码与复现说明</a></footer>
  </div>;
}
