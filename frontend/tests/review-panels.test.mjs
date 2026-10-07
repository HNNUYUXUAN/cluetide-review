import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { build } from "esbuild";

const frontend = fileURLToPath(new URL("..", import.meta.url));
const python = process.env.CLUETIDE_TEST_PYTHON ?? resolve(frontend, "../.venv/Scripts/python.exe");

const harness = `
import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import ImportPanel from './src/components/ImportPanel';
import ReviewPanel from './src/components/ReviewPanel';
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const evidence = {request:{from_block:1,to_block:2},metadata:{},coverage:{},transfers:[]};
const imported = {validation:{verified:true},manifest:{files:{}},manifest_hash:'a'.repeat(64),evidence,report:{case_id:'case-A',revision:1,conclusion:{summary:'Imported report'}},registry_verification:'unknown'};
const record = (head=1, id='case-A') => ({id,evidence,agent:null,status:'completed',report:{conclusion:{summary:'Server summary '+head,claims:[{text:'Server claim '+head,evidence_ids:[]}]}},versions:Array.from({length:head},(_,i)=>({version_id:i+1,parent_version_id:i,author:'local-author',content_hash:String(i+1).repeat(64)})),reviews:[],registry:{head_version_id:head}});
window.importCalls=[]; window.versionCalls=[]; window.reviewCalls=[]; window.resetCount=0; window.record=record;
function ImportHarness(){
 const [value,setValue]=useState(imported); const [generation]=useState(()=>({current:0}));
 return <ImportPanel imported={value} onReset={()=>{generation.current++;window.resetCount++;setValue(null);}} onImport={async(file)=>{
  const current=++generation.current; const pending=deferred(); window.importCalls.push({file:file.name,...pending}); await pending.promise;
  if(current===generation.current)setValue({...imported,report:{...imported.report,conclusion:{summary:file.name}}});
 }}/>;
}
function ReviewHarness(){
 const [value,setValue]=useState(record());window.setInvestigation=setValue;
 return <ReviewPanel investigation={value} reviewVersionId={1} onReview={async(...args)=>{const pending=deferred();window.reviewCalls.push({args,...pending});await pending.promise;}}
 onVersion={async(...args)=>{const pending=deferred();window.versionCalls.push({args,...pending});await pending.promise;}}/>;
}
createRoot(document.getElementById('import')).render(<ImportHarness/>);
createRoot(document.getElementById('review')).render(<ReviewHarness/>);
`;

