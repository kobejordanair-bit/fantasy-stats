"""Synthetic fixtures only: these are not the user's league or championship results."""
import json
import hashlib
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fantasy_core import parse_stats, player_info, blank_season, validate_season, aggregate, awards, waiver_stints, player_totals
from season_collector import Collector
from yahoo_client import YahooError, YahooAuthError

LEAGUE='999.l.1';TEAM=LEAGUE+'.t.1';OTHER=LEAGUE+'.t.2'


def stats_block(coverage,period,pts=10):
    return {'coverage_type':coverage,'week' if coverage=='week' else 'date':str(period),
            'stats':[{'stat':{'stat_id':'12','value':str(pts)}},{'stat':{'stat_id':'4','value':'2'}},
                     {'stat':{'stat_id':'3','value':'4'}},{'stat':{'stat_id':'5','value':'.5'}}]}


def player(pid,day,slot='PG',pts=10):
    return [[{'player_key':'999.p.'+pid},{'name':{'full':'SYNTHETIC '+pid}},{'display_position':'PG'}],
            {'selected_position':[{'coverage_type':'date','date':day},{'position':slot}]},
            {'player_stats':stats_block('date',day,pts)}]


class FakeClient:
    def __init__(self): self.calls=[];self.fail_day=None;self.live=False;self.final_rank=1;self.guid='synthetic-account-1';self.auth_fail_day=None;self.invalid_day=None
    def get(self,path):
        self.calls.append(path)
        if path=='/users;use_login=1': return {'fantasy_content':{'users':{'0':{'user':[{'guid':self.guid}]},'count':1}}}
        if path=='/league/'+LEAGUE:
            return {'fantasy_content':{'league':[{'league_key':LEAGUE,'name':'SYNTHETIC league','season':'2025','start_week':'1','end_week':'1','current_week':'1','is_finished':'1'}]}}
        if path=='/team/'+TEAM: return {'fantasy_content':{'team':[[{'team_key':TEAM},{'name':'SYNTHETIC team'}]]}}
        if path.endswith('/settings'):
            return {'fantasy_content':{'settings':{'stat_categories':{'stats':[{'stat':{'stat_id':'12','display_name':'PTS','sort_order':'1'}},{'stat':{'stat_id':'5','display_name':'FG%','sort_order':'1'}}]}}}}
        if path.endswith('/game_weeks'):
            return {'fantasy_content':{'game_weeks':[{'game_week':{'week':'1','start':'2025-10-20','end':'2025-10-22'}}]}}
        if '/scoreboard;' in path:
            teams=[{'team':[[{'team_key':TEAM},{'name':'SYNTHETIC team'}],{'team_stats':stats_block('week',1,100)}]},
                   {'team':[[{'team_key':OTHER},{'name':'SYNTHETIC opponent'}],{'team_stats':stats_block('week',1,90)}]}]
            return {'fantasy_content':{'matchups':[{'matchup':{'week':'1','status':'midevent' if self.live else 'postevent','is_playoffs':'1','winner_team_key':TEAM,
                     'stat_winners':[{'stat_winner':{'stat_id':'12','winner_team_key':TEAM}},{'stat_winner':{'stat_id':'5','is_tied':'1'}}],'teams':teams}}]}}
        if '/roster;date=' in path:
            day=path.split('date=')[1][:10]
            if day==self.auth_fail_day: raise YahooAuthError('synthetic auth unavailable')
            if day==self.fail_day: raise YahooError('synthetic unavailable')
            if day==self.invalid_day: return {'fantasy_content':{'roster':{'coverage_type':'date','date':'1900-01-01','players':{}}}}
            return {'fantasy_content':{'roster':{'coverage_type':'date','date':day,'players':{'0':{'player':player('1',day)},'1':{'player':player('2',day,'BN',999)},'count':2}}}}
        if '/transactions;' in path:
            def tx_player(pid,src,dst):
                return {'player':[[{'player_key':'999.p.'+pid},{'name':{'full':'SYNTHETIC '+pid}}],{'transaction_data':{'source_team_key':src,'destination_team_key':dst,'source_team_name':'SYNTHETIC source','destination_team_name':'SYNTHETIC dest'}}]}
            return {'fantasy_content':{'transactions':[{'transaction':[{'transaction_key':'999.l.1.tr.1','type':'trade','status':'successful','timestamp':1760918400},
                    {'players':[tx_player('1',OTHER,TEAM),tx_player('3',TEAM,OTHER)]}]}]}}
        if path.startswith('/player/'):
            day=path.rsplit('date=',1)[1]
            return {'fantasy_content':{'player':[[],{'player_stats':stats_block('date',day,20 if '999.p.1/' in path else 15)}]}}
        if path.endswith('/standings'):
            return {'fantasy_content':{'standings':{'teams':[{'team':[[{'team_key':TEAM}],{'team_standings':{'rank':str(self.final_rank)}}]}]}}}
        raise AssertionError('unexpected endpoint '+path)


