/** Bounded in-memory reader for ClueTide's five-member ZIP_STORED format. */
const MAX_BYTES = 16 * 1024 * 1024;
const NAMES = ['evidence.json', 'manifest.json', 'raw.json', 'report.json', 'report.md'];
const DATA_NAMES = NAMES.filter(name => name !== 'manifest.json');
const decoder = new TextDecoder('utf-8', { fatal: true });
const encoder = new TextEncoder();
const crcTable = Uint32Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let i = 0; i < 8; i++) crc = (crc & 1) ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  return crc >>> 0;
});
const fail = message => { throw new Error(message); };
const exactKeys = (value, expected) => value && !Array.isArray(value) && typeof value === 'object' && Object.keys(value).sort().join('|') === [...expected].sort().join('|');
const equalBytes = (left, right) => left.length === right.length && left.every((byte, i) => byte === right[i]);
const hashPattern = /^[0-9a-f]{64}$/;
const addressPattern = /^0x[0-9a-fA-F]{40}$/;
const txPattern = /^0x[0-9a-fA-F]{64}$/;
const privateFields = new Set(['apikey', 'apikeys', 'authorization', 'proxyauthorization', 'xapikey', 'privatekey', 'privatekeys', 'secret', 'secrets', 'clientsecret', 'password', 'passwd', 'credential', 'credentials', 'accesskey', 'accesskeyid', 'secretaccesskey', 'sessiontoken', 'accesstoken', 'refreshtoken', 'idtoken', 'authtoken', 'bearertoken', 'seedphrase', 'mnemonic', 'env', 'dotenv', 'keys', 'auth', 'authentication', 'xauthtoken', 'cookie', 'setcookie', 'secretkey']);
const credentialPatterns = [ /-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----/, /\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}\b/, /\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b/, /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/, /\b(?:xox[baprs]-)[A-Za-z0-9-]{16,}\b/, /\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/-]{12,}={0,2}\b/i, /https?:\/\/[^\s/@:]+:[^\s/@]+@/i ];

function inspectPublic(value, depth = 0) {
  if (depth > 64) fail('JSON exceeds the supported nesting depth.');
  if (typeof value === 'string') {
    for (const char of value) {
      const point = char.codePointAt(0);
      if (point >= 0xd800 && point <= 0xdfff) fail('JSON contains invalid Unicode.');
    }
    if (credentialPatterns.some(pattern => pattern.test(value))) fail('The archive contains credential content.');
  } else if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) fail('JSON numbers must be safe; blockchain quantities use strings.');
  } else if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      if (!Array.isArray(value)) {
        const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '');
        if (privateFields.has(normalized) || /(?:apikey|privatekey|clientsecret|accesstoken|refreshtoken|authtoken|password|secretkey|secretaccesskey)$/.test(normalized)) fail('The archive contains a credential field.');
        inspectPublic(key, depth + 1);
      }
      inspectPublic(child, depth + 1);
    }
  }
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

function readJson(bytes, name) {
  let text, value;
  try { text = decoder.decode(bytes); value = JSON.parse(text); } catch { fail(`${name} requires valid UTF-8 JSON.`); }
  inspectPublic(value);
  // This equality also rejects duplicate keys, whitespace and alternate spellings.
  if (!equalBytes(encoder.encode(canonical(value)), bytes)) fail(`${name} requires RFC 8785 canonical JSON.`);
  return value;
}

