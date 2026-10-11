import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

export function Icon({name,size=20}:{name:"plus"|"refresh"|"wallet"|"file"|"arrow"|"check"|"close"|"external"|"network"|"info";size?:number}) {
  const paths:Record<string,ReactNode> = {plus:<><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></>,refresh:<><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 18 17"/></>,wallet:<><path d="M20 8H5a2 2 0 0 1 0-4h13v4M4 6v13a1 1 0 0 0 1 1h15V8M20 12h-6v4h6"/><path d="M16 14h1"/></>,file:<><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M9 12l-3 3 3 3M15 12l3 3-3 3M13 12l-2 6"/></>,arrow:<path d="M5 12h14M14 7l5 5-5 5"/>,check:<path d="m5 12 4 4L19 6"/>,close:<path d="m6 6 12 12M18 6 6 18"/>,external:<><path d="M14 3h7v7M21 3 11 13M10 3H4v17h17v-7"/></>,network:<><circle cx="12" cy="5" r="3"/><circle cx="5" cy="18" r="3"/><circle cx="19" cy="18" r="3"/><path d="m11 8-4 7M13 8l4 7M8 18h8"/></>,info:<><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/></>};
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export function Brand(){return <span className="brand"><svg width="49" height="32" viewBox="0 0 58 36" aria-hidden="true"><path d="M1 18C13-6 28 4 38 12c6 4 12 4 19-8-7 22-19 22-29 14C17 9 9 8 1 18Z" fill="#087f78"/><path d="M1 31C12 8 28 19 38 25c7 4 13 3 19-7-7 21-19 20-29 12-11-8-19-8-27 1Z" fill="#77bab3"/></svg><span>ClueTide</span></span>}
export function Dialog({title,description,onClose,children,wide=false}:{title:string;description?:string;onClose:()=>void;children:ReactNode;wide?:boolean}){
  const ref = useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const previous=document.activeElement as HTMLElement|null;
    const old=document.body.style.overflow;document.body.style.overflow="hidden";
    ref.current?.querySelector<HTMLElement>("input,textarea,button,select")?.focus();
    const keyboard=(event:KeyboardEvent)=>{
      if(event.key==="Escape"){event.preventDefault();onClose();}
      if(event.key!=="Tab")return;
      const focusable=Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]')||[]);
      const first=focusable[0],last=focusable.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    };
    document.addEventListener("keydown",keyboard);
    return()=>{document.body.style.overflow=old;document.removeEventListener("keydown",keyboard);previous?.focus();};
  },[onClose]);
  return <div className="dialog-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}><div ref={ref} className={`dialog ${wide?"dialog-wide":""}`} role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div className="dialog-heading"><div><h2 id="dialog-title">{title}</h2>{description&&<p>{description}</p>}</div><button className="icon-button" onClick={onClose} aria-label="Close dialog"><Icon name="close"/></button></div>{children}</div></div>;
}
export function Status({value}:{value:string}){return <span className={`status status-${value.replaceAll("_","-")}`}><span/>{value.replaceAll("_"," ").replace(/^./,s=>s.toUpperCase())}</span>}
export function Notice({children,tone="info"}:{children:ReactNode;tone?:"info"|"warning"|"error"}){return <div className={`notice notice-${tone}`} role={tone==="error"?"alert":"status"}><Icon name="info"/><div>{children}</div></div>}
export function Field({label,children,help}:{label:string;children:ReactNode;help?:string}){return <label className="field"><span>{label}</span>{children}{help&&<small>{help}</small>}</label>}
export function Data({label,value}:{label:string;value:ReactNode}){return <div className="data-row"><span>{label}</span><div>{value}</div></div>}
