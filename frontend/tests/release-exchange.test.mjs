import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { zipSync } from "fflate";
import { backupBytes, buildVersion, canonicalBytes, decodeUtf8, hasCompleteHistory, importPackage, manifestBytes, newTask, packageZip, requireCompleteHistory, sha256, strictJsonParse, unzipPackage, validateTask } from "../src/release/package.ts";
import { mergeTask } from "../src/release/browser-store.ts";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/release-public.json", import.meta.url), "utf8"));
const encode = value => new TextEncoder().encode(value);
const cloneTask = () => structuredClone(fixture.task);
const input = {summary: "Exact release", source_reference: "git:test", artifact_text: "<h1>Release</h1>", test_evidence: ["HTML checked"], expires_at: 2100000000};
const makeVersion = (task, overrides = {}, parent = 0) => buildVersion(task, {...input, ...overrides}, parent, input.expires_at - 86400);

test("UTF-8 BOM, CRLF, Chinese and emoji survive browser package round trips byte for byte", async () => {
  const original = encode("\ufeff<!doctype html>\r\n<p>协作 😀</p>\r\n");
  const task = cloneTask(), version = await makeVersion(task, {artifact_text: decodeUtf8(original)});
  task.versions = [version];
  const imported = await importPackage(packageZip(task, version));
  assert.deepEqual(encode(imported.versions[0].artifact_text), original);
  assert.equal(imported.versions[0].artifact_hash, await sha256(original));
  assert.throws(() => decodeUtf8(new Uint8Array([0xc0, 0xaf])));
});

test("historical manifests preserve raw formatting, old deadlines and original whitespace text", async () => {
  const task = cloneTask(), version = task.versions[0];
  version.manifest.summary = " "; version.summary = " ";
  version.manifest.expires_at = 1; version.expires_at = 1;
  version.manifest_text = "\ufeff" + JSON.stringify(version.manifest, null, 2).replaceAll("\n", "\r\n") + "\r\n";
  version.manifest_hash = await sha256(encode(version.manifest_text));
  const imported = await importPackage(packageZip(task, version));
  assert.deepEqual(manifestBytes(imported.versions[0]), encode(version.manifest_text));
  assert.equal(imported.versions[0].manifest.summary, " ");
  assert.equal(imported.versions[0].expires_at, 1);
  await assert.rejects(makeVersion(task, {summary: " "}), /summary/);
});

test("new text limits count Unicode scalars and reject unpaired surrogates and blank fields", async () => {
  const task = await newTask("😀".repeat(160), fixture.task.owner, fixture.task.reviewer);
  assert.equal([...task.title].length, 160);
  await assert.rejects(newTask("😀".repeat(161), fixture.task.owner, fixture.task.reviewer), /title/);
  const version = await makeVersion(task, {summary: "😀".repeat(1000), source_reference: "😀".repeat(300), test_evidence: ["😀".repeat(1000)]});
  assert.equal([...version.summary].length, 1000);
  await assert.rejects(makeVersion(task, {summary: "😀".repeat(1001)}), /summary/);
  await assert.rejects(makeVersion(task, {test_evidence: ["\ud800"]}), /evidence/);
  await assert.rejects(makeVersion(task, {artifact_text: " \r\n"}), /UTF-8/);
  await assert.rejects(makeVersion(task, {artifact_text: "<p>\ud800</p>"}), /UTF-8/);
});

test("32 KiB artifacts including JSON escape expansion are accepted exactly at the byte boundary", async () => {
  const task = cloneTask();
  const version = await makeVersion(task, {artifact_text: "\"".repeat(32768)});
  task.versions = [version];
  assert.equal(version.manifest.artifact.bytes, 32768);
  assert.equal((await importPackage(packageZip(task, version))).versions[0].artifact_text.length, 32768);
  await assert.rejects(makeVersion(task, {artifact_text: "\"".repeat(32769)}), /32 KiB/);
  await assert.rejects(makeVersion(task, {artifact_text: "😀".repeat(8193)}), /32 KiB/);
});

test("JSON duplicate keys, escaped aliases, invalid numbers and excessive nesting are rejected", () => {
  for (const raw of ['{"x":1,"x":2}', '{"x":1,"\\u0078":2}', '{"a":{"x":1,"x":2}}', '{"a":1e999}', '{"a":01}', '[true,]', '"\\ud800"', "[".repeat(66) + "0" + "]".repeat(66)]) assert.throws(() => strictJsonParse(raw), /JSON/);
  assert.deepEqual(strictJsonParse('\ufeff{"x":"escaped \\\"quote\\\"","a":[true,false,null,-2.5e2]}'), {x: 'escaped "quote"', a: [true, false, null, -250]});
  const parsed = strictJsonParse('{"__proto__":{"polluted":true}}');
  assert.equal(Object.prototype.polluted, undefined);
  assert.equal(Object.hasOwn(parsed, "__proto__"), true);
});

