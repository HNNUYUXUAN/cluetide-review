import test from 'node:test';import assert from 'node:assert/strict';
import {publicationStatus} from '../src/release/publication-status.ts';
test('a recorded failed receipt remains failed while its signature and chain are valid',()=>{
  assert.equal(publicationStatus({result:2,artifactMatches:false,chainMatches:true,archived:false},true,true),'Execution failed · receipt verified');
  assert.equal(publicationStatus({result:2,artifactMatches:false,chainMatches:false,archived:false},false,true),'Execution failed · receipt awaiting registration');
});
test('publication completion requires output evidence and receipt commitment',()=>{
  assert.equal(publicationStatus({result:1,artifactMatches:true,chainMatches:false,archived:false},false,true),'Published, receipt awaiting registration');
  assert.equal(publicationStatus({result:1,artifactMatches:true,chainMatches:true,archived:false},true,true),'Publication verified');
  assert.equal(publicationStatus(undefined,false,true,true),'Publication result pending');
  assert.equal(publicationStatus(undefined,false,true),'Start authorized');
});
test('an unavailable or conflicting chain observation does not imply absent registration',()=>{
  assert.equal(publicationStatus({result:1,artifactMatches:true,chainMatches:false,archived:false,chainStatus:'unavailable'},true,true),'Output verified · current chain check unavailable');
  assert.equal(publicationStatus({result:2,artifactMatches:false,chainMatches:false,archived:false,chainStatus:'unavailable'},true,true),'Execution failed · current chain check unavailable');
  assert.equal(publicationStatus({result:1,artifactMatches:true,chainMatches:false,archived:false,chainStatus:'mismatch'},true,true),'Receipt commitment differs · inspect the attempt');
});
