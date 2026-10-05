import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {mkdtemp, readdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {constants, createCipheriv, createHash, createPublicKey, generateKeyPairSync, publicEncrypt, randomBytes} from 'node:crypto';
import {SITE_ORIGIN, PhoneAuthError, validateConfig, validateAuthorizationUrl, decryptCallback, ownsExpectedTeam, runPhoneAuth} from '../scripts/yahoo-phone-auth.mjs';

const state='SYNTHETIC_STATE_12345678901234567890123456789012';
const callback=SITE_ORIGIN+'/yahoo/callback';
const authorizationUrl='https://api.login.yahoo.com/oauth2/request_auth?'+new URLSearchParams({client_id:'SYNTHETIC_PUBLIC_CLIENT',redirect_uri:callback,response_type:'code',state});
const id='a'.repeat(64);
const teamKey='466.l.170945.t.16';
const config={python:path.resolve('SYNTHETIC-python.exe'),collector:path.resolve('SYNTHETIC-fetch_stats.py'),envFile:path.resolve('SYNTHETIC-absent.env'),siteOrigin:SITE_ORIGIN,sitePassword:'SYNTHETIC_SITE_PASSWORD',expectedTeamKey:teamKey,finalTokenFile:path.join(tmpdir(),'SYNTHETIC-final-token.json')};
const ownedPayload=(key=teamKey,owner)=>({fantasy_content:{users:{'0':{user:[{guid:'SYNTHETIC_GUID'},{games:{'0':{game:[{game_key:'466'},{teams:{'0':{team:[[{team_key:key},...(owner===undefined?[]:[{is_owned_by_current_login:owner}])]]},count:1}}]},count:1}}]},count:1}}});

function encrypt(payload,publicKey) {
  const key=randomBytes(32),iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);
  const ciphertext=Buffer.concat([cipher.update(JSON.stringify(payload),'utf8'),cipher.final(),cipher.getAuthTag()]);
  return {v:1,key:publicEncrypt({key:publicKey,padding:constants.RSA_PKCS1_OAEP_PADDING,oaepHash:'sha256'},key).toString('base64'),iv:iv.toString('base64'),data:ciphertext.toString('base64')};
}

test('configuration is stdin-oriented, HTTPS-pinned and never echoes supplied secrets',()=>{
  assert.equal(validateConfig({...config,sitePassword:undefined},{FANTASY_SITE_PASSWORD:'SYNTHETIC_ENV_PASSWORD'}).sitePassword,'SYNTHETIC_ENV_PASSWORD');
  for(const change of [{siteOrigin:'https://evil.invalid'},{python:'relative.py'},{sitePassword:''}]) {
    assert.throws(()=>validateConfig({...config,...change},{}),error=>error instanceof PhoneAuthError&&!error.message.includes('SYNTHETIC'));
  }
});

test('authorization parser pins Yahoo endpoint, HTTPS callback, state, scope and unique parameters',()=>{
  assert.equal(validateAuthorizationUrl(authorizationUrl).state,state);
  assert.equal(validateAuthorizationUrl(authorizationUrl+'&scope=fspt-r').redirectUri,callback);
  const mutations=[url=>url.replace('api.login.yahoo.com','evil.invalid'),url=>url.replace('request_auth?','request_auth_extra?'),url=>url+'&state='+state,url=>url+'&client_secret=SYNTHETIC_SECRET',url=>url+'&token=SYNTHETIC_SECRET',url=>url+'&scope=fspt-w',url=>url.replace('response_type=code','response_type=token'),url=>url.replace(encodeURIComponent(callback),encodeURIComponent('https://localhost:8080')),url=>url.replace(state,'short')];
  for(const mutate of mutations)assert.throws(()=>validateAuthorizationUrl(mutate(authorizationUrl)),PhoneAuthError);
});

