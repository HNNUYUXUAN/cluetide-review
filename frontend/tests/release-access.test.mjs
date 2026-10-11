import test from "node:test";
import assert from "node:assert/strict";
import {api} from "../src/release/api.ts";
import {taskAccess} from "../src/release/task-access.ts";

const key="test-package-capability-"+"a".repeat(32);
const taskId=n=>"0x"+n.toString(16).padStart(64,"0");
function setup(t,fetcher){
  const previousFetch=globalThis.fetch,previousStorage=Object.getOwnPropertyDescriptor(globalThis,"localStorage");
  const values=new Map();
  Object.defineProperty(globalThis,"localStorage",{configurable:true,value:{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}});
  globalThis.fetch=fetcher;
  t.after(()=>{globalThis.fetch=previousFetch;if(previousStorage)Object.defineProperty(globalThis,"localStorage",previousStorage);else delete globalThis.localStorage;});
  return values;
}
const response=value=>new Response(JSON.stringify(value),{status:201,headers:{"Content-Type":"application/json"}});

test("creation retains package access privately and returns public task fields",async t=>{
  const id=taskId(101),values=setup(t,async()=>response({task_id:id,title:"Build",write_key:key}));
  const task=await api.create({title:"Build"});
  assert.deepEqual(task,{task_id:id,title:"Build"});assert.equal(taskAccess.key(id),key);
  assert.equal(taskAccess.persistent(id),true);assert.equal([...values.values()].filter(v=>v===key).length,1);
});

test("only the matching task package POST carries its write capability",async t=>{
  const id=taskId(102),calls=[];setup(t,async(path,options)=>{calls.push({path,options});return response({version_id:1});});
  taskAccess.save(id,key);await api.version(id,{summary:"Build"});await api.task(id);
  assert.equal(calls[0].options.headers["X-ClueTide-Task-Key"],key);assert.equal(calls[1].options.headers,undefined);
  assert.equal(calls[0].options.body.includes(key),false);
});

test("another task's capability cannot authorize a package request",async t=>{
  let requests=0;setup(t,async()=>{requests++;return response({});});taskAccess.save(taskId(103),key);
  assert.throws(()=>api.version(taskId(104),{}),/creator/);assert.equal(requests,0);
});

test("blocked storage prevents task creation before network mutation",async t=>{
  let requests=0;setup(t,async()=>{requests++;return response({});});
  globalThis.localStorage.setItem=()=>{throw new Error("Blocked");};
  await assert.rejects(api.create({title:"Build"}),/Enable local storage/);assert.equal(requests,0);
});

test("a storage failure after creation retains current-tab access and reports persistence accurately",async t=>{
  const id=taskId(105);setup(t,async()=>{globalThis.localStorage.setItem=()=>{throw new Error("Quota");};return response({task_id:id,write_key:key});});
  await api.create({});assert.equal(taskAccess.key(id),key);assert.equal(taskAccess.persistent(id),false);
});

test("rejected package access is removed and later package attempts are blocked locally",async t=>{
  const id=taskId(106);let requests=0;const values=setup(t,async()=>{requests++;return new Response(JSON.stringify({detail:"A valid task write key is required to add a release package."}),{status:403});});
  taskAccess.save(id,key);await assert.rejects(api.version(id,{}),/restore this release/);
  assert.equal(taskAccess.key(id),undefined);assert.equal(taskAccess.persistent(id),false);
  assert.equal([...values.values()].includes(key),false);assert.throws(()=>api.version(id,{}),/creator/);assert.equal(requests,1);
});
