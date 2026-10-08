import { archiveDate, assetUrl, networkLabel, story } from "./data";
import { Brand, Icon } from "./icons";
import { caseHref } from "./routes";

export function Home() {
  return <>
    <section className="ct-hero">
      <div className="ct-hero-copy"><h1>Every claim.<br />A traceable history.</h1>
        <p>Anchor evidence, review the exact version,<br className="ct-desktop" /> and preserve every correction on BOT Chain.</p>
        <div className="ct-actions"><a className="ct-button" href={caseHref()}>Explore the UNI case <Icon name="arrow" /></a><a className="ct-text-link" href={story.contract_url} target="_blank" rel="noreferrer">View contract <Icon name="arrow" /></a></div>
      </div>
      <div className="ct-hero-media"><img src={assetUrl("evidence-lineage.png")} alt="Three glass documents connected in sequence: version 1, review and version 2" fetchPriority="high" width="1536" height="1024" /></div>
    </section>
    <div className="ct-proof-strip" aria-label="Archived on-chain evidence"><span><Icon name="layers" />{networkLabel}</span><span>2 versions <b aria-hidden="true">·</b> 1 review</span><span>Recorded {archiveDate}</span></div>
    <section className="ct-flow"><h2>Evidence moves forward.<br />Its history stays intact.</h2><ol>
      <li><span className="ct-flow-icon"><Icon name="file" /></span><div><h3><span>01</span> Capture</h3><p>Anchor your claim on chain.</p><a href={caseHref("v1")}>Read the original report <Icon name="arrow" /></a></div></li>
      <li><span className="ct-flow-icon"><Icon name="search" /></span><div><h3><span>02</span> Review</h3><p>Bind each review to an exact version.</p><a href={caseHref("review")}>Follow the review <Icon name="arrow" /></a></div></li>
      <li><span className="ct-flow-icon"><Icon name="layers" /></span><div><h3><span>03</span> Correct</h3><p>Preserve every new version.</p><a href={caseHref("v2")}>See what changed <Icon name="arrow" /></a></div></li>
    </ol></section>
  </>;
}

export function Footer() {
  return <footer className="ct-footer"><div><Brand /><p>Evidence. Review. Revision.</p></div><p>Recorded on {networkLabel} · {archiveDate}<br />This demonstration uses one author/reviewer wallet.</p><a href="#/developers">Verification scope <Icon name="arrow" /></a></footer>;
}
