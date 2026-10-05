"""Synthetic fixtures only: these are not the user's league or championship results."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fantasy_core import parse_stats, player_info, blank_season, validate_season, aggregate, awards, waiver_stints, player_totals
from season_collector import Collector
from yahoo_client import YahooError

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
    def __init__(self): self.calls=[];self.fail_day=None;self.live=False;self.final_rank=1
    def get(self,path):
        self.calls.append(path)
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
            if day==self.fail_day: raise YahooError('synthetic unavailable')
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

if __name__=='__main__':unittest.main()
