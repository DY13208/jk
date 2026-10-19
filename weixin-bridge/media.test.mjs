import assert from 'node:assert/strict';
import fs from 'node:fs';
import {decodeImage,deliverMedia} from './media.mjs';
const image={mime:'image/jpeg',data:Buffer.from([255,216,255,217]).toString('base64')};
assert.throws(()=>decodeImage({mime:'image/svg+xml',data:'bad'}));
assert.throws(()=>decodeImage({mime:'image/jpeg',data:'aGVsbG8='}));
for(const failure of [false,true]){
  const calls=[];let reserved=0,file;
  const result=await deliverMedia({text:'商品测试',image,reserve:()=>reserved++,canSendImage:()=>true,
    sendText:async text=>{calls.push('text');},sendImage:async value=>{file=value;assert.ok(fs.existsSync(value));calls.push('image');if(failure)throw Error('failed');}});
  assert.deepEqual(calls,['text','image']);assert.equal(reserved,2);
  assert.equal(result.ok,true);assert.equal(result.image_status,failure?'uncertain':'accepted');
  assert.equal(fs.existsSync(file),false);
}
let sent=0;
const result=await deliverMedia({text:'text only',image,reserve:()=>{},canSendImage:()=>false,sendText:async()=>sent++,sendImage:async()=>{throw Error('must not call');}});
assert.equal(sent,1);assert.equal(result.image_status,'skipped');
await assert.rejects(()=>deliverMedia({text:'test',image:{mime:'bad'},reserve:()=>{},sendText:async()=>{throw Error('must not send');}}),/invalid image/);
console.log('PASS: text then image, quota reservation, invalid attachment rejection, partial-image failure without text resend, temporary-file cleanup');
