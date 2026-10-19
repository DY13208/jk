import {startWeixinLoginWithQr, waitForWeixinLogin, saveWeixinAccount, registerWeixinAccountId} from './official.mjs';
import {normalizeAccountId} from 'openclaw/plugin-sdk/account-id';
try {
  const start = await startWeixinLoginWithQr({apiBaseUrl:'https://ilinkai.weixin.qq.com',force:true});
  if (!start.qrcodeUrl) throw Error('qr unavailable');
  process.send({qr:start.qrcodeUrl});
  const result = await waitForWeixinLogin({sessionKey:start.sessionKey,apiBaseUrl:'https://ilinkai.weixin.qq.com',timeoutMs:240000});
  if (result.alreadyConnected) process.send({done:true});
  else if (result.connected && result.botToken && result.accountId && result.userId) {
    const url = new URL(result.baseUrl || 'https://ilinkai.weixin.qq.com');
    if (url.protocol !== 'https:' || !(url.hostname === 'weixin.qq.com' || url.hostname.endsWith('.weixin.qq.com'))) throw Error('invalid host');
    const id = normalizeAccountId(result.accountId);
    saveWeixinAccount(id,{token:result.botToken,baseUrl:url.origin,userId:result.userId});
    registerWeixinAccountId(id);
    process.send({done:true});
  } else process.send({failed:true});
} catch { process.send?.({failed:true}); }
process.exit(0);
