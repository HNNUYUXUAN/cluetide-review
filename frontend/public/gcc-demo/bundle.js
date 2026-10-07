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
  if (depth > 64) fail('JSON 层级超过校验上限。');
  if (typeof value === 'string') {
    for (const char of value) {
      const point = char.codePointAt(0);
      if (point >= 0xd800 && point <= 0xdfff) fail('JSON 含无效 Unicode 字符。');
    }
    if (credentialPatterns.some(pattern => pattern.test(value))) fail('文件含不适合公开证据包的认证内容。');
  } else if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) fail('JSON 数字超出安全范围；链上金额应使用字符串。');
  } else if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      if (!Array.isArray(value)) {
        const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '');
        if (privateFields.has(normalized) || /(?:apikey|privatekey|clientsecret|accesstoken|refreshtoken|authtoken|password|secretkey|secretaccesskey)$/.test(normalized)) fail('文件含不适合公开证据包的认证字段。');
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
  try { text = decoder.decode(bytes); value = JSON.parse(text); } catch { fail(`${name} 需要有效的 UTF-8 JSON。`); }
  inspectPublic(value);
  // This equality also rejects duplicate keys, whitespace and alternate spellings.
  if (!equalBytes(encoder.encode(canonical(value)), bytes)) fail(`${name} 需要 RFC 8785 规范 JSON。`);
  return value;
}

export async function sha256(bytes) {
  if (!globalThis.crypto?.subtle) fail('完整性验证需要 HTTPS 或本机 localhost 安全环境。');
  const result = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(result)].map(value => value.toString(16).padStart(2, '0')).join('');
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function storedZip(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength > MAX_BYTES || bytes.byteLength < 22) fail('请选择 16 MiB 以内的完整 ZIP 文件。');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = offset => { if (offset < 0 || offset + 2 > bytes.length) fail('ZIP 目录或内容不完整。'); return view.getUint16(offset, true); };
  const u32 = offset => { if (offset < 0 || offset + 4 > bytes.length) fail('ZIP 目录或内容不完整。'); return view.getUint32(offset, true); };
  const end = bytes.length - 22;
  if (u32(end) !== 0x06054b50 || u16(end + 20) !== 0 || u16(end + 4) !== 0 || u16(end + 6) !== 0) fail('ZIP 需要单卷、完整的中央目录。');
  if (u16(end + 8) !== 5 || u16(end + 10) !== 5) fail('证据包应包含固定的五个文件。');
  const centralSize = u32(end + 12), centralStart = u32(end + 16);
  if (centralStart + centralSize !== end) fail('ZIP 中央目录位置不一致。');
  const members = [];
  let cursor = centralStart;
  for (let count = 0; count < 5; count++) {
    if (cursor + 46 > end || u32(cursor) !== 0x02014b50) fail('ZIP 中央目录记录不完整。');
    const flags = u16(cursor + 8), method = u16(cursor + 10), crc = u32(cursor + 16), compressedSize = u32(cursor + 20), size = u32(cursor + 24);
    const nameLength = u16(cursor + 28), extraLength = u16(cursor + 30), commentLength = u16(cursor + 32), disk = u16(cursor + 34), attrs = u32(cursor + 38), offset = u32(cursor + 42);
    const stop = cursor + 46 + nameLength + extraLength + commentLength;
    if (stop > end || disk !== 0 || extraLength || commentLength || (flags !== 0 && flags !== 0x800)) fail('ZIP 目录参数不受此查看器支持。');
    if (method !== 0) fail('此查看器支持未压缩（stored）证据 ZIP。请使用 ClueTide 示例导出的格式。');
    if (compressedSize !== size || size > MAX_BYTES) fail('ZIP 文件大小不一致。');
    const type = (attrs >>> 16) & 0xf000;
    if (type !== 0 && type !== 0x8000) fail('证据包只接受常规文件。');
    let name;
    try { name = decoder.decode(bytes.subarray(cursor + 46, cursor + 46 + nameLength)); } catch { fail('ZIP 文件名需要 UTF-8。'); }
    if (!NAMES.includes(name) || members.some(member => member.name === name)) fail('证据包含重复或未知文件路径。');
    members.push({ name, flags, method, crc, size, nameLength, offset });
    cursor = stop;
  }
  if (cursor !== end) fail('ZIP 中央目录长度不一致。');
  members.sort((a, b) => a.offset - b.offset);
  let expectedOffset = 0, total = 0;
  const payloads = new Map();
  for (const member of members) {
    const start = member.offset;
    if (start !== expectedOffset || start + 30 > centralStart || u32(start) !== 0x04034b50) fail('ZIP 本地文件目录位置不一致。');
    if (u16(start + 6) !== member.flags || u16(start + 8) !== member.method || u32(start + 14) !== member.crc || u32(start + 18) !== member.size || u32(start + 22) !== member.size || u16(start + 26) !== member.nameLength || u16(start + 28) !== 0) fail('ZIP 本地目录与中央目录不一致。');
    const dataStart = start + 30 + member.nameLength, dataEnd = dataStart + member.size;
    if (dataEnd > centralStart || !equalBytes(bytes.subarray(start + 30, dataStart), encoder.encode(member.name))) fail('ZIP 文件路径或边界不一致。');
    const data = bytes.subarray(dataStart, dataEnd);
    if (crc32(data) !== member.crc) fail(`${member.name} 的 CRC32 校验失败：文件内容已改变或损坏。`);
    total += data.length;
    if (total > MAX_BYTES) fail('证据内容超过 16 MiB 上限。');
    payloads.set(member.name, data);
    expectedOffset = dataEnd;
  }
  if (expectedOffset !== centralStart) fail('ZIP 含未声明的文件内容。');
  return payloads;
}

