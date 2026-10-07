import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { verifyBundle } from '../src/product/bundle-verifier.js';

const read = async version => new Uint8Array(await readFile(new URL(`../public/bot-demo/uni-${version}.zip`, import.meta.url)));
test('original UNI archives retain their recorded content and parent commitments', async () => {
  const [v1, v2] = await Promise.all([read('v1').then(verifyBundle), read('v2').then(verifyBundle)]);
  assert.equal(v1.archiveHash, '37e6d1adab334a751a93da1cc5d7f8401229b333f3e3ce50353f650123c67e4b');
  assert.equal(v2.archiveHash, 'b8f918413e79f797b3a3606c835d64b79919ca5ecdc369e28723dffa112037d6');
  assert.equal(v1.manifestHash, 'a41f6d5233d353a90af7c28b387238d59bc05ffdbc6631de49c9a91392fdd6fb');
  assert.equal(v2.manifestHash, 'b012792582b0121bfd9a9223904541e89e4bba6f89dfd66208e8889ccc2223ef');
  assert.equal(v2.report.parent_manifest_hash, v1.manifestHash);
  assert.equal(v1.report.revision, 1);
  assert.equal(v2.report.revision, 2);
  assert.equal(v1.verifiedFiles.length, 4);
});
test('the same observed transfer can appear in the source and Agent evidence sections', async () => {
  const bundle = await verifyBundle(await read('v1'));
  const originalIds = new Set(bundle.evidence.transfers.map(value => value.evidence_id));
  assert.ok(bundle.report.agent.evidence.some(value => originalIds.has(value.evidence_id)));
});
test('changed file bytes fail ZIP integrity validation', async () => {
  const bytes = await read('v2');
  bytes[100] ^= 1;
  await assert.rejects(verifyBundle(bytes), /CRC32/);
});
test('truncated and oversized input archives are rejected', async () => {
  await assert.rejects(verifyBundle((await read('v1')).slice(0, 1000)));
  await assert.rejects(verifyBundle(new Uint8Array(16 * 1024 * 1024 + 1)), /16 MiB/);
});