test("all original ZIP representations are checked before returning an imported task", async () => {
  const entries = unzipPackage(packageZip(fixture.task, fixture.version));
  await assert.rejects(importPackage(zipSync({...entries, "artifact.html": encode("<p>changed</p>")})), /copies/);
  await assert.rejects(importPackage(zipSync({...entries, "manifest.json": canonicalBytes({...fixture.version.manifest, summary: "changed"})})), /copies/);
  const raw = decodeUtf8(entries["task.json"]).replace('"title":', '"title":"hidden replacement","title":');
  await assert.rejects(importPackage(zipSync({...entries, "task.json": encode(raw)})), /duplicate/);
  await assert.rejects(importPackage(zipSync({...entries, "extra.json": encode("{}")})), /unsupported/);
  const badRaw = cloneTask(); badRaw.versions[0].manifest_text = JSON.stringify({...fixture.version.manifest, summary: "changed"});
  await assert.rejects(validateTask(badRaw), /manifest/);
});

test("ZIP paths, duplicate members, CRC, inflated size and member count are bounded", () => {
  assert.throws(() => unzipPackage(zipSync({"../task.json": encode("{}")})), /path/);
  assert.throws(() => unzipPackage(zipSync({"dir\\task.json": encode("{}")})), /path/);
  assert.throws(() => unzipPackage(zipSync({"C:task.json": encode("{}")})), /path/);
  const duplicates = zipSync({"task.json": encode("{}"), "tusk.json": encode("{}")});
  const from = encode("tusk.json"), to = encode("task.json");
  for (let i = 0; i <= duplicates.length - from.length; i++) if (from.every((b, n) => duplicates[i + n] === b)) duplicates.set(to, i);
  assert.throws(() => unzipPackage(duplicates), /duplicate/);
  const badCrc = zipSync({"task.json": encode("{}")}, {level: 0});
  badCrc[30 + "task.json".length] ^= 1;
  assert.throws(() => unzipPackage(badCrc), /CRC/);
  assert.throws(() => unzipPackage(zipSync({a: new Uint8Array(2 * 1024 * 1024 + 1), b: new Uint8Array(2 * 1024 * 1024)})), /expanded/);
  assert.throws(() => unzipPackage(zipSync(Object.fromEntries(Array.from({length: 161}, (_, n) => [String(n), encode("x")])))), /member count/);
  const bomb = zipSync({artifact: new Uint8Array(128 * 1024)});
  const view = new DataView(bomb.buffer, bomb.byteOffset, bomb.byteLength);
  view.setUint32(22, 1, true);
  for (let i = 0; i < bomb.length - 46; i++) if (view.getUint32(i, true) === 0x02014b50) {view.setUint32(i + 24, 1, true); break;}
  assert.throws(() => unzipPackage(bomb), /expanded/);
});

test("legacy two-file bundles use public roles while sparse history remains inspectable", async () => {
  const task = cloneTask();
  const version = await makeVersion(task, {}, 1);
  const bytes = zipSync({"manifest.json": manifestBytes(version), "artifact.html": encode(version.artifact_text)});
  await assert.rejects(importPackage(bytes), /public chain roles/);
  const imported = await importPackage(bytes, {resolveTask: async id => {assert.equal(id, task.task_id); return task;}});
  assert.equal(imported.versions[0].version_id, 2);
  assert.equal(hasCompleteHistory(imported), false);
  assert.throws(() => requireCompleteHistory(imported), /preceding/);
  const merged = mergeTask([task], imported)[0];
  assert.equal(hasCompleteHistory(merged), true);
  assert.doesNotThrow(() => requireCompleteHistory(merged));
  await assert.rejects(importPackage(bytes, {resolveTask: async () => ({...task, task_id: "0x" + "ab".repeat(32)})}), /resolved chain task/);
  await assert.rejects(importPackage(bytes, {resolveTask: async () => ({...task, target_id: "0x" + "ab".repeat(32)})}), /resolved chain target/);
});

test("fixed version exports include preceding originals and retain local user titles", async () => {
  const task = cloneTask(); task.versions.push(await makeVersion(task, {}, 1));
  const v1 = await importPackage(packageZip(task, task.versions[0]));
  assert.deepEqual(v1.versions.map(v => v.version_id), [1]);
  const v2 = await importPackage(packageZip(task, task.versions[1]));
  assert.deepEqual(v2.versions.map(v => v.version_id), [1, 2]);
  const placeholder = {...task, title: "Shared release", versions: []};
  assert.equal(mergeTask([placeholder], v2)[0].title, task.title);
  assert.equal(mergeTask([{...task, title: "My own title"}], v2)[0].title, "My own title");
});

test("exports validate commitments, exclude private fields and guarantee re-import size", async () => {
  const task = cloneTask(); task.write_key = "private capability";
  const backup = backupBytes(task);
  assert.equal(decodeUtf8(backup).includes("private capability"), false);
  assert.equal((await importPackage(backup)).task_id, task.task_id);
  task.versions[0].artifact_text += "changed";
  assert.throws(() => packageZip(task, task.versions[0]), /commitments/);
  const large = cloneTask(); large.versions = [];
  for (let i = 0; i < 50; i++) large.versions.push(await makeVersion(large, {artifact_text: "\u0001".repeat(32768)}, i));
  assert.throws(() => packageZip(large, large.versions.at(-1)), /4 MiB/);
  assert.throws(() => backupBytes(large), /4 MiB/);
});