function validateEvidence(evidence, report, raw) {
  if (!evidence || evidence.schema_version !== 'cluetide-evidence/v1' || !report || report.schema_version !== 'cluetide-report/v1' || !raw || typeof raw !== 'object' || Array.isArray(raw)) fail('此包需要 ClueTide v1 证据与报告结构。');
  const request = evidence.request;
  if (!request || request.chain_id !== 1 || !addressPattern.test(request.address) || !addressPattern.test(request.token_address) || !Number.isSafeInteger(request.from_block) || !Number.isSafeInteger(request.to_block) || request.from_block < 0 || request.to_block < request.from_block || request.to_block - request.from_block >= 2000) fail('调查窗口或 Ethereum 地址格式无效。');
  if (!Array.isArray(evidence.transfers) || evidence.transfers.length > 20000 || !evidence.coverage || !['complete', 'empty', 'partial', 'error'].includes(evidence.coverage.status)) fail('证据记录或覆盖状态无效。');
  if (!Array.isArray(report.agent?.evidence ?? [])) fail('补查证据结构无效。');
  const references = new Set();
  for (const transfer of evidence.transfers) {
    if (!transfer || transfer.chain_id !== 1 || typeof transfer.evidence_id !== 'string' || references.has(transfer.evidence_id) || !txPattern.test(transfer.transaction_hash) || !txPattern.test(transfer.block_hash) || !addressPattern.test(transfer.from_address) || !addressPattern.test(transfer.to_address) || !addressPattern.test(transfer.token_address) || typeof transfer.value_raw !== 'string' || !/^(0|[1-9][0-9]*)$/.test(transfer.value_raw) || transfer.value_raw.length > 78 || BigInt(transfer.value_raw) >= 2n ** 256n || !Number.isSafeInteger(transfer.block_number) || transfer.block_number < request.from_block || transfer.block_number > request.to_block || !Number.isSafeInteger(transfer.log_index) || transfer.log_index < 0) fail('Transfer 的金额、标识或区块范围无效。');
    references.add(transfer.evidence_id);
  }
  for (const item of report.agent?.evidence ?? []) {
    if (!item || typeof item.evidence_id !== 'string' || references.has(item.evidence_id)) fail('补查证据标识无效或重复。');
    references.add(item.evidence_id);
  }
  const conclusion = report.conclusion;
  if (!conclusion || typeof conclusion.summary !== 'string' || !Array.isArray(conclusion.claims) || !Array.isArray(conclusion.assessments)) fail('报告结论结构无效。');
  for (const claim of conclusion.claims) {
    if (typeof claim.text !== 'string' || !Array.isArray(claim.evidence_ids) || claim.evidence_ids.some(id => !references.has(id))) fail('报告主张引用了包外证据。');
  }
  for (const assessment of conclusion.assessments) {
    if (!['supported', 'refuted', 'unknown'].includes(assessment.status) || typeof assessment.explanation !== 'string' || !Array.isArray(assessment.support_evidence_ids) || !Array.isArray(assessment.counter_evidence_ids) || [...assessment.support_evidence_ids, ...assessment.counter_evidence_ids].some(id => !references.has(id)) || (assessment.unknowns !== undefined && (!Array.isArray(assessment.unknowns) || assessment.unknowns.some(item => typeof item !== 'string')))) fail('候选解释状态或证据引用无效。');
  }
  if (report.parent_manifest_hash != null && !hashPattern.test(report.parent_manifest_hash)) fail('父版本 manifest 摘要格式无效。');
}

export async function verifyBundle(bytes) {
  const payloads = storedZip(bytes);
  const manifest = readJson(payloads.get('manifest.json'), 'manifest.json');
  if (!exactKeys(manifest, ['format', 'canonicalization', 'integrity_statement', 'files']) || manifest.format !== 'cluetide-public-evidence/v1' || manifest.canonicalization !== 'RFC8785' || manifest.integrity_statement !== 'SHA-256 verifies artifact integrity, not factual truth.' || !exactKeys(manifest.files, DATA_NAMES)) fail('Manifest 格式或文件清单无效。');
  const verifiedFiles = [];
  for (const name of DATA_NAMES) {
    const record = manifest.files[name], payload = payloads.get(name);
    if (!exactKeys(record, ['sha256', 'size']) || !hashPattern.test(record.sha256) || !Number.isSafeInteger(record.size) || record.size !== payload.length || record.sha256 !== await sha256(payload)) fail(`${name} 与 manifest 的 SHA-256 或大小不一致。`);
    verifiedFiles.push(name);
  }
  const evidence = readJson(payloads.get('evidence.json'), 'evidence.json');
  const report = readJson(payloads.get('report.json'), 'report.json');
  const raw = readJson(payloads.get('raw.json'), 'raw.json');
  let markdown;
  try { markdown = decoder.decode(payloads.get('report.md')); } catch { fail('report.md 需要有效 UTF-8。'); }
  inspectPublic(markdown);
  validateEvidence(evidence, report, raw);
  return { manifest, evidence, report, raw, markdown, payloads, verifiedFiles, manifestHash: await sha256(payloads.get('manifest.json')), archiveHash: await sha256(bytes), bytes };
}
