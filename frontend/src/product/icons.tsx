export function Icon({ name, className = "" }: { name: "arrow" | "external" | "download" | "layers" | "file" | "search" | "check" | "link" | "info" | "copy" | "menu" | "close" | "alert"; className?: string }) {
  const paths = {
    arrow: <><path d="M4 12h16M14 6l6 6-6 6" /></>,
    external: <><path d="M14 3h7v7M21 3l-10 10M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5" /></>,
    download: <><path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4" /></>,
    layers: <><path d="m12 3 10 6-10 6L2 9l10-6ZM2 13l10 6 10-6M2 17l10 6 10-6" /></>,
    file: <><path d="M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h8" /></>,
    search: <><circle cx="10.5" cy="10.5" r="7.5" /><path d="m16 16 5 5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    link: <><path d="m10 13 4-4M8 16l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 2 1-1a4 4 0 1 1 6 6l-5 5a4 4 0 0 1-6 0" /></>,
    info: <><circle cx="12" cy="12" r="10" /><path d="M12 11v6M12 7h.01" /></>,
    copy: <><rect x="8" y="7" width="12" height="15" rx="2" /><path d="M15 7V2H3v15h5" /></>,
    menu: <path d="M3 6h18M3 12h18M3 18h18" />,
    close: <path d="m5 5 14 14M5 19 19 5" />,
    alert: <><path d="m12 2 11 20H1L12 2ZM12 8v6M12 18h.01" /></>,
  };
  return <svg className={`ct-icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function Brand() {
  return <a className="ct-brand" href="#/" aria-label="ClueTide home"><span>Clue<span>Tide</span></span></a>;
}