test('hybrid encryption supports long authorization codes and cancellation; state and integrity are checked',()=>{
  const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048});
  const code='SYNTHETIC_LONG_CODE_'.repeat(100);
  const result=new URL(decryptCallback(encrypt({code,state},publicKey),privateKey,state));
  assert.equal(result.origin+result.pathname,callback);assert.equal(result.searchParams.get('code'),code);
  assert.equal(new URL(decryptCallback(encrypt({error:'access_denied',state},publicKey),privateKey,state)).searchParams.get('error'),'access_denied');
  for(const payload of [{code,state:'X'.repeat(43)},{code,error:'access_denied',state},{state},{code:'x'.repeat(4097),state},{code,state,token:'SYNTHETIC_SECRET'}])assert.throws(()=>decryptCallback(encrypt(payload,publicKey),privateKey,state),PhoneAuthError);
  const changed=encrypt({code,state},publicKey);const ciphertext=Buffer.from(changed.data,'base64');ciphertext[0]^=1;changed.data=ciphertext.toString('base64');
  assert.throws(()=>decryptCallback(changed,privateKey,state),error=>error instanceof PhoneAuthError&&!error.message.includes(code));
  assert.throws(()=>decryptCallback({...changed,key:'x'.repeat(500)},privateKey,state),PhoneAuthError);
});

test('ownership requires the exact team inside the current user and expected game collections',()=>{
  assert.equal(ownsExpectedTeam(ownedPayload(),teamKey),true);
  assert.equal(ownsExpectedTeam(ownedPayload(teamKey,1),teamKey),true);
  assert.equal(ownsExpectedTeam(ownedPayload(teamKey,0),teamKey),false);
  assert.equal(ownsExpectedTeam(ownedPayload('466.l.170945.t.99'),teamKey),false);
  assert.equal(ownsExpectedTeam({fantasy_content:{team:[{team_key:teamKey}]}},teamKey),false);
  const wrongGame=ownedPayload();wrongGame.fantasy_content.users['0'].user[1].games['0'].game[0].game_key='999';
  assert.equal(ownsExpectedTeam(wrongGame,teamKey),false);
  assert.equal(ownsExpectedTeam({error:'SYNTHETIC_ERROR',...ownedPayload()},teamKey),false);
});

