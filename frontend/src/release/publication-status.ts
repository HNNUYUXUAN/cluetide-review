export function publicationStatus(receipt:{result:number;artifactMatches:boolean;chainMatches:boolean;archived:boolean;chainStatus?:string}|undefined,recorded:boolean,started:boolean,publishing=false):string{
  if(receipt?.chainStatus==="unavailable")return receipt.result===2?"Execution failed · current chain check unavailable":"Output verified · current chain check unavailable";
  if(receipt?.chainStatus==="mismatch")return "Receipt commitment differs · inspect the attempt";
  if(receipt?.result===2)return receipt.chainMatches?"Execution failed · receipt verified":"Execution failed · receipt awaiting registration";
  if(receipt?.result===1&&receipt.artifactMatches&&receipt.chainMatches)return receipt.archived?"Archived publication verified":"Publication verified";
  if(receipt?.result===1&&receipt.artifactMatches)return "Published, receipt awaiting registration";
  if(recorded)return "Receipt recorded · verify the result";
  if(publishing)return "Publication result pending";
  if(started)return "Start authorized";
  return "Awaiting an approved start";
}
