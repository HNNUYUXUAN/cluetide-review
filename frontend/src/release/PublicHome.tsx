import type { LocalTask } from "./package.ts";
import { short } from "./model.ts";
import { Icon } from "./ui.tsx";

export function PublicHome({tasks,onCreate,onImport,onTask,onExplore,onVerify}:{tasks:LocalTask[];onCreate:()=>void;onImport:()=>void;onTask:(id:string)=>void;onExplore:()=>void;onVerify:()=>void}){
  return <>
    <section className="public-hero">
      <div className="wordmark-pattern" aria-hidden="true">{Array.from({length:6},(_,i)=><div key={i}>CLUETIDE CLUETIDE CLUETIDE</div>)}</div>
      <div className="hero-content"><h1>Approve an exact release.<br/>Verify what shipped.</h1><p>Review the package. Approve its scope. Check the result.</p><div className="hero-actions"><button className="button primary" onClick={onCreate}>Create release</button><button className="button secondary" onClick={onExplore}>Explore verified release</button></div><p className="hero-network">BOT Mainnet · 677</p></div>
    </section>
    <section className="start-section" aria-labelledby="start-title"><h2 id="start-title">Start with a release</h2><div className="entry-list">
      <button className="entry-row" onClick={onCreate}><span className="entry-number">01</span><span><strong>Create a release</strong><span>Prepare a package and register it on BOT Mainnet.</span></span><Icon name="arrow" size={29}/></button>
      <button className="entry-row" onClick={onImport}><span className="entry-number">02</span><span><strong>Review a version</strong><span>Open a shared task package and inspect the exact files.</span></span><Icon name="arrow" size={29}/></button>
      <button className="entry-row" onClick={onVerify}><span className="entry-number">03</span><span><strong>Verify a receipt</strong><span>Compare the signature, chain record and published bytes.</span></span><Icon name="arrow" size={29}/></button>
    </div></section>
    {tasks.length>0&&<section className="local-releases content-width"><div className="section-title"><h2>Your releases</h2><button className="text-button" onClick={onImport}>Import package</button></div><p>Stored on this device. Export a task backup to retain its original files.</p><div className="task-list">{tasks.map(task=><button className="local-task" key={task.task_id} onClick={()=>onTask(task.task_id)}><span><strong>{task.title}</strong><span>{task.versions.length} package versions · {short(task.task_id)}</span></span><Icon name="arrow"/></button>)}</div></section>}
  </>;
}
