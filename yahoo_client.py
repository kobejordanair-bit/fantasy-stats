"""Read-only Yahoo OAuth/API client. No response bodies or credentials enter logs."""
from __future__ import annotations
import json
import os
import secrets
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
import requests

AUTH_URL='https://api.login.yahoo.com/oauth2/request_auth'
TOKEN_URL='https://api.login.yahoo.com/oauth2/get_token'
BASE_URL='https://fantasysports.yahooapis.com/fantasy/v2'


class YahooError(RuntimeError):
    pass


class YahooClient:
    def __init__(self, client_id, client_secret, redirect_uri, token_file, *, session=None, sleep=time.sleep):
        if not client_id or not client_secret:
            raise YahooError('請設定 YAHOO_CLIENT_ID 與 YAHOO_CLIENT_SECRET。')
        uri=urlsplit(redirect_uri)
        if uri.scheme not in {'https','http'} or not uri.netloc or uri.query or uri.fragment:
            raise YahooError('YAHOO_REDIRECT_URI 必須與 Yahoo Developer App 完全一致且不含 query。')
        self.client_id=client_id;self.client_secret=client_secret;self.redirect_uri=redirect_uri
        self.token_file=Path(token_file).expanduser();self.session=session or requests.Session();self.sleep=sleep
        self.token={}
        if self.token_file.exists():
            try:
                self.token=json.loads(self.token_file.read_text(encoding='utf-8'))
                if not isinstance(self.token,dict): raise ValueError()
                if not isinstance(self.token.get('expires_at',0),(int,float)): raise ValueError()
                if any(k in self.token and not isinstance(self.token[k],str) for k in ['access_token','refresh_token']): raise ValueError()
            except (OSError,ValueError):
                raise YahooError('Token 檔案無法讀取；請備份移開後重新登入。') from None

    def close(self):
        self.session.close()

    def _store_token(self, data, *, preserve_refresh=False):
        if not isinstance(data,dict) or not isinstance(data.get('access_token'),str):
            raise YahooError('Yahoo 未傳回有效 token。')
        # Only refresh grants may reuse an omitted refresh_token. A fresh login may
        # belong to a different Yahoo account and must never inherit its predecessor.
        merged={k:v for k,v in self.token.items() if preserve_refresh and k in {'refresh_token'}}
        merged.update({k:data[k] for k in ['access_token','refresh_token','token_type'] if k in data})
        try: merged['expires_at']=time.time()+max(60,int(data.get('expires_in',3600)))
        except (TypeError,ValueError): raise YahooError('Yahoo token 有效期格式不正確。') from None
        self.token_file.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.token_file.with_name(self.token_file.name+'.'+secrets.token_hex(6)+'.tmp')
        try:
            fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(merged,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.token_file)
            if os.name!='nt': os.chmod(self.token_file,0o600)
        finally:
            if temporary.exists(): temporary.unlink()
        self.token=merged

    def _token_request(self, data):
        try:
            response=self.session.post(TOKEN_URL,data=data,auth=(self.client_id,self.client_secret),
                                       timeout=(10,45),allow_redirects=False)
        except requests.RequestException:
            raise YahooError('Yahoo 授權連線失敗，請稍後重試。') from None
        try:
            if response.status_code!=200:
                raise YahooError(f'Yahoo 授權失敗（HTTP {response.status_code}），請重新登入。')
            try: result=response.json()
            except ValueError: raise YahooError('Yahoo 授權回應不是 JSON。') from None
            self._store_token(result,preserve_refresh=data.get('grant_type')=='refresh_token')
        finally: response.close()

    def login(self, *, input_fn=input, open_browser=True):
        state=secrets.token_urlsafe(32)
        link=AUTH_URL+'?'+urlencode({'client_id':self.client_id,'redirect_uri':self.redirect_uri,
                                   'response_type':'code','state':state})
        print('請在 Yahoo 登入並授權，之後貼回完整 callback URL（不會記錄該 URL）。')
        print(link)
        if open_browser: webbrowser.open(link)
        callback=urlsplit(input_fn('Callback URL: ').strip()); expected=urlsplit(self.redirect_uri)
        query=parse_qs(callback.query)
        if (callback.scheme,callback.netloc,callback.path or '/')!=(expected.scheme,expected.netloc,expected.path or '/'):
            raise YahooError('Callback 位址不符，未交換 token。')
        supplied=query.get('state',[])
        if len(supplied)!=1 or not secrets.compare_digest(supplied[0],state):
            raise YahooError('OAuth state 不符，未交換 token。')
        codes=query.get('code',[])
        if len(codes)!=1 or not codes[0] or query.get('error'):
            raise YahooError('Yahoo 未完成授權。')
        self._token_request({'grant_type':'authorization_code','code':codes[0],'redirect_uri':self.redirect_uri})

    def refresh(self):
        if not self.token.get('refresh_token'):
            raise YahooError('沒有可用登入，請先執行 login。')
        self._token_request({'grant_type':'refresh_token','refresh_token':self.token['refresh_token'],
                             'redirect_uri':self.redirect_uri})

    def get(self, path):
        if not path.startswith('/') or '://' in path or '?' in path or '#' in path:
            raise ValueError('API path 格式不正確')
        if not self.token.get('access_token') or self.token.get('expires_at',0)<time.time()+60:
            self.refresh()
        refreshed=False
        for attempt in range(4):
            try:
                response=self.session.get(BASE_URL+path,params={'format':'json'},
                                          headers={'Authorization':'Bearer '+self.token['access_token']},
                                          timeout=(10,45),allow_redirects=False)
            except requests.RequestException:
                if attempt==3: raise YahooError('Yahoo API 連線失敗，已達重試上限。') from None
                self.sleep(min(2**attempt,8));continue
            try:
                code=response.status_code
                if code==401 and not refreshed:
                    self.refresh();refreshed=True;continue
                if code in {429,500,502,503,504,999} and attempt<3:
                    retry=response.headers.get('Retry-After','')
                    self.sleep(min(int(retry),60) if retry.isdigit() else min(2**attempt,8));continue
                if code!=200:
                    raise YahooError(f'Yahoo API 拒絕請求（HTTP {code}）。')
                try: data=response.json()
                except ValueError: raise YahooError('Yahoo API 回應不是 JSON。') from None
                if not isinstance(data,dict) or 'fantasy_content' not in data:
                    raise YahooError('Yahoo API 回應缺少 fantasy_content。')
                return data
            finally: response.close()
        raise YahooError('Yahoo API 未成功完成請求。')
