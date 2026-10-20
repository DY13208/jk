import http from 'node:http';
import fs from 'node:fs';
import {fork} from 'node:child_process';
import {timingSafeEqual} from 'node:crypto';
import QRCode from 'qrcode';
import {listIndexedWeixinAccountIds,loadWeixinAccount,getUpdates,sendMessageWeixin,sendImageMessageWeixin,uploadFileToWeixin,notifyStart} from './official.mjs';
import {usable,acceptMessages} from './core.mjs';
import {deliverMedia,decodeImage} from './media.mjs';

process.umask(0o077);
const stateFile='/state/monitor-session.json';
let state={};try{state=JSON.parse(fs.readFileSync(stateFile,'utf8'));}catch{}
const save=()=>{fs.writeFileSync(stateFile+'.tmp',JSON.stringify(state),{mode:0o600});fs.renameSync(stateFile+'.tmp',stateFile);};
let login=null,auth={state:'idle'},sending=false,online=false,notified=null,polling=false;
const account=()=>{
  const id=listIndexedWeixinAccountIds().at(-1);
  const data=id?loadWeixinAccount(id):null;
  if(!data?.token||!data.userId)return null;
  const url=new URL(data.baseUrl||'https://ilinkai.weixin.qq.com');
  if(url.protocol!=='https:'||!url.hostname.endsWith('.weixin.qq.com'))return null;
  if(state.account!==id){state={account:id};save();}
  return {...data,id,baseUrl:url.origin};
};
async function qr(url){
  try{
    const parsed=new URL(url);
    if(parsed.protocol!=='https:'||parsed.hostname!=='liteapp.weixin.qq.com')return;
    const png=await QRCode.toDataURL(url,{width:240,margin:2});
    if(login)auth={state:'waiting',qr:png,expires:Date.now()+240000};
  }catch{}
}
function startLogin(){
  if(login)return;
  auth={state:'starting'};
  login=fork('/bridge/login.mjs',[],{stdio:['pipe','pipe','ignore','ipc']});
  const child=login;
  child.on('message',async m=>{
    if(m.qr)await qr(m.qr);
    if(m.done){auth={state:'done'};state={};save();online=false;notified=null;}
    if(m.failed)auth={state:'failed'};
  });
  let tail='';
  child.stdout.on('data',chunk=>{
    tail=(tail+chunk.toString()).slice(-20000);
    const urls=tail.match(/https:\/\/liteapp\.weixin\.qq\.com\/[^\s\u001b]+/g);
    if(urls?.length)void qr(urls.at(-1));
    if(/配对码|验证码|verify.?code/i.test(tail))auth={...auth,verification_required:true};
  });
  child.on('exit',()=>{if(login===child)login=null;if(['starting','waiting'].includes(auth.state))auth={state:'expired'};});
  child.on('error',()=>{login=null;auth={state:'failed'};});
  setTimeout(()=>{if(login===child){child.kill();auth={state:'expired'};}},260000).unref();
}
async function receive(){
  if(polling||login)return;
  const a=account();if(!a)return;
  polling=true;
  try{
    if(notified!==a.id){await notifyStart(a);notified=a.id;}
    const response=await getUpdates({...a,get_updates_buf:state.cursor||'',timeoutMs:35000});
    if(login||account()?.id!==a.id)return;
    if(response.ret && response.ret!==0){online=false;return;}
    acceptMessages(state,response,a.userId);save();online=true;
  }catch{online=false;}
  finally{polling=false;}
}
setInterval(()=>void receive(),1500);

http.createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
  const reply=(code,data)=>{res.writeHead(code);res.end(JSON.stringify(data));};
  try{
    const wanted=fs.readFileSync('/run/bridge.token','utf8').trim();
    const supplied=String(req.headers['x-weixin-token']||'');
    if(!wanted||Buffer.byteLength(supplied)!==Buffer.byteLength(wanted)||!timingSafeEqual(Buffer.from(supplied),Buffer.from(wanted)))return reply(403,{error:'unauthorized'});
    if(req.method==='GET'&&req.url==='/status'){
      const a=account();return reply(200,{running:true,bound:Boolean(a),ready:Boolean(a)&&online&&usable(state)&&!login,online,auth,recipient:a?.userId||'',
        remaining:Math.max(0,9-(state.used||0)),message:!a?'请扫码绑定微信':!online?'正在连接微信，尚未验证会话':!usable(state)?'请给微信 ClawBot 发一句话以激活或续期':'会话可用于发送，尚不代表新通知已送达'});
    }
    if(req.method!=='POST')return reply(404,{error:'not found'});
    let text='';for await(const chunk of req){text+=chunk;if(Buffer.byteLength(text)>3000000)return reply(413,{error:'too large'});}
    const body=JSON.parse(text||'{}');
    if(req.url==='/login'){if(sending)return reply(409,{error:'busy'});startLogin();return reply(200,{ok:true});}
    if(req.url==='/cancel'){login?.kill();login=null;auth={state:'cancelled'};return reply(200,{ok:true});}
    if(req.url==='/verify'){
      if(!login||!/^\d{4,12}$/.test(body.code||''))return reply(400,{error:'invalid code'});
      login.stdin.write(body.code+'\n');auth.verification_required=false;return reply(200,{ok:true});
    }
    if(req.url==='/send'){
      if(sending||login)return reply(409,{error:'busy'});
      const a=account();if(!a||!online||!usable(state))return reply(409,{error:'not ready'});
      if(typeof body.text!=='string'||!body.text.trim()||body.text.length>3500)return reply(400,{error:'invalid text'});
      try{decodeImage(body.image);}catch{return reply(400,{error:'invalid image'});}
      sending=true;
      try{
        const opts={...a,contextToken:state.context,timeoutMs:15000};
        const result=await deliverMedia({text:body.text,image:body.image,
          reserve:()=>{state.used=(state.used||0)+1;save();},canSendImage:()=>usable(state),
          sendText:text=>sendMessageWeixin({to:a.userId,text,opts}),
          sendImage:async filePath=>{
            const uploaded=await uploadFileToWeixin({filePath,toUserId:a.userId,opts,cdnBaseUrl:'https://novac2c.cdn.weixin.qq.com/c2c'});
            await sendImageMessageWeixin({to:a.userId,text:'',uploaded,opts,filePath,mediaMime:'image/jpeg'});
          }});
        return reply(200,{...result,recipient:a.userId});
      }catch{state.context=null;save();return reply(502,{error:'delivery uncertain'});}
      finally{sending=false;}
    }
    return reply(404,{error:'not found'});
  }catch{if(!res.headersSent)reply(503,{error:'bridge unavailable'});else res.end();}
}).listen(8091,'0.0.0.0',()=>console.log('Weixin notification bridge listening (no AI agent, no automatic replies)'));
