#!/usr/bin/env node
/* Local-only key custody. Never print Yahoo URLs, callbacks, cookies or secrets. */
import {constants, createDecipheriv, createHash, generateKeyPairSync, privateDecrypt, randomBytes, timingSafeEqual} from 'node:crypto';
import {spawn} from 'node:child_process';
import {access, mkdir, mkdtemp, open, rename, unlink, rmdir} from 'node:fs/promises';
import {homedir} from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

export const SITE_ORIGIN='https://fantasy.piamamba.com';
const YAHOO_AUTH='https://api.login.yahoo.com/oauth2/request_auth';
const CALLBACK=SITE_ORIGIN+'/yahoo/callback';
const MAX_BODY=32768;
export class PhoneAuthError extends Error {}
const fail=message=>{throw new PhoneAuthError(message);};

export function validateConfig(input, environment=process.env) {
  if(!input||typeof input!=='object'||Array.isArray(input))fail('手機授權設定格式不正確。');
  const result={};
  for(const key of ['python','collector','envFile','finalTokenFile']) {
    const value=input[key];
    if(typeof value!=='string'||!value||value.length>4096||/[\0\r\n]/.test(value)||!path.isAbsolute(value))fail('Python、程式與設定檔必須提供完整本機路徑。');
    result[key]=value;
  }
  if(typeof input.expectedTeamKey!=='string'||input.expectedTeamKey.length>100||!/^\d+\.l\.\d+\.t\.\d+$/.test(input.expectedTeamKey))fail('請提供要驗證的 Yahoo 球隊代碼。');
  const tokenRelative=path.relative(path.dirname(result.collector),result.finalTokenFile);
  if(!tokenRelative.startsWith('..'+path.sep)&&!path.isAbsolute(tokenRelative))fail('正式 token 必須保存在程式目錄之外的私人位置。');
  if([result.python,result.collector,result.envFile].some(value=>path.resolve(value)===path.resolve(result.finalTokenFile)))fail('正式 token 路徑不得取代程式或設定檔。');
  if(input.siteOrigin!==SITE_ORIGIN)fail('手機授權只接受指定的 HTTPS 網站。');
  const password=input.sitePassword??environment.FANTASY_SITE_PASSWORD;
  if(typeof password!=='string'||!password||password.length>1024||/[\0\r\n]/.test(password))fail('請透過標準輸入或環境提供網站密碼。');
  return {...result,expectedTeamKey:input.expectedTeamKey,siteOrigin:SITE_ORIGIN,sitePassword:password};
}

export function validateAuthorizationUrl(value) {
  if(typeof value!=='string'||value.length>8192||/[\x00-\x20\x7f]/.test(value))fail('本機程式傳回的授權網址不正確。');
  let url;try{url=new URL(value);}catch{fail('本機程式傳回的授權網址不正確。');}
  if(url.origin+url.pathname!==YAHOO_AUTH||url.username||url.password||url.hash)fail('Yahoo 授權網址不符合允許的位址。');
  const allowed=new Set(['client_id','redirect_uri','response_type','state','scope']);
  for(const key of url.searchParams.keys())if(!allowed.has(key)||url.searchParams.getAll(key).length!==1)fail('Yahoo 授權參數不正確。');
  const client=url.searchParams.get('client_id'),state=url.searchParams.get('state');
  if(!client||client.length>4096||/[\x00-\x20\x7f]/.test(client)||url.searchParams.get('response_type')!=='code'||url.searchParams.get('redirect_uri')!==CALLBACK||!/^[A-Za-z0-9_-]{32,128}$/.test(state||''))fail('Yahoo 授權參數或 HTTPS callback 不正確。');
  if(url.searchParams.has('scope')&&url.searchParams.get('scope')!=='fspt-r')fail('Yahoo 授權只能要求 Fantasy 讀取權限。');
  return {authorizationUrl:value,state,redirectUri:CALLBACK};
}

function base64(value, maxBytes, exact) {
  if(typeof value!=='string'||value.length>Math.ceil(maxBytes/3)*4||!/^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))fail('網站的加密回傳格式不正確。');
  const bytes=Buffer.from(value,'base64');
  if(bytes.length>maxBytes||(exact!==undefined&&bytes.length!==exact)||bytes.toString('base64')!==value)fail('網站的加密回傳長度不正確。');
  return bytes;
}