export async function sha256(bytes) {
  if (!globalThis.crypto?.subtle) fail('Verification requires HTTPS or localhost.');
  const result = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(result)].map(value => value.toString(16).padStart(2, '0')).join('');
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function storedZip(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength > MAX_BYTES || bytes.byteLength < 22) fail('Choose a complete ZIP no larger than 16 MiB.');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = offset => { if (offset < 0 || offset + 2 > bytes.length) fail('The ZIP directory or content is incomplete.'); return view.getUint16(offset, true); };
  const u32 = offset => { if (offset < 0 || offset + 4 > bytes.length) fail('The ZIP directory or content is incomplete.'); return view.getUint32(offset, true); };
  const end = bytes.length - 22;
  if (u32(end) !== 0x06054b50 || u16(end + 20) !== 0 || u16(end + 4) !== 0 || u16(end + 6) !== 0) fail('ZIP requires a complete single-volume central directory.');
  if (u16(end + 8) !== 5 || u16(end + 10) !== 5) fail('The evidence bundle must contain the five required files.');
  const centralSize = u32(end + 12), centralStart = u32(end + 16);
  if (centralStart + centralSize !== end) fail('The ZIP central directory position is inconsistent.');
  const members = [];
  let cursor = centralStart;
  for (let count = 0; count < 5; count++) {
    if (cursor + 46 > end || u32(cursor) !== 0x02014b50) fail('The ZIP central directory record is incomplete.');
    const flags = u16(cursor + 8), method = u16(cursor + 10), crc = u32(cursor + 16), compressedSize = u32(cursor + 20), size = u32(cursor + 24);
    const nameLength = u16(cursor + 28), extraLength = u16(cursor + 30), commentLength = u16(cursor + 32), disk = u16(cursor + 34), attrs = u32(cursor + 38), offset = u32(cursor + 42);
    const stop = cursor + 46 + nameLength + extraLength + commentLength;
    if (stop > end || disk !== 0 || extraLength || commentLength || (flags !== 0 && flags !== 0x800)) fail('Unsupported ZIP directory parameters.');
    if (method !== 0) fail('This verifier supports uncompressed ZIP_STORED evidence bundles exported by ClueTide.');
    if (compressedSize !== size || size > MAX_BYTES) fail('ZIP file sizes are inconsistent.');
    const type = (attrs >>> 16) & 0xf000;
    if (type !== 0 && type !== 0x8000) fail('The bundle accepts regular files only.');
    let name;
    try { name = decoder.decode(bytes.subarray(cursor + 46, cursor + 46 + nameLength)); } catch { fail('ZIP file names must use UTF-8.'); }
    if (!NAMES.includes(name) || members.some(member => member.name === name)) fail('The bundle contains a duplicate or unknown path.');
    members.push({ name, flags, method, crc, size, nameLength, offset });
    cursor = stop;
  }
  if (cursor !== end) fail('The ZIP central directory length is inconsistent.');
  members.sort((a, b) => a.offset - b.offset);
  let expectedOffset = 0, total = 0;
  const payloads = new Map();
  for (const member of members) {
    const start = member.offset;
    if (start !== expectedOffset || start + 30 > centralStart || u32(start) !== 0x04034b50) fail('The local ZIP header position is inconsistent.');
    if (u16(start + 6) !== member.flags || u16(start + 8) !== member.method || u32(start + 14) !== member.crc || u32(start + 18) !== member.size || u32(start + 22) !== member.size || u16(start + 26) !== member.nameLength || u16(start + 28) !== 0) fail('The local and central ZIP directories disagree.');
    const dataStart = start + 30 + member.nameLength, dataEnd = dataStart + member.size;
    if (dataEnd > centralStart || !equalBytes(bytes.subarray(start + 30, dataStart), encoder.encode(member.name))) fail('ZIP file paths or boundaries are inconsistent.');
    const data = bytes.subarray(dataStart, dataEnd);
    if (crc32(data) !== member.crc) fail(`${member.name} failed CRC32 verification: file bytes changed or were corrupted.`);
    total += data.length;
    if (total > MAX_BYTES) fail('Evidence exceeds the 16 MiB limit.');
    payloads.set(member.name, data);
    expectedOffset = dataEnd;
  }
  if (expectedOffset !== centralStart) fail('The ZIP includes undeclared file content.');
  return payloads;
}