async function harness(t,options={}) {
  const stateDir=await mkdtemp(path.join(tmpdir(),'synthetic-yahoo-phone-'));
  t.after(()=>{assert.ok(path.resolve(stateDir).startsWith(path.join(path.resolve(tmpdir()),'synthetic-yahoo-phone-')));return rm(stateDir,{recursive:true,force:true});});
  const finalTokenFile=path.join(stateDir,'existing-token.json');
  const controller=new AbortController();
  const originalToken=JSON.stringify({access_token:'SYNTHETIC_EXISTING_TOKEN'});
  await writeFile(finalTokenFile,originalToken,{mode:0o600});
  const requests=[],output=[],spawnCalls=[],events=[];
  let child,delivered,publicKey,pickupHash,clock=Date.now(),claimCount=0,finishCount=0,watcher;
  t.after(()=>clearInterval(watcher));
  const spawnMock=(python,args,spawnOptions)=>{
    spawnCalls.push({python,args,options:spawnOptions});
    child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();
    child.kill=()=>{events.push('kill');queueMicrotask(()=>child.emit('close',null));return true;};
    queueMicrotask(()=>child.stdout.write((options.authorizationUrl??authorizationUrl)+'\n'));
    const target=args[args.indexOf('--callback-file')+1];
    const stagedToken=args[args.indexOf('--token-file')+1];
    assert.notEqual(stagedToken,finalTokenFile);assert.equal(path.dirname(stagedToken),path.dirname(finalTokenFile));
    watcher=setInterval(async()=>{
      try{const raw=await readFile(target,'utf8');if(delivered)return;delivered=new URL(raw);events.push('callback');clearInterval(watcher);
        const code=options.childExit??(delivered.searchParams.has('error')?1:0);
        if(code===0)await writeFile(stagedToken,JSON.stringify({access_token:'SYNTHETIC_NEW_TOKEN',refresh_token:'SYNTHETIC_NEW_REFRESH'}),{mode:0o600});
        setTimeout(()=>{events.push('python-exit');child.emit('close',code);},2);}catch{}
    },2);
    return child;
  };
  const fetchMock=async(url,request)=>{
    if(new URL(url).origin==='https://fantasysports.yahooapis.com') {
      assert.equal(url,'https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_keys=466/teams?format=json');
      assert.equal(request.method,'GET');assert.equal(request.redirect,'manual');
      assert.equal(request.headers.Authorization,'Bearer SYNTHETIC_NEW_TOKEN');
      assert.equal(await readFile(finalTokenFile,'utf8'),originalToken,'Ownership must be checked before replacing the existing token');
      events.push('ownership');
      if(options.abortDuringOwnership)controller.abort();
      if(options.ownerStatus)return Response.json({},{status:options.ownerStatus});
      if(options.oversizedOwner)return new Response(' '.repeat(2*1024*1024+1));
      return Response.json(options.ownerPayload??ownedPayload());
    }
    assert.equal(new URL(url).origin,SITE_ORIGIN);assert.equal(request.redirect,'manual');assert.equal(request.headers.Origin,SITE_ORIGIN);
    const route=new URL(url).pathname;requests.push({route,request});
    if(route==='/login'){
      assert.equal(new URLSearchParams(request.body).get('password'),'SYNTHETIC_SITE_PASSWORD');
      return new Response(null,{status:303,headers:{Location:'/', 'Set-Cookie':'synthetic_session=SYNTHETIC_COOKIE; Secure; HttpOnly; Path=/'}});
    }
    const body=JSON.parse(request.body);
    if(route==='/api/yahoo/handoffs'){
      assert.equal(request.headers.Cookie,'synthetic_session=SYNTHETIC_COOKIE');
      assert.equal(body.authorizationUrl,authorizationUrl);assert.equal(body.publicKey.alg,'RSA-OAEP-256');
      assert.deepEqual(Object.keys(body.publicKey).sort(),['alg','e','kty','n']);
      publicKey=createPublicKey({key:body.publicKey,format:'jwk'});pickupHash=body.pickupHash;
      return Response.json({id,connectUrl:options.connectUrl??SITE_ORIGIN+'/yahoo/connect/'+id,expiresAt:new Date(clock+60000).toISOString()},{status:201});
    }
    assert.equal(request.headers.Cookie,undefined,'Pickup never sends the website login cookie');
    assert.equal(createHash('sha256').update(body.pickupSecret).digest('hex'),pickupHash);
    if(route.endsWith('/claim')){
      claimCount++;
      if(options.claimStatus)return Response.json({},{status:options.claimStatus});
      if(options.earlyExit){queueMicrotask(()=>child.emit('close',1));return Response.json({},{status:202});}
      if(claimCount===1)return Response.json({},{status:202});
      const payload=options.payload??{code:'SYNTHETIC_PRIVATE_CODE',state};
      return Response.json({envelope:encrypt(payload,publicKey)});
    }
    if(route.endsWith('/finish')){
      assert.equal(events.at(-1),'ownership','Finish must follow successful ownership validation');
      assert.equal(JSON.parse(await readFile(finalTokenFile,'utf8')).access_token,'SYNTHETIC_NEW_TOKEN','Verified token must be durable before finish');
      assert.equal(options.childExit??0,0);finishCount++;
      if(finishCount<=(options.finishFailures??0))return Response.json({},{status:503});
      events.push('finish');return Response.json({ok:true});
    }
    assert.fail('Unexpected route');
  };
  return {stateDir,finalTokenFile,originalToken,requests,output,events,spawnCalls,get delivered(){return delivered;},run:()=>runPhoneAuth({...config,finalTokenFile},{fetch:fetchMock,spawn:spawnMock,stateDir,output:value=>output.push(value),now:()=>clock,sleep:async ms=>{assert.equal(ms,5000);clock+=ms;},environment:{},signal:controller.signal})};
}

test('real crypto + HTTP fixture sends only connect link, atomically hands callback to child, then finishes',async t=>{
  const h=await harness(t);assert.deepEqual(await h.run(),{completed:true});
  assert.deepEqual(h.output,[SITE_ORIGIN+'/yahoo/connect/'+id]);
  assert.equal(h.delivered.searchParams.get('code'),'SYNTHETIC_PRIVATE_CODE');
  assert.equal(h.delivered.searchParams.get('state'),state);
  assert.deepEqual(h.events,['callback','python-exit','ownership','finish']);
  assert.equal(h.spawnCalls[0].options.windowsHide,true);assert.equal(h.spawnCalls[0].options.shell,false);
  assert.equal(h.spawnCalls[0].args.includes('SYNTHETIC_SITE_PASSWORD'),false);
  assert.ok(h.spawnCalls[0].args.includes('--callback-timeout'));
  assert.deepEqual(await readdir(h.stateDir),['existing-token.json'],'Only the verified final token should remain');
});

