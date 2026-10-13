/** The preview is an intentionally small, inert display language. Original bytes stay separate. */
const allowed = new Set("h1 h2 h3 h4 h5 h6 p div span section article header footer main aside strong em b i u s small blockquote pre code ul ol li table thead tbody tfoot tr th td br hr dl dt dd".split(" "));
const escape = (s:string) => s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
export function previewDocument(source:string):string {
  // Retain only fixed tag names and escaped text; no source attribute or URL is emitted.
  const stripped=source.replace(/<!--[\s\S]*?(?:-->|$)/g,"").replace(/<(script|style|template|noscript)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi,"");
  const body=stripped.split(/(<[^>]*>)/g).map(part=>{
    if(!part.startsWith("<"))return escape(part);
    const tag=/^<\s*(\/?)\s*([a-z][a-z0-9]*)\b[^>]*>$/i.exec(part);
    return tag&&allowed.has(tag[2].toLowerCase())?`<${tag[1]}${tag[2].toLowerCase()}>`:"";
  }).join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'"><style>body{font:16px/1.6 system-ui;margin:24px;color:#252525;overflow-wrap:anywhere}pre{white-space:pre-wrap}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px}</style></head><body>${body}</body></html>`;
}
export interface DiffLine {kind:"same"|"added"|"removed";text:string;oldLine?:number;newLine?:number}
export function lineDiff(before:string,after:string):DiffLine[]{
  const a=before.split("\n"),b=after.split("\n"),out:DiffLine[]=[];
  // Bound work for the maximum 32 KiB input, including files with many short lines.
  if(a.length*b.length>1_000_000){
    let first=0;while(first<a.length&&first<b.length&&a[first]===b[first]){out.push({kind:"same",text:a[first],oldLine:first+1,newLine:first+1});first++;}
    let lastA=a.length,lastB=b.length;while(lastA>first&&lastB>first&&a[lastA-1]===b[lastB-1]){lastA--;lastB--;}
    for(let i=first;i<lastA;i++)out.push({kind:"removed",text:a[i],oldLine:i+1});
    for(let j=first;j<lastB;j++)out.push({kind:"added",text:b[j],newLine:j+1});
    while(lastA<a.length){out.push({kind:"same",text:a[lastA],oldLine:++lastA,newLine:++lastB});}return out;
  }
  const rows=Array.from({length:a.length+1},()=>new Uint16Array(b.length+1));
  for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)rows[i][j]=a[i]===b[j]?1+rows[i+1][j+1]:Math.max(rows[i+1][j],rows[i][j+1]);
  let i=0,j=0;while(i<a.length||j<b.length){
    if(i<a.length&&j<b.length&&a[i]===b[j]){out.push({kind:"same",text:a[i],oldLine:++i,newLine:++j});}
    else if(i<a.length&&(j===b.length||rows[i+1][j]>=rows[i][j+1]))out.push({kind:"removed",text:a[i],oldLine:++i});
    else out.push({kind:"added",text:b[j],newLine:++j});
  }return out;
}
