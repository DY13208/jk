import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export function decodeImage(image) {
  if(!image)return null;
  if(image.mime!=='image/jpeg'||typeof image.data!=='string'||image.data.length>2800000||!/^[A-Za-z0-9+/]+={0,2}$/.test(image.data))throw Error('invalid image');
  const data=Buffer.from(image.data,'base64');
  if(!data.length||data.length>2*1024*1024||data.toString('base64')!==image.data||data[0]!==0xff||data[1]!==0xd8)throw Error('invalid image');
  return data;
}

export async function deliverMedia({text,image,sendText,sendImage,reserve,canSendImage}) {
  const data=decodeImage(image); // Validate before the first message leaves this process.
  reserve();await sendText(text);
  if(!data)return {ok:true,image_status:'none'};
  if(!canSendImage())return {ok:true,image_status:'skipped'};
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'catalog-image-'));
  try {
    const file=path.join(dir,'product.jpg');fs.writeFileSync(file,data,{mode:0o600});
    reserve();await sendImage(file);
    return {ok:true,image_status:'accepted'};
  }catch{return {ok:true,image_status:'uncertain'};}
  finally{fs.rmSync(dir,{recursive:true,force:true});}
}