const browserChecks = String.raw`
import asyncio, json, sys
from playwright.async_api import async_playwright, expect

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(sys.argv[1])
        imports = page.locator("#import")
        upload = imports.get_by_label("选择公开证据 ZIP 文件", exact=True)
        await expect(imports.get_by_text("文件哈希与 manifest 校验通过", exact=True)).to_be_visible()
        await upload.set_input_files({"name":"first.zip","mimeType":"application/zip","buffer":b"first"})
        await expect(imports.get_by_text("文件哈希与 manifest 校验通过", exact=True)).to_have_count(0)
        assert await page.evaluate("window.resetCount") == 1
        await imports.locator("form").evaluate("form=>{form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));}")
        assert await page.evaluate("window.importCalls.length") == 1, "same-turn double submit must be guarded"
        await upload.set_input_files({"name":"second.zip","mimeType":"application/zip","buffer":b"second"})
        await imports.get_by_role("button",name="验证并导入",exact=True).click()
        assert await page.evaluate("window.importCalls.length") == 2
        await page.evaluate("window.importCalls[0].reject(new Error('stale import failure'))")
        await expect(imports.get_by_role("button",name="验证中…",exact=True)).to_be_disabled()
        await expect(imports.get_by_text("stale import failure",exact=True)).to_have_count(0)
        await page.evaluate("window.importCalls[1].resolve()")
        await expect(imports.get_by_text("文件哈希与 manifest 校验通过",exact=True)).to_be_visible()
        await imports.get_by_role("button",name="验证并导入",exact=True).click()
        assert await page.evaluate("window.importCalls.length") == 3, "same file can be submitted again"
        await page.evaluate("window.importCalls[2].reject(new Error('current import failed'))")
        await expect(imports.get_by_role("alert")).to_have_text("current import failed")
        await upload.set_input_files({"name":"second.zip","mimeType":"application/zip","buffer":b"second"})
        assert await page.evaluate("window.resetCount") == 3, "same file can be selected again"
        await expect(imports.get_by_role("alert")).to_have_count(0)
        print("PASS import reset, duplicate guard, file replacement, stale error, current error and same-file retry")

        review = page.locator("#review")
        summary = review.get_by_label("修订后的摘要",exact=True)
        reason = review.get_by_role("textbox",name="更正说明",exact=True)
        await summary.fill("My correction draft")
        await reason.fill("Evidence-backed correction")
        await page.evaluate("window.setInvestigation(window.record(2))")
        await expect(summary).to_have_value("My correction draft")
        await expect(reason).to_have_value("Evidence-backed correction")
        await expect(review.get_by_role("combobox",name="父版本",exact=True)).to_have_value("1")
        await expect(review.get_by_role("button",name="创建 v3",exact=True)).to_be_disabled()
        await expect(review.get_by_test_id("review-version-binding")).to_contain_text("复核绑定 v1")
        await review.get_by_role("button",name="采用当前版本，保留草稿",exact=True).click()
        await expect(review.get_by_role("combobox",name="父版本",exact=True)).to_have_value("2")
        await expect(summary).to_have_value("My correction draft")
        version_form = review.locator("form").nth(1)
        await version_form.evaluate("form=>{form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));}")
        assert await page.evaluate("window.versionCalls.length") == 1
        assert await page.evaluate("window.versionCalls[0].args[2]") == 2
        await page.evaluate("window.setInvestigation(window.record(3));window.versionCalls[0].reject(new Error('HTTP 409: parent version changed'))")
        await expect(summary).to_have_value("My correction draft")
        await expect(reason).to_have_value("Evidence-backed correction")
        await expect(review.get_by_role("alert")).to_have_text("HTTP 409: parent version changed")
        await review.get_by_role("button",name="采用当前版本，保留草稿",exact=True).click()
        await expect(review.get_by_role("alert")).to_have_text("HTTP 409: parent version changed")
        await expect(review.get_by_role("combobox",name="父版本",exact=True)).to_have_value("3")
        await review.get_by_role("button",name="创建 v4",exact=True).click()
        await page.evaluate("window.setInvestigation(window.record(4));window.versionCalls[1].resolve()")
        await expect(summary).to_have_value("Server summary 4")
        await expect(reason).to_have_value("")
        await expect(review.get_by_test_id("correction-baseline")).to_contain_text("草稿基线 v4")
        print("PASS version binding, head-change draft preservation, explicit adoption, duplicate guard, conflict and success reset")

        await summary.fill("Case A draft")
        await reason.fill("Case A reason")
        await review.get_by_role("button",name="创建 v5",exact=True).click()
        await page.evaluate("window.setInvestigation(window.record(1,'case-B'))")
        await expect(summary).to_have_value("Server summary 1")
        await summary.fill("Case B draft")
        await page.evaluate("window.versionCalls[2].reject(new Error('old case response'))")
        await expect(summary).to_have_value("Case B draft")
        await expect(review.get_by_text("old case response",exact=True)).to_have_count(0)
        await review.get_by_role("textbox",name="复核意见",exact=True).fill("Review precise version")
        await review.locator("form").first.evaluate("form=>{form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));}")
        assert await page.evaluate("window.reviewCalls.length") == 1
        await page.evaluate("window.reviewCalls[0].resolve()")
        await expect(review.get_by_role("textbox",name="复核意见",exact=True)).to_have_value("")
        assert not errors, json.dumps(errors)
        print("PASS case-switch response isolation and review duplicate guard; no runtime errors")
        await browser.close()

asyncio.run(main())
`;

test("import and version review preserve the active file, version and draft in Chrome", {
  skip: !existsSync(python) ? "Set CLUETIDE_TEST_PYTHON to a Python runtime with Playwright and installed Chrome." : false,
  timeout: 60000,
}, async () => {
  const output = await build({ stdin: { contents: harness, resolveDir: frontend, loader: "tsx" }, bundle: true, write: false, format: "iife", jsx: "automatic" });
  const script = output.outputFiles[0].text;
  const server = createServer((request, response) => {
    response.setHeader("Content-Type", request.url === "/app.js" ? "text/javascript; charset=utf-8" : "text/html; charset=utf-8");
    response.end(request.url === "/app.js" ? script : '<!doctype html><meta charset="utf-8"><div id="import"></div><div id="review"></div><script src="/app.js"></script>');
  });
  await new Promise((yes) => server.listen(0, "127.0.0.1", yes));
  try {
    const address = `http://127.0.0.1:${server.address().port}`;
    const result = await new Promise((yes, no) => {
      const child = spawn(python, ["-I", "-X", "utf8", "-c", browserChecks, address], { windowsHide: true });
      let output = "";
      child.stdout.on("data", (data) => output += data);
      child.stderr.on("data", (data) => output += data);
      child.on("error", no);
      child.on("close", (code) => yes({ code, output }));
    });
    assert.equal(result.code, 0, result.output);
    console.log(result.output.trim());
  } finally {
    await new Promise((yes, no) => server.close((error) => error ? no(error) : yes()));
  }
});
