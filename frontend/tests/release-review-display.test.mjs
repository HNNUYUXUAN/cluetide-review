import test from 'node:test';
import assert from 'node:assert/strict';
import {lineDiff,previewDocument} from '../src/release/review-display.ts';

test('preview emits only inert fixed tags and escaped text from hostile HTML',()=>{
  const source='<h1 onclick="alert(1)">Title</h1><script>fetch("https://bad.test")</script><img src="https://bad.test"><a href="https://bad.test">Link</a><form action="https://bad.test"><input autofocus></form><svg><foreignObject><iframe srcdoc="<script>bad()</script>"></iframe></foreignObject></svg><base href="https://bad.test"><p style="background:url(https://bad.test)">Text</p>';
  const output=previewDocument(source);
  assert.match(output,/<h1>Title<\/h1>/);assert.match(output,/<p>Text<\/p>/);
  assert.doesNotMatch(output,/bad\.test|onclick|<script|<img|<a\b|<form|<input|<svg|<iframe|<base\b/i);
  assert.match(output,/default-src 'none'/);
});
test('line diff retains unchanged lines and exact changed text including CRLF',()=>{
  assert.deepEqual(lineDiff('a\r\nb\r\nc','a\r\nnew\r\nc'),[
    {kind:'same',text:'a\r',oldLine:1,newLine:1},{kind:'removed',text:'b\r',oldLine:2},
    {kind:'added',text:'new\r',newLine:2},{kind:'same',text:'c',oldLine:3,newLine:3}]);
});
test('large line files preserve complete before/after representations with bounded diff work',()=>{
  const a='start\n'+'a\n'.repeat(1500)+'end',b='start\n'+'b\n'.repeat(1500)+'end',diff=lineDiff(a,b);
  assert.equal(diff.filter(l=>l.kind!=='added').map(l=>l.text).join('\n'),a);
  assert.equal(diff.filter(l=>l.kind!=='removed').map(l=>l.text).join('\n'),b);
});
