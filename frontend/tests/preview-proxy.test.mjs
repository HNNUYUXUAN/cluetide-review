import test from 'node:test';
import assert from 'node:assert/strict';
import { allowedPreviewRequest } from '../preview-proxy.mjs';
test('API proxy accepts exact loopback preview origins and rejects cross-site requests', () => {
  for (const port of [5187,4187]) {
    assert.equal(allowedPreviewRequest(`127.0.0.1:${port}`,`http://127.0.0.1:${port}`,'same-origin'),true);
    assert.equal(allowedPreviewRequest(`127.0.0.1:${port}`,undefined,undefined),true);
    for (const origin of ['null','https://evil.test','http://127.0.0.1:9999',`http://127.0.0.1:${port}.evil.test`]) assert.equal(allowedPreviewRequest(`127.0.0.1:${port}`,origin,'same-site'),false);
    assert.equal(allowedPreviewRequest(`127.0.0.1:${port}`,undefined,'cross-site'),false);
  }
  assert.equal(allowedPreviewRequest('evil.test','http://evil.test','same-origin'),false);
});
