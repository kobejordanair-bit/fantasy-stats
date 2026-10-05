import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yahoo_client import YahooClient,YahooError,YahooAuthError


class Response:
    def __init__(self,status,data):self.status_code=status;self.data=data;self.headers={};self.closed=False
    def json(self):return self.data
    def close(self):self.closed=True


class Session:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def get(self,*args,**kwargs):self.calls.append(('get',args,kwargs));return self.responses.pop(0)
    def post(self,*args,**kwargs):self.calls.append(('post',args,kwargs));return self.responses.pop(0)
    def close(self):pass


class ClientTests(unittest.TestCase):
    def client(self,responses):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        session=Session(responses);client=YahooClient('synthetic-id','synthetic-secret','https://localhost:8080',Path(tmp.name)/'token.json',session=session,sleep=lambda _:None)
        client.token={'access_token':'synthetic-old','refresh_token':'synthetic-refresh','expires_at':time.time()+3600}
        return client,session

    def test_401_refresh_once_retains_refresh_token_and_closes_responses(self):
        responses=[Response(401,{}),Response(200,{'access_token':'synthetic-new','expires_in':3600}),Response(200,{'fantasy_content':{}})]
        client,session=self.client(responses);client.get('/team/999.l.1.t.1')
        self.assertEqual(client.token['refresh_token'],'synthetic-refresh')
        self.assertEqual([c[0] for c in session.calls],['get','post','get'])
        self.assertTrue(all(r.closed for r in responses));self.assertTrue(client.token_file.exists())

    def test_exhausted_throttle_raises_not_none(self):
        client,_=self.client([Response(999,{}) for _ in range(4)])
        with self.assertRaises(YahooError):client.get('/team/999.l.1.t.1')

    def test_redirect_response_not_followed_and_body_not_exposed(self):
        client,session=self.client([Response(302,{'secret':'never print this'})])
        with self.assertRaises(YahooError) as caught:client.get('/team/999.l.1.t.1')
        self.assertNotIn('never print',str(caught.exception));self.assertFalse(session.calls[0][2]['allow_redirects'])

    def test_oauth_wrong_state_or_callback_never_posts(self):
        client,session=self.client([])
        with patch('builtins.print'):
            with self.assertRaisesRegex(YahooError,'state'):
                client.login(input_fn=lambda _:'https://localhost:8080?state=wrong&code=fake',open_browser=False)
            with self.assertRaisesRegex(YahooError,'位址'):
                client.login(input_fn=lambda _:'https://evil.example/?code=fake',open_browser=False)
        self.assertFalse(session.calls)

    def test_oauth_valid_state(self):
        client,session=self.client([Response(200,{'access_token':'synthetic-access','refresh_token':'synthetic-refresh','expires_in':3600})])
        printed=[]
        def callback(_):
            url=next(v for v in printed if v.startswith('https://api.login.yahoo.com'))
            state=parse_qs(urlsplit(url).query)['state'][0]
            return 'https://localhost:8080?state='+state+'&code=synthetic-code'
        with patch('builtins.print',side_effect=lambda v:printed.append(v)):
            client.login(input_fn=callback,open_browser=False)
        self.assertEqual(session.calls[0][0],'post')

    def test_fresh_login_normalized_root_callback_does_not_keep_other_account_refresh(self):
        client,session=self.client([Response(200,{'access_token':'synthetic-account-B','expires_in':3600})])
        printed=[]
        def callback(_):
            state=parse_qs(urlsplit(next(v for v in printed if v.startswith('https://api.login.yahoo.com'))).query)['state'][0]
            return 'https://localhost:8080/?state='+state+'&code=synthetic-code'
        with patch('builtins.print',side_effect=lambda v:printed.append(v)):
            client.login(input_fn=callback,open_browser=False)
        self.assertEqual(client.token['access_token'],'synthetic-account-B')
        self.assertNotIn('refresh_token',client.token)
        with self.assertRaises(YahooError):client.refresh()

    def test_callback_file_atomic_handoff_consumes_before_exchange_without_echo(self):
        client,session=self.client([Response(200,{'access_token':'synthetic-access','expires_in':3600})])
        callback_file=client.token_file.parent/'one-use-callback.txt'
        printed=[];writers=[];failures=[]
        def emit(line):
            printed.append(line)
            if line.startswith('https://api.login.yahoo.com'):
                state=parse_qs(urlsplit(line).query)['state'][0]
                def deliver():
                    try:
                        time.sleep(.02)
                        pending=callback_file.with_suffix('.pending')
                        pending.write_text('https://localhost:8080/?state='+state+'&code=synthetic-private-code',encoding='utf-8')
                        os.replace(pending,callback_file)
                    except Exception as error:failures.append(type(error).__name__)
                writer=threading.Thread(target=deliver);writers.append(writer);writer.start()
        original_request=client._token_request
        def exchange(data):
            self.assertFalse(callback_file.exists(),'The callback must be removed before token exchange')
            original_request(data)
        try:
            with patch('builtins.print',side_effect=emit),patch.object(client,'_token_request',side_effect=exchange):
                client.login(callback_file=callback_file,callback_timeout=2,open_browser=False,
                             input_fn=lambda _:self.fail('File mode must not prompt for input'))
        finally:
            for writer in writers:writer.join(timeout=2)
        self.assertFalse(failures);self.assertFalse(callback_file.exists())
        self.assertEqual(session.calls[0][2]['data']['code'],'synthetic-private-code')
        self.assertEqual(session.calls[0][2]['data']['redirect_uri'],'https://localhost:8080')
        self.assertNotIn('synthetic-private-code','\n'.join(printed))
        self.assertNotIn('refresh_token',client.token,'A new login must not inherit an earlier account refresh token')

    def test_callback_file_rejects_invalid_grants_and_deletes_consumed_input(self):
        for ending in ['?state=wrong&code=synthetic-private-code',
                       '?state={state}&code=synthetic-private-code&code=',
                       '?state={state}&state=&code=synthetic-private-code',
                       '?state={state}&code=synthetic-private-code&error=',
                       '?state={state}&code=synthetic-private-code#fragment',
                       '?state={state}&code=one\nhttps://example.invalid/']:
            with self.subTest(ending=ending):
                client,session=self.client([]);path=client.token_file.parent/'callback.txt';printed=[]
                def emit(line):
                    printed.append(line)
                    if line.startswith('https://api.login.yahoo.com'):
                        state=parse_qs(urlsplit(line).query)['state'][0]
                        path.write_text('https://localhost:8080/'+ending.replace('{state}',state),encoding='utf-8')
                with patch('builtins.print',side_effect=emit),self.assertRaises(YahooError) as caught:
                    client.login(callback_file=path,callback_timeout=1,open_browser=False)
                self.assertFalse(path.exists());self.assertFalse(session.calls)
                self.assertNotIn('synthetic-private-code',str(caught.exception)+'\n'.join(printed))
                self.assertFalse(client.token_file.exists())

    def test_callback_file_bad_encoding_and_size_are_consumed_without_exchange(self):
        for payload in [b'\xffsynthetic-private-code',b'x'*8193]:
            with self.subTest(size=len(payload)):
                client,session=self.client([]);path=client.token_file.parent/'callback.txt'
                def emit(line):
                    if line.startswith('https://api.login.yahoo.com'):path.write_bytes(payload)
                with patch('builtins.print',side_effect=emit),self.assertRaises(YahooError) as caught:
                    client.login(callback_file=path,callback_timeout=1,open_browser=False)
                self.assertFalse(path.exists());self.assertFalse(session.calls)
                self.assertNotIn('synthetic-private-code',str(caught.exception))

    def test_callback_file_preexisting_is_preserved_and_never_opens_browser(self):
        client,session=self.client([]);path=client.token_file.parent/'callback.txt'
        path.write_text('synthetic-private-stale-callback',encoding='utf-8')
        with patch('builtins.print') as output,patch('webbrowser.open') as browser,self.assertRaisesRegex(YahooError,'已存在'):
            client.login(callback_file=path)
        self.assertEqual(path.read_text(encoding='utf-8'),'synthetic-private-stale-callback')
        output.assert_not_called();browser.assert_not_called();self.assertFalse(session.calls)

    def test_callback_file_timeout_and_invalid_timeouts_never_exchange(self):
        client,session=self.client([]);path=client.token_file.parent/'callback.txt'
        with patch('builtins.print'),self.assertRaisesRegex(YahooError,'逾時'):
            client.login(callback_file=path,callback_timeout=.02,open_browser=False)
        self.assertFalse(path.exists());self.assertFalse(session.calls)
        for seconds in [0,-1,1801,float('inf'),float('nan'),True]:
            with self.subTest(seconds=seconds),patch('builtins.print') as output,self.assertRaises(YahooError):
                client.login(callback_file=path,callback_timeout=seconds,open_browser=False)
            output.assert_not_called()

    def test_callback_file_cannot_replace_token_file(self):
        client,session=self.client([])
        with patch('builtins.print'),self.assertRaisesRegex(YahooError,'不同檔案'):
            client.login(callback_file=client.token_file,open_browser=False)
        self.assertFalse(session.calls);self.assertFalse(client.token_file.exists())

    def test_callback_file_remove_failure_never_exchanges_or_echoes(self):
        client,session=self.client([]);path=client.token_file.parent/'callback.txt'
        def emit(line):
            if line.startswith('https://api.login.yahoo.com'):
                state=parse_qs(urlsplit(line).query)['state'][0]
                path.write_text('https://localhost:8080/?state='+state+'&code=synthetic-private-code',encoding='utf-8')
        with patch('builtins.print',side_effect=emit),patch.object(Path,'unlink',side_effect=PermissionError('synthetic-private-code')):
            with self.assertRaisesRegex(YahooError,'安全移除') as caught:
                client.login(callback_file=path,callback_timeout=1,open_browser=False)
        self.assertFalse(session.calls);self.assertNotIn('synthetic-private-code',str(caught.exception))

    def test_authorization_failures_are_terminal_for_collection(self):
        for responses in [[Response(403,{})],
                          [Response(401,{}),Response(200,{'access_token':'synthetic-new'}),Response(401,{})]]:
            with self.subTest(statuses=[r.status_code for r in responses]):
                client,session=self.client(responses)
                with self.assertRaises(YahooAuthError):client.get('/users;use_login=1')
                self.assertTrue(all(r.closed for r in responses))
                self.assertEqual(len(session.calls),len(responses))
        client,session=self.client([Response(400,{'sensitive':'synthetic-private-response'})])
        with self.assertRaises(YahooAuthError) as caught:client.refresh()
        self.assertNotIn('synthetic-private-response',str(caught.exception))
        client.token={}
        with self.assertRaises(YahooAuthError):client.refresh()

if __name__=='__main__':unittest.main()