function validateEvidence(evidence, report, raw) {
  if (!evidence || evidence.schema_version !== 'cluetide-evidence/v1' || !report || report.schema_version !== 'cluetide-report/v1' || !raw || typeof raw !== 'object' || Array.isArray(raw)) fail('The bundle requires ClueTide v1 evidence and report schemas.');
  if (typeof report.case_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(report.case_id) || !Number.isSafeInteger(report.revision) || report.revision < 1) fail('The report must identify a case and a positive integer revision.');
  const request = evidence.request;
  if (!request || request.chain_id !== 1 || !addressPattern.test(request.address) || !addressPattern.test(request.token_address) || !Number.isSafeInteger(request.from_block) || !Number.isSafeInteger(request.to_block) || request.from_block < 0 || request.to_block < request.from_block || request.to_block - request.from_block >= 2000) fail('The investigation window or Ethereum address is invalid.');
  if (!Array.isArray(evidence.transfers) || evidence.transfers.length > 20000 || !evidence.coverage || !['complete', 'empty', 'partial', 'error'].includes(evidence.coverage.status)) fail('The evidence records or coverage status are invalid.');
  if (!Array.isArray(report.agent?.evidence ?? [])) fail('The Agent evidence structure is invalid.');
  const references = new Set();
  for (const transfer of evidence.transfers) {
    if (!transfer || transfer.chain_id !== 1 || typeof transfer.evidence_id !== 'string' || references.has(transfer.evidence_id) || !txPattern.test(transfer.transaction_hash) || !txPattern.test(transfer.block_hash) || !addressPattern.test(transfer.from_address) || !addressPattern.test(transfer.to_address) || !addressPattern.test(transfer.token_address) || typeof transfer.value_raw !== 'string' || !/^(0|[1-9][0-9]*)$/.test(transfer.value_raw) || transfer.value_raw.length > 78 || BigInt(transfer.value_raw) >= 2n ** 256n || !Number.isSafeInteger(transfer.block_number) || transfer.block_number < request.from_block || transfer.block_number > request.to_block || !Number.isSafeInteger(transfer.log_index) || transfer.log_index < 0) fail('The Transfer quantity, identity or block range is invalid.');
    references.add(transfer.evidence_id);
  }
  const agentReferences = new Set();
  for (const item of report.agent?.evidence ?? []) {
    if (!item || typeof item.evidence_id !== 'string' || !item.evidence_id || agentReferences.has(item.evidence_id)) fail('Agent evidence IDs must be nonempty and unique within the Agent record.');
    agentReferences.add(item.evidence_id);
    references.add(item.evidence_id);
  }
  const conclusion = report.conclusion;
  if (!conclusion || typeof conclusion.summary !== 'string' || !Array.isArray(conclusion.claims) || !Array.isArray(conclusion.assessments)) fail('The report conclusion structure is invalid.');
  for (const claim of conclusion.claims) {
    if (!claim || typeof claim.text !== 'string' || !Array.isArray(claim.evidence_ids) || claim.evidence_ids.some(id => !references.has(id))) fail('A report claim has an invalid reference.');
  }
  for (const assessment of conclusion.assessments) {
    if (!assessment || !['supported', 'refuted', 'unknown'].includes(assessment.status) || typeof assessment.explanation !== 'string' || !Array.isArray(assessment.support_evidence_ids) || !Array.isArray(assessment.counter_evidence_ids) || [...assessment.support_evidence_ids, ...assessment.counter_evidence_ids].some(id => !references.has(id)) || (assessment.unknowns !== undefined && (!Array.isArray(assessment.unknowns) || assessment.unknowns.some(item => typeof item !== 'string')))) fail('An explanation status or evidence reference is invalid.');
  }
  if (report.parent_manifest_hash != null && (typeof report.parent_manifest_hash !== 'string' || !hashPattern.test(report.parent_manifest_hash))) fail('The parent manifest commitment must be a SHA-256 string.');
}

export async function verifyBundle(bytes) {
  const payloads = storedZip(bytes);
  const manifest = readJson(payloads.get('manifest.json'), 'manifest.json');
  if (!exactKeys(manifest, ['format', 'canonicalization', 'integrity_statement', 'files']) || manifest.format !== 'cluetide-public-evidence/v1' || manifest.canonicalization !== 'RFC8785' || manifest.integrity_statement !== 'SHA-256 verifies artifact integrity, not factual truth.' || !exactKeys(manifest.files, DATA_NAMES)) fail('The manifest format or file inventory is invalid.');
  const verifiedFiles = [];
  for (const name of DATA_NAMES) {
    const record = manifest.files[name], payload = payloads.get(name);
    if (!exactKeys(record, ['sha256', 'size']) || !hashPattern.test(record.sha256) || !Number.isSafeInteger(record.size) || record.size !== payload.length || record.sha256 !== await sha256(payload)) fail(`${name} does not match the manifest SHA-256 or size.`);
    verifiedFiles.push(name);
  }
  const evidence = readJson(payloads.get('evidence.json'), 'evidence.json');
  const report = readJson(payloads.get('report.json'), 'report.json');
  const raw = readJson(payloads.get('raw.json'), 'raw.json');
  let markdown;
  try { markdown = decoder.decode(payloads.get('report.md')); } catch { fail('report.md requires valid UTF-8.'); }
  inspectPublic(markdown);
  validateEvidence(evidence, report, raw);
  return { manifest, evidence, report, raw, markdown, payloads, verifiedFiles, manifestHash: await sha256(payloads.get('manifest.json')), archiveHash: await sha256(bytes), bytes };
}
