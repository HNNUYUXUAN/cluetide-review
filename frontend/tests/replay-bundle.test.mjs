import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { verifyBundle } from '../public/gcc-demo/bundle.js'

const asset = name => new URL(`../public/gcc-demo/${name}`, import.meta.url)
const fixtures = JSON.parse(await readFile(asset('fixtures.json'), 'utf8'))
const load = (caseId, version) => readFile(asset(`${caseId}-synthetic-v${version}.zip`)).then(bytes => new Uint8Array(bytes))
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value)
const utf8 = value => new TextEncoder().encode(value)

function crc32(bytes) {
  let crc = 0xffffffff
  for (const byte of bytes) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? 0xedb88320 ^ crc >>> 1 : crc >>> 1
  }
  return (crc ^ 0xffffffff) >>> 0
}

function zip(files) {
  const local = [], central = []
  let offset = 0
  for (const [name, bytes] of files) {
    const encodedName = utf8(name), crc = crc32(bytes)
    const record = Buffer.alloc(30)
    record.writeUInt32LE(0x04034b50, 0); record.writeUInt16LE(20, 4)
    record.writeUInt32LE(crc, 14); record.writeUInt32LE(bytes.length, 18); record.writeUInt32LE(bytes.length, 22); record.writeUInt16LE(encodedName.length, 26)
    local.push(record, encodedName, bytes)
    const entry = Buffer.alloc(46)
    entry.writeUInt32LE(0x02014b50, 0); entry.writeUInt16LE(20, 4); entry.writeUInt16LE(20, 6)
    entry.writeUInt32LE(crc, 16); entry.writeUInt32LE(bytes.length, 20); entry.writeUInt32LE(bytes.length, 24); entry.writeUInt16LE(encodedName.length, 28); entry.writeUInt32LE(offset, 42)
    central.push(entry, encodedName)
    offset += 30 + encodedName.length + bytes.length
  }
  const directory = Buffer.concat(central), end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10); end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16)
  return new Uint8Array(Buffer.concat([...local, directory, end]))
}

async function modifyReport(change) {
  const original = await verifyBundle(await load('uniswap93', 2))
  const payloads = new Map(original.payloads)
  const report = JSON.parse(new TextDecoder().decode(payloads.get('report.json')))
  change(report)
  const reportBytes = utf8(canonical(report))
  payloads.set('report.json', reportBytes)
  const manifest = JSON.parse(new TextDecoder().decode(payloads.get('manifest.json')))
  manifest.files['report.json'] = { size: reportBytes.length, sha256: hash(reportBytes) }
  payloads.set('manifest.json', utf8(canonical(manifest)))
  return zip([...payloads])
}

for (const caseId of ['uniswap93', 'euler-20230313']) {
  test(`${caseId}: both public versions verify and bind the exact parent manifest`, async () => {
    const bundles = await Promise.all([1, 2].map(async version => {
      const bundle = await verifyBundle(await load(caseId, version))
      assert.equal(bundle.report.case_id, `${caseId}-synthetic`)
      assert.equal(bundle.report.revision, version)
      assert.equal(bundle.report.synthetic_fixture.model_requests, 0)
      assert.equal(bundle.report.synthetic_fixture.transactions_signed_or_broadcast, 0)
      assert.equal(bundle.report.synthetic_fixture.mode, 'synthetic')
      assert.equal(bundle.archiveHash, fixtures[`${caseId}-v${version}`].archive)
      assert.equal(bundle.manifestHash, fixtures[`${caseId}-v${version}`].manifest)
      assert.equal(bundle.payloads.size, 5)
      return bundle
    }))
    assert.equal(bundles[1].report.parent_manifest_hash, bundles[0].manifestHash)
    assert.equal(bundles[0].report.parent_manifest_hash, null)
  })
}

test('Euler amounts retain all integer digits and the bounded net-flow identity', async () => {
  const bundle = await verifyBundle(await load('euler-20230313', 2))
  const transfers = bundle.evidence.transfers
  const net = transfers.reduce((value, transfer) => value + (transfer.directions.includes('in') ? 1n : -1n) * BigInt(transfer.value_raw), 0n)
  assert.equal(net.toString(), '-8904507348306697267428294')
  assert.equal(transfers.filter(transfer => transfer.directions.includes('in')).length, 2)
  assert.equal(transfers.filter(transfer => transfer.directions.includes('out')).length, 1)
  assert.equal(bundle.evidence.request.to_block - bundle.evidence.request.from_block + 1, 3)
})

test('changing one payload byte fails CRC32 verification', async () => {
  const bytes = await load('uniswap93', 2)
  bytes[100] ^= 1
  await assert.rejects(verifyBundle(bytes), /CRC32/)
})

test('recomputed ZIP CRC cannot conceal a manifest SHA-256 mismatch', async () => {
  const bundle = await verifyBundle(await load('uniswap93', 2))
  const files = new Map(bundle.payloads)
  files.set('report.md', utf8('modified report'))
  await assert.rejects(verifyBundle(zip([...files])), /SHA-256/)
})

test('missing, duplicate and traversal members are rejected', async () => {
  const bundle = await verifyBundle(await load('uniswap93', 2))
  const files = [...bundle.payloads]
  await assert.rejects(verifyBundle(zip(files.slice(0, 4))), /五个文件/)
  const duplicate = [...files]; duplicate[4] = files[0]
  await assert.rejects(verifyBundle(zip(duplicate)), /重复或未知文件路径/)
  const traversal = [...files]; traversal[0] = ['../evidence.json', files[0][1]]
  await assert.rejects(verifyBundle(zip(traversal)), /重复或未知文件路径/)
})

test('a rehashed report must still reference evidence inside its own bundle', async () => {
  const bytes = await modifyReport(report => { report.conclusion.claims[0].evidence_ids = ['outside-the-bundle'] })
  await assert.rejects(verifyBundle(bytes), /包外证据/)
})

test('candidate references and statuses remain independently checked', async () => {
  const bytes = await modifyReport(report => { report.conclusion.assessments[0].status = 'accepted' })
  await assert.rejects(verifyBundle(bytes), /候选解释/)
})

test('truncated archives fail before content can be displayed', async () => {
  const bytes = await load('euler-20230313', 1)
  await assert.rejects(verifyBundle(bytes.slice(0, bytes.length - 10)), /中央目录/)
})