class CollectorTests(unittest.TestCase):
    def collect(self,client=None):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        client=client or FakeClient()
        c=Collector(client,self.tmp.name,today=date(2025,11,1),progress=lambda x:None)
        return c.collect(LEAGUE,TEAM),client

    def test_official_team_and_active_daily_are_separate(self):
        season,client=self.collect()
        self.assertEqual(season['teamWeeks'][0]['stats']['PTS'],100)
        self.assertEqual(season['players'][0]['stats']['PTS'],30)
        self.assertEqual(len(season['players']),2)
        self.assertEqual(season['players'][1]['stats']['PTS'],0)
        self.assertIsNone(season['players'][1]['stats']['FG%'])
        self.assertEqual(season['players'][0]['stats']['FG%'],.5)
        self.assertEqual(season['finalRank'],1);self.assertTrue(season['isChampion'])
        self.assertEqual(season['matchups'][0]['phase'],'playoffs')
        self.assertEqual(season['categoryRecord'][0]['wins'],1)
        self.assertTrue((Path(self.tmp.name)/'source-manifest.json').exists())

    def test_partial_day_marks_week_unknown_without_replacing_official_score(self):
        client=FakeClient();client.fail_day='2025-10-21';season,_=self.collect(client)
        self.assertIsNone(season['players'][0]['stats']['PTS'])
        self.assertEqual(season['teamWeeks'][0]['stats']['PTS'],100)
        self.assertEqual(season['quality']['missingWeeks'],[1])

    def test_live_results_not_treated_as_official_wins(self):
        client=FakeClient();client.live=True;season,_=self.collect(client)
        self.assertEqual(season['matchups'][0]['status'],'live')
        self.assertIsNone(season['matchups'][0]['result'])
        self.assertEqual(season['categoryRecord'][0]['wins'],0)

    def test_trade_both_sides_get_same_days_and_scope(self):
        season,client=self.collect();r=season['tradeAnalysis'][0]['categories'][0]
        left=[p.rsplit('date=',1)[1] for p in client.calls if p.startswith('/player/999.p.1/')]
        right=[p.rsplit('date=',1)[1] for p in client.calls if p.startswith('/player/999.p.3/')]
        self.assertEqual(left,right);self.assertGreater(len(left),0)
        self.assertEqual(r['received'],20*len(left));self.assertEqual(r['sent'],15*len(right))

    def test_selected_position_list_preserves_metadata(self):
        self.assertTrue(player_info(player('1','2025-10-20'))['counts'])
        self.assertFalse(player_info(player('1','2025-10-20','IL+'))['counts'])

    def test_wrong_stats_date_remains_unknown(self):
        value=parse_stats(player('1','2025-10-20'),{'12':'PTS'},coverage='date',period='2025-10-21')
        self.assertEqual(value,{})

    def test_aggregate_zero_attempts_and_unknown(self):
        self.assertEqual(aggregate([{'FGM':0,'FGA':0},{'FGM':3,'FGA':4}],['FG%'])['FG%'],.75)
        self.assertIsNone(aggregate([{'FGM':3,'FGA':2}],['FG%'])['FG%'])
        self.assertIsNone(aggregate([{'PTS':True}],['PTS'])['PTS'])

    def test_invalid_ratios_and_schema_limit_fail_without_truncation(self):
        s=blank_season(2025,'SYNTHETIC');s['teamWeeks']=[{'week':1,'stats':{'FGM':3,'FGA':2}}]
        with self.assertRaisesRegex(ValueError,'命中'):validate_season(s)
        s['teamWeeks']=[];s['players']=[{}]*201
        with self.assertRaisesRegex(ValueError,'未|禁止'):validate_season(s)

    def test_display_only_stats_are_auxiliary_and_unknown_ratio_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Collector(FakeClient(),tmp,progress=lambda _:None)
            raw={'stat_categories':{'stats':[{'stat':{'stat_id':'12','display_name':'PTS'}},{'stat':{'stat_id':'3','display_name':'FGA','is_only_display_stat':'1'}}]}}
            mapping,cats=c.categories(raw)
            self.assertEqual(mapping['3'],'FGA');self.assertEqual([r['key'] for r in cats],['PTS'])
            raw['stat_categories']['stats'].append({'stat':{'stat_id':'new','display_name':'A/T'}})
            with self.assertRaisesRegex(YahooError,'比例'):c.categories(raw)

    def test_invalid_daily_ratio_cannot_be_hidden_by_aggregation(self):
        self.assertIsNone(aggregate([{'FGM':3,'FGA':2},{'FGM':1,'FGA':10}],['FG%'])['FG%'])
        self.assertIsNone(aggregate([{'FGM':2,'FGA':4,'FG%':.9}],['FG%'])['FG%'])
        weekly=aggregate([{'FGM':3,'FGA':2},{'FGM':1,'FGA':10}],['FG%'])
        total=player_totals([{'playerId':'1','name':'A','position':'PG','stats':weekly}],['FG%'])
        self.assertIsNone(total[0]['stats']['FG%'])
        self.assertIsNone(total[0]['stats']['FGM'])

    def test_export_validation_rejects_inconsistent_rates_and_invalid_dates(self):
        s=blank_season(2025,'SYNTHETIC')
        s['teamWeeks']=[{'week':1,'stats':{'FGM':5,'FGA':10,'FG%':.9}}]
        with self.assertRaisesRegex(ValueError,'命中'):validate_season(s)
        s['teamWeeks']=[{'week':1,'startDate':'2025-02-30','stats':{}}]
        with self.assertRaisesRegex(ValueError,'日期'):validate_season(s)

    def test_awards_missing_data_is_not_zero_and_negative_direction(self):
        players=[{'name':'A','weeks':1,'stats':{'PTS':10,'TO':1}},{'name':'B','weeks':1,'stats':{'PTS':20,'TO':3}}, {'name':'unknown','weeks':1,'stats':{'PTS':None,'TO':0}}]
        cats=[{'key':'PTS','lowerIsBetter':False},{'key':'TO','lowerIsBetter':True}]
        leaders,mvp=awards(players,cats)
        self.assertEqual(leaders[1]['totalName'],'B');self.assertIn('負向',leaders[1]['label'])
        self.assertIsNone(next(p for p in mvp if p['name']=='unknown')['score'])
        self.assertGreater(next(p for p in mvp if p['name']=='A')['categories']['TO'],next(p for p in mvp if p['name']=='B')['categories']['TO'])

    def test_missing_day_prevents_waiver_partial_total(self):
        p={'id':'1','name':'A'};event={'type':'add','date':'2025-10-19','received':[p],'sent':[]}
        rows=[{'playerId':'1','date':'2025-10-20','week':1,'stats':{'PTS':10}}]
        self.assertIsNone(waiver_stints([event],rows,['PTS'],[(1,'2025-10-21')])[0]['stats']['PTS'])

    def new_collector(self,client,path,**kwargs):
        return Collector(client,path,today=date(2025,11,1),progress=lambda _:None,**kwargs)

    def test_resume_repairs_partial_day_without_changing_old_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';new=Path(tmp)/'new'
            client=FakeClient();client.fail_day='2025-10-21'
            partial=self.new_collector(client,old).collect(LEAGUE,TEAM)
            before={p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}
            current=FakeClient();season=self.new_collector(current,new,resume_from=old).collect(LEAGUE,TEAM)
            self.assertEqual(partial['quality']['missingWeeks'],[1])
            self.assertEqual(season['quality']['missingWeeks'],[])
            self.assertEqual(season['players'][0]['stats']['PTS'],30)
            calls=[p for p in current.calls if '/roster;date=' in p]
            self.assertEqual(len(calls),1);self.assertIn('date=2025-10-21',calls[0])
            self.assertFalse(any('/scoreboard;' in p or p.startswith('/player/') for p in current.calls))
            self.assertIn('/users;use_login=1',current.calls)
            self.assertIn('/league/'+LEAGUE+'/settings',current.calls)
            self.assertIn('/game/999/game_weeks',current.calls)
            self.assertEqual(before,{p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()})
            manifest=json.loads((new/'source-manifest.json').read_text(encoding='utf-8'))
            for entry in manifest['requests']:
                content=(new/entry['file']).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(),entry['sha256'])
                self.assertTrue(Path(entry['file']).name.startswith(hashlib.sha256(entry['path'].encode()).hexdigest()+'-'))

    def test_fatal_auth_stops_immediately_and_interrupted_run_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';client=FakeClient();client.auth_fail_day='2025-10-21'
            with self.assertRaises(YahooAuthError): self.new_collector(client,old).collect(LEAGUE,TEAM)
            self.assertFalse(any('date=2025-10-22' in p for p in client.calls))
            self.assertTrue((old/'source-manifest.jsonl').is_file())
            self.assertFalse((old/'source-manifest.json').exists())
            current=FakeClient();season=self.new_collector(current,Path(tmp)/'new',resume_from=old).collect(LEAGUE,TEAM)
            self.assertEqual(season['players'][0]['stats']['PTS'],30)
            self.assertFalse(any('roster;date=2025-10-20' in p for p in current.calls))

    def test_resume_rejects_wrong_identity_and_legacy_unbound_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';self.new_collector(FakeClient(),old).collect(LEAGUE,TEAM)
            other=FakeClient();other.guid='synthetic-different-account'
            with self.assertRaisesRegex(ValueError,'帳號'):
                self.new_collector(other,Path(tmp)/'other',resume_from=old).collect(LEAGUE,TEAM)
            self.assertFalse(any('roster;' in p for p in other.calls))
            with self.assertRaisesRegex(ValueError,'時區'):
                self.new_collector(FakeClient(),Path(tmp)/'zone',resume_from=old,timezone_name='UTC').collect(LEAGUE,TEAM)
            journal=old/'source-manifest.jsonl';original=journal.read_text(encoding='utf-8')
            for field,value in [('leagueKey','999.l.2'),('teamKey',OTHER),('season',2024)]:
                lines=original.splitlines();header=json.loads(lines[0]);header['identity'][field]=value
                lines[0]=json.dumps(header);journal.write_text('\n'.join(lines)+'\n',encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'球季'):
                    self.new_collector(FakeClient(),Path(tmp)/field,resume_from=old).collect(LEAGUE,TEAM)
            journal.unlink()
            with self.assertRaisesRegex(ValueError,'可驗證帳號'):
                self.new_collector(FakeClient(),Path(tmp)/'legacy',resume_from=old).collect(LEAGUE,TEAM)

    def test_resume_validates_digest_and_path_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';self.new_collector(FakeClient(),old).collect(LEAGUE,TEAM)
            journal=old/'source-manifest.jsonl';original=journal.read_text(encoding='utf-8')
            entries=[json.loads(line) for line in original.splitlines()]
            cached=next(e for e in entries if '/roster;date=' in e.get('path',''))
            raw=old/cached['file'];content=raw.read_bytes();raw.write_bytes(content+b' ')
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                self.new_collector(FakeClient(),Path(tmp)/'digest',resume_from=old).collect(LEAGUE,TEAM)
            raw.write_bytes(content)
            cached['file']='../outside.json'
            journal.write_text('\n'.join(json.dumps(e) for e in entries)+'\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'API path'):
                self.new_collector(FakeClient(),Path(tmp)/'path',resume_from=old).collect(LEAGUE,TEAM)

    def test_semantically_invalid_cached_day_is_refetched_and_live_scores_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';client=FakeClient();client.invalid_day='2025-10-21';client.live=True
            partial=self.new_collector(client,old).collect(LEAGUE,TEAM)
            current=FakeClient();season=self.new_collector(current,Path(tmp)/'new',resume_from=old).collect(LEAGUE,TEAM)
            self.assertEqual(partial['quality']['missingWeeks'],[1])
            self.assertEqual(season['quality']['missingWeeks'],[])
            self.assertEqual(season['matchups'][0]['status'],'final')
            self.assertTrue(any('roster;date=2025-10-21' in p for p in current.calls))
            self.assertTrue(any('/scoreboard;' in p for p in current.calls))

    def test_truncated_final_journal_record_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';self.new_collector(FakeClient(),old).collect(LEAGUE,TEAM)
            with (old/'source-manifest.jsonl').open('ab') as stream: stream.write(b'{"kind":"response","path":')
            season=self.new_collector(FakeClient(),Path(tmp)/'new',resume_from=old).collect(LEAGUE,TEAM)
            self.assertEqual(season['players'][0]['stats']['PTS'],30)
            self.assertTrue(any('最後一筆' in warning for warning in season['quality']['warnings']))

    def test_resume_rejects_invalid_json_error_responses_and_unknown_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';self.new_collector(FakeClient(),old).collect(LEAGUE,TEAM)
            journal=old/'source-manifest.jsonl';original=journal.read_text(encoding='utf-8')
            for label,content in [('invalid',b'{bad-json'),('error',b'{"error":{"description":"synthetic"}}')]:
                entries=[json.loads(line) for line in original.splitlines()]
                row=next(e for e in entries if '/roster;date=' in e.get('path',''))
                digest=hashlib.sha256(content).hexdigest()
                row['sha256']=digest
                row['file']='raw/'+hashlib.sha256(row['path'].encode()).hexdigest()+'-'+digest+'.json'
                (old/row['file']).write_bytes(content)
                journal.write_text('\n'.join(json.dumps(e) for e in entries)+'\n',encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.new_collector(FakeClient(),Path(tmp)/label,resume_from=old).collect(LEAGUE,TEAM)
            journal.write_text(original+json.dumps({'kind':'unknown','path':'/anything'})+'\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'未知'):
                self.new_collector(FakeClient(),Path(tmp)/'unknown',resume_from=old).collect(LEAGUE,TEAM)

    def test_absent_account_identity_cannot_use_cache_and_same_run_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';self.new_collector(FakeClient(),old).collect(LEAGUE,TEAM)
            client=FakeClient();client.guid=''
            with self.assertRaises(YahooAuthError):
                self.new_collector(client,Path(tmp)/'new',resume_from=old).collect(LEAGUE,TEAM)
            self.assertFalse(any('/roster;' in path for path in client.calls))
            with self.assertRaisesRegex(ValueError,'不可覆寫'):
                self.new_collector(FakeClient(),old,resume_from=old)

    def test_calendar_historical_dates_are_authoritative_and_missing_week_fails(self):
        class BrokenCalendar(FakeClient):
            def get(self,path):
                response=super().get(path)
                if path=='/league/'+LEAGUE:
                    response['fantasy_content']['league'][0].update(end_week='2',current_week='2')
                return response
        with tempfile.TemporaryDirectory() as tmp:
            client=BrokenCalendar()
            with self.assertRaisesRegex(YahooError,'日曆缺漏'):
                self.new_collector(client,tmp).collect(LEAGUE,TEAM)
            self.assertIn('/game/999/game_weeks',client.calls)
            self.assertFalse(any('roster;' in p for p in client.calls))

if __name__=='__main__':unittest.main()