test('denied consent is handed to Python once and never acknowledged as success',async t=>{
  const h=await harness(t,{payload:{error:'access_denied',state}});
  await assert.rejects(h.run(),PhoneAuthError);
  assert.equal(h.delivered.searchParams.get('error'),'access_denied');
  assert.equal(h.requests.filter(r=>r.route.endsWith('/claim')).length,2);
  assert.equal(h.requests.some(r=>r.route.endsWith('/finish')),false);
  assert.deepEqual(await readdir(h.stateDir),['existing-token.json']);
});

test('expired handoff, stopped Python, wrong state and foreign connect links do not write callbacks or finish',async t=>{
  for(const options of [{claimStatus:410},{earlyExit:true},{payload:{code:'SYNTHETIC_PRIVATE_CODE',state:'Z'.repeat(43)}},{connectUrl:'https://evil.invalid/connect'}]) {
    await t.test(JSON.stringify(Object.keys(options)),async childTest=>{
      const h=await harness(childTest,options);
      await assert.rejects(h.run(),error=>error instanceof PhoneAuthError&&!error.message.includes('SYNTHETIC_PRIVATE_CODE'));
      assert.equal(h.delivered,undefined);assert.equal(h.requests.some(r=>r.route.endsWith('/finish')),false);
      assert.deepEqual(await readdir(h.stateDir),['existing-token.json']);
    });
  }
});

test('failed local exchange preserves server envelope and never calls finish',async t=>{
  const h=await harness(t,{childExit:1});await assert.rejects(h.run(),PhoneAuthError);
  assert.ok(h.delivered);assert.equal(h.requests.some(r=>r.route.endsWith('/finish')),false);
});

test('wrong account, ownership redirect and oversized response preserve the existing token and private staging',async t=>{
  for(const options of [{ownerPayload:ownedPayload('466.l.170945.t.99')},{ownerStatus:302},{oversizedOwner:true},{abortDuringOwnership:true}])await t.test(JSON.stringify(Object.keys(options)),async childTest=>{
    const h=await harness(childTest,options);let error;
    await assert.rejects(h.run(),caught=>{error=caught;return caught instanceof PhoneAuthError;});
    assert.equal(await readFile(h.finalTokenFile,'utf8'),h.originalToken);
    assert.ok(error.stagedTokenFile);assert.equal(path.dirname(error.stagedTokenFile),path.dirname(h.finalTokenFile));
    assert.equal(JSON.parse(await readFile(error.stagedTokenFile,'utf8')).access_token,'SYNTHETIC_NEW_TOKEN');
    assert.equal(h.requests.some(r=>r.route.endsWith('/finish')),false);
    assert.equal(JSON.stringify(h.output).includes('TOKEN'),false);
    assert.equal(error.message.includes('SYNTHETIC_NEW_TOKEN'),false);
  });
});

test('finish can be retried after durable promotion without reusing or reprinting authorization',async t=>{
  const h=await harness(t,{finishFailures:1});assert.deepEqual(await h.run(),{completed:true});
  assert.equal(h.requests.filter(r=>r.route.endsWith('/finish')).length,2);
  assert.equal(h.events.filter(event=>event==='callback').length,1);
  assert.deepEqual(h.output,[SITE_ORIGIN+'/yahoo/connect/'+id]);
});

test('exhausted finish retries report successful token promotion without exposing token values',async t=>{
  const h=await harness(t,{finishFailures:3});let error;
  await assert.rejects(h.run(),caught=>{error=caught;return caught instanceof PhoneAuthError;});
  assert.equal(error.tokenPromoted,true);assert.equal(error.stagedTokenFile,undefined);
  assert.equal(JSON.parse(await readFile(h.finalTokenFile,'utf8')).access_token,'SYNTHETIC_NEW_TOKEN');
  assert.equal(h.requests.filter(r=>r.route.endsWith('/finish')).length,3);
  assert.equal(error.message.includes('SYNTHETIC_NEW_TOKEN'),false);
});
