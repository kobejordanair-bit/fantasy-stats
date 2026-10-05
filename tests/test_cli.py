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

    def test_login_callback_file_options_reach_client_without_reading_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);callback=root/'callback.txt'
            with patch.dict(os.environ,{'YAHOO_CLIENT_ID':'synthetic-id','YAHOO_CLIENT_SECRET':'synthetic-secret'},clear=True),patch('fetch_stats.YahooClient') as constructor:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    code=main(['--env-file',str(root/'absent'),'--token-file',str(root/'token.json'),
                               'login','--no-browser','--callback-file',str(callback),'--callback-timeout','1800'])
                self.assertEqual(code,0)
                constructor.return_value.login.assert_called_once_with(open_browser=False,callback_file=callback,callback_timeout=1800)
                constructor.return_value.close.assert_called_once()
                self.assertNotIn('synthetic-secret',output.getvalue());self.assertFalse(callback.exists())

    def test_resume_is_forwarded_to_collector_with_a_new_output_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);previous=root/'previous';previous.mkdir()
            marker=previous/'synthetic-evidence.txt';marker.write_text('must remain unchanged',encoding='utf-8')
            with patch.dict(os.environ,{'YAHOO_CLIENT_ID':'synthetic-id','YAHOO_CLIENT_SECRET':'synthetic-secret'},clear=True),patch('fetch_stats.YahooClient') as constructor,patch('fetch_stats.Collector') as collector,patch('fetch_stats.export_all') as export:
                constructor.return_value.get.return_value={'fantasy_content':{'league':[{'season':'2025'}]}}
                collector.return_value.collect.return_value={'quality':{'warnings':[]}}
                with contextlib.redirect_stdout(io.StringIO()):
                    code=main(['--env-file',str(root/'absent'),'--token-file',str(root/'token.json'),
                               'collect','--league','999.l.1','--team','999.l.1.t.1','--output',str(root/'reports'),
                               '--resume',str(previous),'--skip-trade-stats'])
                self.assertEqual(code,0)
                args,kwargs=collector.call_args
                self.assertEqual(kwargs['resume_from'],previous)
                self.assertNotEqual(args[1],previous);self.assertTrue(args[1].is_dir())
                self.assertTrue(args[1].is_relative_to(root/'reports'/'2025-26'/'999.l.1'/'999.l.1.t.1'))
                self.assertEqual(marker.read_text(encoding='utf-8'),'must remain unchanged')
                collector.return_value.collect.assert_called_once_with('999.l.1','999.l.1.t.1',include_trades=False)
                export.assert_called_once()

if __name__=='__main__':unittest.main()
