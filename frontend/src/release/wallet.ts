import { connectWallet, walletSnapshot, isTransactionHash, parseAccounts, parseChainId } from "./primitives.ts";
import type { WalletProvider, WalletSnapshot } from "../bot-wallet.ts";
import { canSend } from "./model.ts";
import type { Prepared } from "./model.ts";

export interface WalletChoice {id:string;name:string;provider:WalletProvider;icon?:string}
export function discoverWallets(onChange:(choices:WalletChoice[])=>void):()=>void {
  const choices = new Map<string,WalletChoice>();
  const announce = (event:Event) => {
    const detail = (event as CustomEvent).detail as {info?:{uuid?:string;name?:string;icon?:string};provider?:WalletProvider}|undefined;
    if (!detail?.info?.uuid || !detail.provider || typeof detail.provider.request !== "function") return;
    const id = detail.info.uuid;
    choices.set(id,{id,name:detail.info.name?.slice(0,80)||"Browser wallet",provider:detail.provider,icon:detail.info.icon?.startsWith("data:image/") && detail.info.icon.length < 100_000 ? detail.info.icon : undefined});
    onChange([...choices.values()]);
  };
  window.addEventListener("eip6963:announceProvider",announce);
  window.dispatchEvent(new Event("eip6963:requestProvider"));
  const legacy = (window as Window & {ethereum?:WalletProvider}).ethereum;
  if (legacy && typeof legacy.request === "function" && ![...choices.values()].some(choice => choice.provider === legacy)) choices.set("injected",{id:"injected",name:"Browser wallet",provider:legacy});
  onChange([...choices.values()]);
  return () => window.removeEventListener("eip6963:announceProvider",announce);
}
export { connectWallet, walletSnapshot, parseAccounts, parseChainId };
export async function switchToBot(provider:WalletProvider):Promise<WalletSnapshot> {
  try { await provider.request({method:"wallet_switchEthereumChain",params:[{chainId:"0x2a5"}]}); }
  catch (error) {
    if ((error as {code?:number})?.code !== 4902) throw error;
    await provider.request({method:"wallet_addEthereumChain",params:[{chainId:"0x2a5",chainName:"BOT Mainnet",nativeCurrency:{name:"BOT",symbol:"BOT",decimals:18},rpcUrls:["https://rpc.botchain.ai"],blockExplorerUrls:["https://scan.botchain.ai"]}]});
    await provider.request({method:"wallet_switchEthereumChain",params:[{chainId:"0x2a5"}]});
  }
  return walletSnapshot(provider);
}
export async function send(provider:WalletProvider,prepared:Prepared,current:()=>boolean):Promise<string> {
  if (!canSend(prepared)) throw new Error("Prepare a complete gas estimate and sufficient BOT balance before signing.");
  const snapshot = await walletSnapshot(provider);
  if (!current() || snapshot.chainId !== 677 || snapshot.account.toLowerCase() !== prepared.transaction.from.toLowerCase()) throw new Error("The account or network changed. Prepare this action again.");
  const result = await provider.request({method:"eth_sendTransaction",params:[{...prepared.transaction}]});
  if (!isTransactionHash(result)) throw new Error("The wallet returned no valid transaction hash. Check wallet activity and recover the hash before retrying.");
  return result;
}