export function decryptCallback(envelope, privateKey, expectedState) {
  if(!envelope||typeof envelope!=='object'||Array.isArray(envelope)||envelope.v!==1)fail('網站的加密回傳版本不正確。');
  let aesKey,plaintext;
  try {
    const wrapped=base64(envelope.key,256,256),iv=base64(envelope.iv,12,12),data=base64(envelope.data,16384);
    if(data.length<17)fail('網站的加密回傳長度不正確。');
    aesKey=privateDecrypt({key:privateKey,padding:constants.RSA_PKCS1_OAEP_PADDING,oaepHash:'sha256'},wrapped);
    if(aesKey.length!==32)fail('網站的加密金鑰長度不正確。');
    const decipher=createDecipheriv('aes-256-gcm',aesKey,iv);
    decipher.setAuthTag(data.subarray(-16));
    plaintext=Buffer.concat([decipher.update(data.subarray(0,-16)),decipher.final()]);
    const decoded=new TextDecoder('utf-8',{fatal:true}).decode(plaintext);
    const payload=JSON.parse(decoded);
    if(!payload||typeof payload!=='object'||Array.isArray(payload)||Object.keys(payload).some(k=>!['code','error','state'].includes(k)))fail('授權回傳內容不正確。');
    if(typeof payload.state!=='string'||!/^[A-Za-z0-9_-]{32,128}$/.test(payload.state))fail('OAuth state 不符，未交付授權碼。');
    const actual=Buffer.from(payload.state),expected=Buffer.from(expectedState);
    if(actual.length!==expected.length||!timingSafeEqual(actual,expected))fail('OAuth state 不符，未交付授權碼。');
    const hasCode=Object.hasOwn(payload,'code'),hasError=Object.hasOwn(payload,'error');
    if(hasCode===hasError)fail('授權回傳必須包含單一結果。');
    const field=hasCode?'code':'error',value=payload[field];
    if(typeof value!=='string'||!value||value.length>4096||/[\x00-\x20\x7f]/.test(value))fail('授權回傳內容不正確。');
    const callback=new URL(CALLBACK);callback.searchParams.set(field,value);callback.searchParams.set('state',payload.state);
    if(Buffer.byteLength(callback.href)>8192)fail('授權回傳超過本機接收上限。');
    return callback.href;
  } catch(error) {
    if(error instanceof PhoneAuthError)throw error;
    fail('加密授權回傳無法驗證，未交付授權碼。');
  } finally {aesKey?.fill(0);plaintext?.fill(0);}
}

async function jsonBody(response,maxBytes=MAX_BODY) {
  const reader=response.body?.getReader();if(!reader)fail('網站回應格式不正確。');
  let length=0;const chunks=[];
  try {
    for(;;){const {done,value}=await reader.read();if(done)break;length+=value.length;if(length>maxBytes)fail('網站回應超過允許大小。');chunks.push(value);}
    const value=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(!value||typeof value!=='object'||Array.isArray(value))fail('網站回應格式不正確。');
    return value;
  } catch(error) {if(error instanceof PhoneAuthError)throw error;fail('網站回應格式不正確。');}
  finally {await reader.cancel().catch(()=>{});}
}

// Follow only Yahoo's documented collection/resource wrappers, not arbitrary
// nested objects that might merely quote the requested team key.
function resources(value,name) {
  const found=[];
  const walk=item=>{if(Array.isArray(item)){item.forEach(walk);return;}if(!item||typeof item!=='object')return;
    if(Object.hasOwn(item,name))found.push(item[name]);
    for(const [key,child]of Object.entries(item))if(/^\d+$/.test(key))walk(child);
  };walk(value);return found;
}
function field(resource,name) {
  const values=[];
  const walk=item=>{if(Array.isArray(item)){item.forEach(walk);return;}if(!item||typeof item!=='object')return;
    if(Object.hasOwn(item,name))values.push(item[name]);
    for(const [key,child]of Object.entries(item))if(/^\d+$/.test(key))walk(child);
  };walk(resource);return values;
}
export function ownsExpectedTeam(payload,teamKey) {
  if(!payload||payload.error||!payload.fantasy_content||payload.fantasy_content.error)return false;
  const gameKey=teamKey.split('.')[0];let found=false;
  for(const users of resources(payload.fantasy_content,'users'))for(const user of resources(users,'user'))
    for(const games of resources(user,'games'))for(const game of resources(games,'game')) {
      const gameIds=field(game,'game_key');if(gameIds.length!==1||String(gameIds[0])!==gameKey)continue;
      for(const teams of resources(game,'teams'))for(const team of resources(teams,'team')) {
        const keys=field(team,'team_key');if(keys.length!==1||keys[0]!==teamKey)continue;
        if(field(team,'is_owned_by_current_login').some(value=>String(value)!=='1'))return false;
        found=true;
      }
    }
  return found;
}

