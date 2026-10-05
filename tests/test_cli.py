import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fetch_stats import main


class CliTests(unittest.TestCase):
    def test_check_config_never_connects_or_prints_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{'YAHOO_CLIENT_ID':'synthetic-private-id','YAHOO_CLIENT_SECRET':'synthetic-private-secret'},clear=True),patch('yahoo_client.requests.Session') as network:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    code=main(['--env-file',str(Path(tmp)/'absent'),'--token-file',str(Path(tmp)/'absent-token'),'--check-config'])
                self.assertEqual(code,0);network.assert_not_called()
                self.assertNotIn('synthetic-private',output.getvalue())

    def test_list_leagues_without_keys_returns_actionable_error_without_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{},clear=True),patch('yahoo_client.requests.Session') as network:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):code=main(['--env-file',str(Path(tmp)/'absent'),'--list-leagues'])
                self.assertEqual(code,1);self.assertIn('YAHOO_CLIENT_ID',output.getvalue());network.assert_not_called()

if __name__=='__main__':unittest.main()
