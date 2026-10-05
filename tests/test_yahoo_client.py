import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yahoo_client import YahooClient,YahooError


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

if __name__=='__main__':unittest.main()