async function verifyStagedOwner(stagedTokenFile,teamKey,fetchFn,signal) {
  let token;
  try {
    const file=await open(stagedTokenFile,'r');
    try {
      const bytes=Buffer.alloc(16385),{bytesRead}=await file.read(bytes,0,bytes.length,0);
      if(bytesRead>16384)fail('本機暫存 token 超過允許大小。');
      try{token=JSON.parse(bytes.subarray(0,bytesRead).toString('utf8'));}finally{bytes.fill(0);}
    } finally {await file.close();}
    if(typeof token?.access_token!=='string'||!token.access_token||token.access_token.length>8192||/[\r\n\0]/.test(token.access_token))fail('本機暫存 token 無法驗證。');
    const url='https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_keys='+teamKey.split('.')[0]+'/teams?format=json';
    const response=await fetchFn(url,{method:'GET',redirect:'manual',cache:'no-store',headers:{Authorization:'Bearer '+token.access_token},
      signal:signal?AbortSignal.any([signal,AbortSignal.timeout(20000)]):AbortSignal.timeout(20000)});
    if(response.status!==200){await response.body?.cancel();fail('Yahoo 無法確認此登入者持有指定球隊；既有 token 未變更。');}
    const payload=await jsonBody(response,2*1024*1024);
    if(!ownsExpectedTeam(payload,teamKey))fail('此次 Yahoo 帳號未通過指定球隊驗證；既有 token 未變更。');
  } catch(error) {if(error instanceof PhoneAuthError)throw error;fail('Yahoo 球隊所有權驗證未完成；既有 token 未變更。');}
  finally {token=undefined;}
}

function monitorChild(child) {
  let result=null,buffer='',ready=false,resolveUrl,rejectUrl,resolveExit;
  const authorization=new Promise((resolve,reject)=>{resolveUrl=resolve;rejectUrl=reject;});
  const exited=new Promise(resolve=>{resolveExit=resolve;});
  child.stdout.setEncoding('utf8');
  child.stdout.on('data',chunk=>{
    if(ready)return;
    buffer+=chunk;if(buffer.length>16384){ready=true;rejectUrl(new PhoneAuthError('本機授權程式輸出超過上限。'));return;}
    let end;
    while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end).replace(/\r$/,'');buffer=buffer.slice(end+1);
      if(line.startsWith('https://api.login.yahoo.com')){try{const auth=validateAuthorizationUrl(line);ready=true;resolveUrl(auth);}catch(error){ready=true;rejectUrl(error);}return;}}
  });
  child.stderr.on('data',()=>{});
  child.once('error',()=>{result={code:null};if(!ready){ready=true;rejectUrl(new PhoneAuthError('無法啟動本機 Python 授權程式。'));}resolveExit(result);});
  child.once('close',code=>{result={code};if(!ready){ready=true;rejectUrl(new PhoneAuthError('本機授權程式未產生可用網址。'));}resolveExit(result);});
  return {authorization,exited,get result(){return result;}};
}

async function bounded(promise,milliseconds,signal,message) {
  let timer;let abort;
  try {
    return await Promise.race([promise,new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(new PhoneAuthError(message)),milliseconds);
      abort=()=>reject(new PhoneAuthError('手機授權已取消。'));
      if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
    })]);
  } finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}

