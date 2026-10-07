import test from 'node:test';
import assert from 'node:assert/strict';
import { parseRoute, caseHref, isLocalHost } from '../src/product/routes.ts';
import { hasArchiveContext } from '../src/product/archive-context.ts';

test('hash routes preserve the exact review stage through refresh and history', () => {
  for (const step of ['v1', 'review', 'v2']) assert.deepEqual(parseRoute(caseHref(step)), { page: 'case', step });
  assert.deepEqual(parseRoute('#/cases/uniswap93?step=unknown'), { page: 'case', step: 'v1' });
  assert.deepEqual(parseRoute('#/developers'), { page: 'developers' });
  assert.deepEqual(parseRoute('#/verify'), { page: 'verify' });
  assert.deepEqual(parseRoute('#/unrelated'), { page: 'missing' });
});
test('legacy investigation and second-client links resolve to workspace while explicit navigation wins', () => {
  assert.deepEqual(parseRoute('', '?investigation=uni-case'), { page: 'workspace' });
  assert.deepEqual(parseRoute('', '?client=second&view=bundles'), { page: 'workspace' });
  assert.deepEqual(parseRoute('#workspace'), { page: 'workspace' });
  assert.deepEqual(parseRoute('#/', '?investigation=uni-case'), { page: 'home' });
  assert.deepEqual(parseRoute('#/workspace', '?client=second'), { page: 'workspace' });
});
test('static deployments do not probe a local backend', () => {
  assert.equal(isLocalHost('hnnuyuxuan.github.io'), false);
  assert.equal(isLocalHost('127.0.0.1.example.com'), false);
  assert.equal(isLocalHost('127.0.0.1'), true);
  assert.equal(isLocalHost('localhost'), true);
});
const expected = { caseId: 'uni-case', localVersionId: 32, contentHash: 'a'.repeat(64), localReviewId: 7, reviewHash: 'b'.repeat(64), decision: 'correction_requested' };
const record = () => ({ id: 'uni-case', versions: [{ version_id: 32, content_hash: 'a'.repeat(64) }], reviews: [{ review_id: 7, version_id: 32, review_hash: 'b'.repeat(64), decision: 'correction_requested' }] });
test('archived readback requires exact saved case, version and review context', () => {
  assert.equal(hasArchiveContext(record(), expected), true);
  assert.equal(hasArchiveContext(null, expected), false);
  for (const mutation of [r => r.id = 'other-case', r => r.versions[0].content_hash = 'c'.repeat(64), r => r.reviews[0].review_hash = 'c'.repeat(64), r => r.reviews[0].version_id = 33, r => r.reviews[0].decision = 'approved', r => r.reviews = []]) {
    const value = record(); mutation(value); assert.equal(hasArchiveContext(value, expected), false);
  }
  assert.equal(hasArchiveContext({ id: 'uni-case', versions: [] }, expected), false);
});
