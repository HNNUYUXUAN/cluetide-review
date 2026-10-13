import type { LocalTask } from "./package.ts";
import { short } from "./model.ts";
import { Icon } from "./ui.tsx";

export function PublicHome({tasks,onCreate,onImport,onTask,onExplore,onVerify,publisherState,onCheckPublisher}:{tasks:LocalTask[];onCreate:()=>void;onImport:()=>void;onTask:(id:string)=>void;onExplore:()=>void;onVerify:()=>void;publisherState:string;onCheckPublisher:()=>void}){
  return <>
    <section className="public-hero">
      <div className="wordmark-pattern" aria-hidden="true">{Array.from({length:6},(_,i)=><div key={i}>CLUETIDE CLUETIDE CLUETIDE</div>)}</div>
      <div className="hero-content"><h1>Approve an exact release.<br/>Verify what shipped.</h1><p>For Web3 teams shipping with agents.<br/>Agree on the version. Keep the evidence.</p><div className="hero-actions"><button className="button primary" onClick={onCreate}>Create release</button><button className="button secondary" onClick={onExplore}>Explore verified release</button></div><p className="hero-network">BOT Mainnet · 677</p></div>
    </section>
    <section className="start-section" aria-labelledby="start-title"><h2 id="start-title">Start with a release</h2><div className="entry-list">
      <button className="entry-row" onClick={onCreate}><span className="entry-number">01</span><span><strong>Create a release</strong><span>Prepare a package and register it on BOT Mainnet.</span></span><Icon name="arrow" size={29}/></button>
      <button className="entry-row" onClick={onImport}><span className="entry-number">02</span><span><strong>Review a version</strong><span>Open a shared task package and inspect the exact files.</span></span><Icon name="arrow" size={29}/></button>
      <button className="entry-row" onClick={onVerify}><span className="entry-number">03</span><span><strong>Verify a receipt</strong><span>Compare the signature, chain record and published bytes.</span></span><Icon name="arrow" size={29}/></button>
    </div></section>
    <section className="collaboration-story content-width"><h2>One project page. A shared commitment.</h2><p>Your Agent prepares a launch page. A partner reviews the exact changes and sends signed correction requirements. Every revision returns to review; one approval authorizes one start. After publication, each collaborator can carry away the originals and verify the delivered bytes.</p><div className="story-columns"><div><h3>Changes return to review</h3><p>A revised package needs a new approval. Decisions and their Gas costs are visible on BOT Mainnet.</p></div><div><h3>One authorization, one start</h3><p>A confirmed start survives device changes. Recover its existing attempt using the transaction hash.</p></div><div><h3>Evidence travels with the work</h3><p>The acceptance bundle binds originals, the executor’s statement and the downloaded result. Tests and human review establish business quality.</p></div></div><div className="publisher-status"><h3>Publication service</h3><p>{publisherState}</p><button className="text-button" onClick={onCheckPublisher}>Check availability</button></div></section>
    {tasks.length>0&&<section className="local-releases content-width"><div className="section-title"><h2>Your releases</h2><button className="text-button" onClick={onImport}>Import package</button></div><p>Stored on this device. Export a task backup to retain its original files.</p><div className="task-list">{tasks.map(task=><button className="local-task" key={task.task_id} onClick={()=>onTask(task.task_id)}><span><strong>{task.title}</strong><span>{task.versions.length} package versions · {short(task.task_id)}</span></span><Icon name="arrow"/></button>)}</div></section>}
  </>;
}
