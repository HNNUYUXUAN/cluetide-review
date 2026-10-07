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
import SavedVersions from './src/gcc/SavedVersions';
const deferred = () => {let resolve; const promise = new Promise(yes => {resolve=yes}); return {promise,resolve};};
const ids = [41,73];
const hash = id => String(id).repeat(32);
const evidence = {request:{from_block:1,to_block:2},metadata:{},coverage:{},transfers:[]};
const record = count => ({id:'saved-case',evidence,agent:null,status:'completed',report:{conclusion:{summary:'Live working summary',claims:[]}},versions:ids.slice(0,count).map((id,index)=>({version_id:id,parent_version_id:ids[index-1]??0,author:'local-author',content_hash:hash(id)})),reviews:[],registry:{head_version_id:ids[count-1]??0}});
const imported = id => ({validation:{verified:true},manifest:{files:{}},manifest_hash:hash(id),evidence,report:{case_id:'saved-case',revision:ids.indexOf(id)+1,conclusion:{summary:'Saved original report '+id,claims:[]}},registry_verification:'unknown'});
window.bundleCalls=[];window.importCalls=[];window.reviewCalls=[];window.refreshCount=0;window.record=record;
window.fetch = (url,options={}) => {
 if(String(url).startsWith('/api/investigations/saved-case/bundle?version=')) {
  const pending=deferred(); window.bundleCalls.push({url:String(url),signal:options.signal,...pending}); return pending.promise;
 }
 if(url==='/api/bundles/import') {
  const pending=deferred(); window.importCalls.push({file:options.body.get('file'),...pending}); return pending.promise;
 }
 if(url==='/api/investigations/saved-case/reviews') {
  window.reviewCalls.push(JSON.parse(options.body)); return Promise.resolve(Response.json(record(2)));
 }
 throw new Error('Unexpected request: '+url);
};
window.resolveBundle = index => window.bundleCalls[index].resolve(new Response('bundle bytes '+index,{headers:{'Content-Type':'application/zip'}}));
window.resolveImport = (index,id) => window.importCalls[index].resolve(Response.json(imported(id)));
function Harness(){
 const [investigation,setInvestigation]=useState(record(0));window.setInvestigation=setInvestigation;
 return <SavedVersions investigation={investigation} client="primary" refresh={async()=>{window.refreshCount++}}/>;
}
createRoot(document.getElementById('root')).render(<Harness/>);
`;

const browserChecks = String.raw`
import asyncio, json, sys
from playwright.async_api import async_playwright, expect

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(sys.argv[1])
        picker = page.get_by_role("combobox", name="核读版本", exact=True)
        report = page.locator(".gcc-version-report")
        binding = page.get_by_test_id("review-version-binding")
        await expect(page.get_by_role("heading", name="先准备一份可复核的报告", exact=True)).to_be_visible()
        assert await page.evaluate("window.bundleCalls.length") == 0
        await page.evaluate("window.setInvestigation(window.record(1))")
        await expect(picker).to_have_value("41")
        await page.wait_for_function("window.bundleCalls.length===1")
        assert await page.evaluate("window.bundleCalls[0].url") == "/api/investigations/saved-case/bundle?version=41"
        await expect(binding).to_contain_text("复核绑定 v1")
        await expect(report).to_have_count(0)
        await page.evaluate("window.resolveBundle(0)")
        await page.wait_for_function("window.importCalls.length===1")
        assert await page.evaluate("window.importCalls[0].file.name") == "case-41.zip"
        assert await page.evaluate("window.importCalls[0].file.text()") == "bundle bytes 0"
        await page.evaluate("window.resolveImport(0,41)")
        await expect(report).to_contain_text("Saved original report 41")
        await expect(report).not_to_contain_text("Live working summary")
        print("PASS empty initial versions select the first saved version and fetch/import its historical report")

        await page.evaluate("window.setInvestigation(window.record(2))")
        await expect(picker).to_have_value("41")
        await expect(report).to_contain_text("Saved original report 41")
        review = page.get_by_role("textbox", name="复核意见", exact=True)
        submit = page.get_by_role("button", name="记录复核", exact=True)
        await review.fill("Review the selected version")
        await picker.select_option("73")
        await expect(report).to_have_count(0)
        await expect(binding).to_contain_text("复核绑定 v2")
        await expect(binding).to_contain_text("73" * 32)
        await submit.click()
        await expect(page.get_by_role("alert")).to_have_text("请等待所选版本读取完成。")
        assert await page.evaluate("window.reviewCalls.length") == 0, "an old bundle cannot authorize review of a newly selected version"
        await page.wait_for_function("window.bundleCalls.length===2")
        assert await page.evaluate("window.bundleCalls[1].url") == "/api/investigations/saved-case/bundle?version=73"
        await page.evaluate("window.resolveBundle(1)")
        await page.wait_for_function("window.importCalls.length===2")
        await page.evaluate("window.resolveImport(1,73)")
        await expect(report).to_contain_text("Saved original report 73")
        await submit.click()
        await expect(review).to_have_value("")
        assert await page.evaluate("window.reviewCalls[0].version_id") == 73
        assert await page.evaluate("window.refreshCount") == 1
        print("PASS version switches hide the old report immediately and review binds only after the selected bundle loads")

        await picker.select_option("41")
        await expect(report).to_have_count(0)
        await page.wait_for_function("window.bundleCalls.length===3")
        await page.evaluate("window.resolveBundle(2)")
        await page.wait_for_function("window.importCalls.length===3")
        await picker.select_option("73")
        await expect(report).to_have_count(0)
        await page.wait_for_function("window.bundleCalls.length===4")
        assert await page.evaluate("window.bundleCalls[2].signal.aborted"), "superseded bundle fetch is aborted"
        await page.evaluate("window.resolveBundle(3)")
        await page.wait_for_function("window.importCalls.length===4")
        await page.evaluate("window.resolveImport(3,73)")
        await expect(report).to_contain_text("Saved original report 73")
        await page.evaluate("window.resolveImport(2,41)")
        await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        await expect(report).to_contain_text("Saved original report 73")
        await expect(report).not_to_contain_text("Saved original report 41")
        await expect(binding).to_contain_text("复核绑定 v2")
        assert await page.evaluate("window.reviewCalls.length") == 1
        assert not errors, json.dumps(errors)
        print("PASS a late v1 import cannot replace the selected v2 report; no runtime errors")
        await browser.close()

asyncio.run(main())
`;

test("saved versions select asynchronously available reports and isolate version loads in Chrome", {
  skip: !existsSync(python) ? "Set CLUETIDE_TEST_PYTHON to a Python runtime with Playwright and installed Chrome." : false,
  timeout: 60000,
}, async () => {
  const output = await build({ stdin: { contents: harness, resolveDir: frontend, loader: "tsx" }, bundle: true, write: false, format: "iife", jsx: "automatic" });
  const script = output.outputFiles[0].text;
  const server = createServer((request, response) => {
    response.setHeader("Content-Type", request.url === "/app.js" ? "text/javascript; charset=utf-8" : "text/html; charset=utf-8");
    response.end(request.url === "/app.js" ? script : '<!doctype html><meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>');
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
