import test from "node:test";
import assert from "node:assert/strict";
import { CaseResource } from "../src/state/case-resource.ts";

const record = (id, status = "complete") => ({ id, status, evidence: null, agent: null, report: null, versions: [], reviews: [] });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = () => Promise.resolve();

test("rapid case switching cancels the old request and ignores its late success", async () => {
  const requests = [];
  const resource = new CaseResource((id, signal) => {
    const response = deferred(); requests.push({ id, signal, response }); return response.promise;
  });
  const first = resource.select("case-A");
  await tick();
  const second = resource.select("case-B");
  await tick();
  assert.equal(requests[0].signal.aborted, true);
  assert.equal(resource.getSnapshot().caseId, "case-B");
  assert.equal(resource.getSnapshot().investigation, null);
  requests[1].response.resolve(record("case-B"));
  await second;
  requests[0].response.resolve(record("case-A"));
  await first;
  assert.equal(resource.getSnapshot().investigation.id, "case-B");
});

test("a late failure from another case cannot replace the selected case's state", async () => {
  const requests = [];
  const resource = new CaseResource(() => {
    const response = deferred(); requests.push(response); return response.promise;
  });
  const first = resource.select("case-A"); await tick();
  const second = resource.select("case-B"); await tick();
  requests[1].resolve(record("case-B")); await second;
  requests[0].reject(new Error("old request failed")); await first;
  assert.equal(resource.getSnapshot().status, "ready");
  assert.equal(resource.getSnapshot().error, null);
});

test("repeated selection and refresh deduplicate an in-flight case request", async () => {
  const response = deferred(); let calls = 0;
  const resource = new CaseResource(() => { calls += 1; return response.promise; });
  const first = resource.select("case-A");
  assert.equal(resource.select("case-A"), first);
  assert.equal(resource.refresh("case-A"), first);
  await tick();
  assert.equal(calls, 1);
  response.resolve(record("case-A")); await first;
  await resource.select("case-A");
  assert.equal(calls, 1);
});

test("refresh preserves the current record while reading and keeps API errors", async () => {
  const responses = [deferred(), deferred()]; let calls = 0;
  const resource = new CaseResource(() => responses[calls++].promise);
  const first = resource.select("case-A"); await tick();
  responses[0].resolve(record("case-A")); await first;
  const refresh = resource.refresh("case-A"); await tick();
  assert.equal(resource.getSnapshot().status, "loading");
  assert.equal(resource.getSnapshot().investigation.id, "case-A");
  const apiError = new Error("HTTP 503");
  responses[1].reject(apiError); await refresh;
  assert.equal(resource.getSnapshot().status, "error");
  assert.equal(resource.getSnapshot().error, apiError);
  assert.equal(resource.getSnapshot().investigation.id, "case-A");
});

test("a completed action for an older case cannot refresh over the current route", async () => {
  let calls = 0;
  const resource = new CaseResource(async (id) => { calls += 1; return record(id); });
  await resource.select("case-A");
  await resource.select("case-B");
  await resource.refresh("case-A");
  assert.equal(calls, 2);
  assert.equal(resource.getSnapshot().investigation.id, "case-B");
});

test("response identity must match the requested investigation", async () => {
  const resource = new CaseResource(async () => record("other-case"));
  await resource.select("case-A");
  assert.equal(resource.getSnapshot().status, "error");
  assert.equal(resource.getSnapshot().investigation, null);
});

test("clearing the selection prevents a cancelled request from repopulating tools or home", async () => {
  const response = deferred(); let signal;
  const resource = new CaseResource((_id, requestSignal) => { signal = requestSignal; return response.promise; });
  const first = resource.select("case-A"); await tick();
  await resource.select(null);
  response.resolve(record("case-A")); await first;
  assert.equal(signal.aborted, true);
  assert.deepEqual(resource.getSnapshot(), { caseId: null, status: "idle", investigation: null, error: null });
});

test("invalid IDs do not call the loader and subscriptions can cleanly detach", async () => {
  let calls = 0, updates = 0;
  const resource = new CaseResource(async (id) => { calls += 1; return record(id); });
  const unsubscribe = resource.subscribe(() => updates += 1);
  await resource.select("../other");
  assert.equal(calls, 0);
  assert.equal(resource.getSnapshot().status, "error");
  assert.equal(updates, 1);
  unsubscribe();
  await resource.select("case-A");
  assert.equal(updates, 1);
});

test("cancelled reads can be re-entered for the same case", async () => {
  const requests = [];
  const resource = new CaseResource((id, signal) => {
    const response = deferred(); requests.push({ id, signal, response }); return response.promise;
  });
  const first = resource.select("case-A"); await tick();
  resource.cancelRead();
  const next = resource.select("case-A"); await tick();
  requests[1].response.resolve(record("case-A")); await next;
  requests[0].response.resolve(record("case-A", "partial")); await first;
  assert.equal(requests[0].signal.aborted, true);
  assert.equal(resource.getSnapshot().investigation.status, "complete");
});
