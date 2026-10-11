import type { BotTransaction } from "../bot-types.ts";
import type { WalletProvider, WalletSnapshot } from "../bot-wallet.ts";

export const isAddress=(value:unknown):value is string=>typeof value==="string"&&/^0x[0-9a-fA-F]{40}$/.test(value)&&!/^0x0{40}$/.test(value);
export const isTransactionHash=(value:unknown):value is string=>typeof value==="string"&&/^0x[0-9a-fA-F]{64}$/.test(value)&&!/^0x0{64}$/.test(value);
export const isDecimalId=(value:unknown):value is string=>typeof value==="string"&&/^[1-9][0-9]{0,77}$/.test(value)&&BigInt(value)<2n**256n;
const quantity=(value:unknown):value is string=>typeof value==="string"&&/^0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})$/.test(value);
export function validateTransaction(value:unknown):BotTransaction{
  if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("The unsigned transaction is unreadable.");
  const tx=value as Record<string,unknown>;
  if(Object.keys(tx).some(key=>!["from","to","data","value","chainId","gas","gasPrice"].includes(key))||!isAddress(tx.from)||(tx.to!==undefined&&!isAddress(tx.to))||typeof tx.data!=="string"||tx.data.length>2_000_002||!/^0x(?:[0-9a-fA-F]{2})+$/.test(tx.data)||tx.value!=="0x0"||tx.chainId!=="0x2a5"||(tx.gas!==undefined&&(!quantity(tx.gas)||BigInt(tx.gas)===0n))||(tx.gasPrice!==undefined&&!quantity(tx.gasPrice)))throw new Error("The unsigned transaction parameters failed validation.");
  return tx as unknown as BotTransaction;
}
export function parseChainId(value:unknown):number{if(!quantity(value)||BigInt(value)>BigInt(Number.MAX_SAFE_INTEGER))throw new Error("The wallet returned an invalid network ID.");return Number(BigInt(value));}
export function parseAccounts(value:unknown):string[]{if(!Array.isArray(value)||value.length>20||!value.every(isAddress))throw new Error("The wallet returned an invalid account list.");return value;}
export async function walletSnapshot(provider:WalletProvider):Promise<WalletSnapshot>{const [accounts,chain]=await Promise.all([provider.request({method:"eth_accounts"}),provider.request({method:"eth_chainId"})]);const parsed=parseAccounts(accounts);if(!parsed.length)throw new Error("Connect a wallet account to continue.");return {account:parsed[0],chainId:parseChainId(chain)};}
export async function connectWallet(provider:WalletProvider):Promise<WalletSnapshot>{parseAccounts(await provider.request({method:"eth_requestAccounts"}));return walletSnapshot(provider);}
export function formatBot(value:string|null):string{if(value===null||!/^(?:0|[1-9][0-9]{0,77})$/.test(value))return "Unavailable";const wei=BigInt(value),fraction=(wei%10n**18n).toString().padStart(18,"0").replace(/0+$/,"");return `${wei/10n**18n}${fraction?`.${fraction}`:""} BOT`;}