export async function runPhoneAuth(input, dependencies={}) {
  const config=validateConfig(input,dependencies.environment??process.env);
  const fetchFn=dependencies.fetch??fetch,spawnFn=dependencies.spawn??spawn,now=dependencies.now??Date.now;
  const sleep=dependencies.sleep??((ms,signal)=>delay(ms,undefined,{signal}));
  const output=dependencies.output??(value=>process.stdout.write(value+'\n'));
  const signal=dependencies.signal;
  const stateDir=dependencies.stateDir??path.join(homedir(),'.fantasy-stats');
  let directory,callbackFile,pendingFile,stagedTokenFile,child,monitor,promoted=false;
  const request=async (route,{cookie,body,form=false}={})=>{
    if(signal?.aborted)fail('手機授權已取消。');
    if(!/^\/(?:login|api\/yahoo\/handoffs(?:\/[a-f0-9]{64}\/(?:claim|finish))?)$/.test(route))fail('網站 API 位址不正確。');
    try{return await fetchFn(SITE_ORIGIN+route,{method:'POST',redirect:'manual',cache:'no-store',
      headers:{Origin:SITE_ORIGIN,'Content-Type':form?'application/x-www-form-urlencoded':'application/json',...(cookie?{Cookie:cookie}:{})},
      body:form?new URLSearchParams(body).toString():JSON.stringify(body),
      signal:signal?AbortSignal.any([signal,AbortSignal.timeout(20000)]):AbortSignal.timeout(20000)});
    }catch{fail(signal?.aborted?'手機授權已取消。':'網站連線失敗；本機沒有交付新的授權碼。');}
  };
  try {
    await mkdir(stateDir,{recursive:true,mode:0o700});
    directory=await mkdtemp(path.join(stateDir,'phone-auth-'));
    callbackFile=path.join(directory,randomBytes(16).toString('hex')+'.callback');pendingFile=callbackFile+'.pending';
    await mkdir(path.dirname(config.finalTokenFile),{recursive:true,mode:0o700});
    stagedTokenFile=path.join(path.dirname(config.finalTokenFile),'.'+path.basename(config.finalTokenFile)+'.'+randomBytes(16).toString('hex')+'.staging');
    const childEnv={...process.env,PYTHONUNBUFFERED:'1'};delete childEnv.FANTASY_SITE_PASSWORD;
    child=spawnFn(config.python,[config.collector,'--env-file',config.envFile,'--token-file',stagedTokenFile,'login','--no-browser','--callback-file',callbackFile,'--callback-timeout','1800'],
      {cwd:path.dirname(config.collector),stdio:['ignore','pipe','pipe'],windowsHide:true,shell:false,env:childEnv});
    monitor=monitorChild(child);
    const auth=await bounded(monitor.authorization,30000,signal,'等待本機授權網址逾時。');
    const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048});
    const exported=publicKey.export({format:'jwk'}),publicJwk={kty:'RSA',n:exported.n,e:exported.e,alg:'RSA-OAEP-256'};
    const pickupSecret=randomBytes(32).toString('base64url'),pickupHash=createHash('sha256').update(pickupSecret).digest('hex');
    const login=await request('/login',{body:{password:config.sitePassword},form:true});
    if(![200,204,302,303].includes(login.status))fail('網站密碼驗證失敗，未建立手機交接。');
    const location=login.headers.get('location');
    if(location&&new URL(location,SITE_ORIGIN).origin!==SITE_ORIGIN)fail('網站登入回傳了不允許的轉址。');
    const cookies=login.headers.getSetCookie().map(value=>value.split(';',1)[0]);
    if(!cookies.length||cookies.some(value=>!/^[-A-Za-z0-9_]+=[^;\r\n]+$/.test(value)))fail('網站沒有提供有效登入狀態。');
    await login.body?.cancel();
    const created=await request('/api/yahoo/handoffs',{cookie:cookies.join('; '),body:{authorizationUrl:auth.authorizationUrl,publicKey:publicJwk,pickupHash}});
    if(created.status!==201)fail('網站未建立手機授權交接。');
    const handoff=await jsonBody(created),expires=Date.parse(handoff.expiresAt);
    if(!/^[a-f0-9]{64}$/.test(handoff.id||'')||handoff.connectUrl!==SITE_ORIGIN+'/yahoo/connect/'+handoff.id||typeof handoff.expiresAt!=='string'||!Number.isFinite(expires)||expires<=now()||expires>now()+1801000)fail('網站交接識別或有效期不正確。');
    if(monitor.result)fail('本機授權程式已停止；未交付手機連結。');
    output(handoff.connectUrl);
    let callback;
    for(;;) {
      if(now()>=expires)fail('手機授權交接已到期；請重新開始。');
      if(monitor.result)fail('本機授權程式已停止；不再接收手機回傳。');
      const claimed=await bounded(Promise.race([request('/api/yahoo/handoffs/'+handoff.id+'/claim',{body:{pickupSecret}}),monitor.exited.then(()=>{fail('本機授權程式已停止。');})]),Math.max(1,expires-now()),signal,'手機授權交接已到期。');
      if(claimed.status===200){const payload=await jsonBody(claimed);callback=decryptCallback(payload.envelope,privateKey,auth.state);break;}
      await claimed.body?.cancel();
      if(claimed.status===410)fail('手機授權交接已到期；請重新開始。');
      if(claimed.status!==202)fail('網站無法提供這次手機授權交接。');
      await bounded(Promise.race([sleep(Math.min(5000,Math.max(1,expires-now())),signal),monitor.exited.then(()=>{fail('本機授權程式已停止。');})]),Math.max(1,expires-now()),signal,'手機授權交接已到期。');
    }
    if(monitor.result||now()>=expires)fail('本機授權或手機交接已結束，未交付回傳。');
    const file=await open(pendingFile,'wx',0o600);
    try{await file.writeFile(callback,{encoding:'utf8'});await file.sync();}finally{await file.close();}
    await rename(pendingFile,callbackFile);
    const result=await bounded(monitor.exited,60000,signal,'本機授權交換未在期限內完成。');
    if(result.code!==0)fail('Yahoo 授權未完成或已取消；未清除網站交接，請重新開始。');
    if(signal?.aborted)fail('手機授權已取消；既有 token 未變更。');
    await verifyStagedOwner(stagedTokenFile,config.expectedTeamKey,fetchFn,signal);
    if(signal?.aborted)fail('手機授權已取消；既有 token 未變更。');
    await rename(stagedTokenFile,config.finalTokenFile);promoted=true;
    let finished=false;
    for(let attempt=0;attempt<3&&!finished;attempt++) {
      try{const response=await request('/api/yahoo/handoffs/'+handoff.id+'/finish',{body:{pickupSecret}});finished=[200,204].includes(response.status);await response.body?.cancel();}catch{}
      if(!finished&&attempt<2&&!signal?.aborted)await sleep(5000,signal);
      if(signal?.aborted)break;
    }
    if(!finished)fail('本機授權已完成且球隊已驗證，但網站暫存清理未確認；請勿重複授權。');
    return {completed:true};
  } catch(error) {
    const safe=error instanceof PhoneAuthError?error:new PhoneAuthError('手機授權未完成；本機詳細資料不會寫入輸出。');
    if(promoted)safe.tokenPromoted=true;
    else if(stagedTokenFile)try{await access(stagedTokenFile);safe.stagedTokenFile=stagedTokenFile;}catch{}
    throw safe;
  } finally {
    if(child&&!monitor?.result){child.kill();if(monitor)await bounded(monitor.exited,3000,undefined,'本機程序停止逾時。').catch(()=>{});}
    for(const file of [pendingFile,callbackFile])if(file)await unlink(file).catch(()=>{});
    if(directory)await rmdir(directory).catch(()=>{});
  }
}

async function main() {
  const controller=new AbortController();
  process.once('SIGINT',()=>controller.abort());process.once('SIGTERM',()=>controller.abort());
  try {
    let size=0;const chunks=[];
    for await(const chunk of process.stdin){size+=chunk.length;if(size>16384)fail('手機授權設定超過大小上限。');chunks.push(chunk);}
    let config;try{config=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{fail('請透過標準輸入提供單一 JSON 設定。');}
    await runPhoneAuth(config,{signal:controller.signal});
  } catch(error) {
    const status={error:error instanceof PhoneAuthError?error.message:'手機授權未完成。'};
    if(error instanceof PhoneAuthError&&error.stagedTokenFile)status.stagedTokenFile=error.stagedTokenFile;
    if(error instanceof PhoneAuthError&&error.tokenPromoted)status.tokenPromoted=true;
    process.stderr.write(JSON.stringify(status)+'\n');process.exitCode=1;
  }
}
if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href)await main();
