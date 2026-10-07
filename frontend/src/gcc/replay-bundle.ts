import type { ReplayCaseId, ReplayVersion } from './replay-data'

export interface ReplayTransfer {
  evidence_id: string; value_raw: string; block_number: number; log_index: number
  from_address: string; to_address: string; transaction_hash: string; block_hash: string
  token_address: string; directions: string[]
}
export interface ReplaySource {
  title: string; publisher: string; url: string; locator: string; summary: string
  actual_read_scope?: string; observed_on_date_utc?: string; source_quality_note?: string
}
export interface VerifiedReplayBundle {
  evidence: {
    transfers: ReplayTransfer[]; sources: ReplaySource[]
    metadata: { decimals: number; symbol: string }
    request: { from_block: number; to_block: number; address: string; token_address: string }
    coverage: { status: string; completed_queries: number; planned_queries: number }
  }
  report: {
    case_id: string; revision: number; parent_manifest_hash: string | null
    synthetic_fixture?: { mode: string; model_requests: number; transactions_signed_or_broadcast: number }
    agent?: { synthetic?: boolean; evidence: { evidence_id: string; kind: string; payload: Record<string, unknown> }[] }
    conclusion: { summary: string; claims: { text: string; evidence_ids: string[] }[]; assessments: { status: string; explanation: string; support_evidence_ids: string[]; counter_evidence_ids: string[] }[] }
  }
  manifest: { files: Record<string, { size: number; sha256: string }> }
  manifestHash: string; archiveHash: string; verifiedFiles: string[]; bytes: Uint8Array; markdown: string
}

export const demoAsset = (file: string) => new URL(`${import.meta.env.BASE_URL}gcc-demo/${file}`, document.baseURI).href

let verifierPromise: Promise<{ verifyBundle: (bytes: Uint8Array) => Promise<VerifiedReplayBundle> }> | undefined
export async function verifyReplayBundle(bytes: Uint8Array): Promise<VerifiedReplayBundle> {
  const moduleUrl = demoAsset('bundle.js')
  verifierPromise ??= import(/* @vite-ignore */ moduleUrl).catch(error => { verifierPromise = undefined; throw error })
  return (await verifierPromise).verifyBundle(bytes)
}

export async function loadReplayBundle(caseId: ReplayCaseId, version: ReplayVersion, signal?: AbortSignal) {
  const response = await fetch(demoAsset(`${caseId}-synthetic-v${version}.zip`), { signal, credentials: 'omit' })
  if (!response.ok) throw new Error(`v${version} 案卷读取失败（${response.status}），请重试。`)
  const bytes = new Uint8Array(await response.arrayBuffer())
  const bundle = await verifyReplayBundle(bytes)
  const indexResponse = await fetch(demoAsset('fixtures.json'), { signal, credentials: 'omit' })
  if (!indexResponse.ok) throw new Error('公开案卷摘要清单读取失败。')
  const index = await indexResponse.json() as Record<string, { archive: string; manifest: string }>
  const expected = index[`${caseId}-v${version}`]
  if (!expected || expected.archive !== bundle.archiveHash || expected.manifest !== bundle.manifestHash) throw new Error('案卷与公开素材摘要清单不一致。')
  if (bundle.report.case_id !== `${caseId}-synthetic` || bundle.report.revision !== version || bundle.report.synthetic_fixture?.mode !== 'synthetic' || bundle.report.synthetic_fixture.model_requests !== 0 || bundle.report.synthetic_fixture.transactions_signed_or_broadcast !== 0 || bundle.report.agent?.synthetic !== true) throw new Error('案卷身份、版本或合成示例标识不一致。')
  return bundle
}

export function downloadReplayBundle(bundle: VerifiedReplayBundle, caseId: ReplayCaseId, version: ReplayVersion) {
  const bytes = new Uint8Array(bundle.bytes)
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${caseId}-synthetic-v${version}.zip`
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
