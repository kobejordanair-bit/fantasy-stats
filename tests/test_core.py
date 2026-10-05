import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fantasy_core import number, aggregate, parse_stats, player_info, waiver_stints, player_week_rows

class CoreTests(unittest.TestCase):
    def test_unknown_and_finite(self):
        for value in ('-', '', 'NaN', 'Infinity', None, True): self.assertIsNone(number(value))
        self.assertEqual(number('0'), 0)
        self.assertEqual(number('5/10'), 0.5)
    def test_no_double_count_and_weighted_percentage(self):
        rows=[{'FTM':5,'FTA':10,'3PTM':4,'3PTA':10,'PTS':10},{'FTM':8,'FTA':10,'3PTM':2,'3PTA':5,'PTS':30}]
        total=aggregate(rows,['FTM','FT%','3PTM','3PT%','PTS'])
        self.assertEqual(total['FTM'],13);self.assertEqual(total['FT%'],13/20)
        self.assertEqual(total['3PTM'],6);self.assertEqual(total['3PT%'],6/15)
    def test_single_resource_and_coverage(self):
        data={'fantasy_content':{'player':[[{'player_key':'123.p.1'},{'name':{'full':'A'}}],{'player_stats':{'coverage_type':'week','week':'2','stats':[{'stat':{'stat_id':'12','value':'0'}}]}}]}}
        self.assertEqual(parse_stats(data,{'12':'PTS'},coverage='week',period=2),{'PTS':0})
        self.assertEqual(parse_stats(data,{'12':'PTS'},coverage='week',period=1),{})
    def test_bench_never_counts(self):
        player=[[{'player_key':'1.p.1'},{'name':{'full':'A'}}],{'selected_position':{'position':'BN'}}]
        self.assertFalse(player_info(player)['counts'])
        player[1]['selected_position']['position']='PG';self.assertTrue(player_info(player)['counts'])
    def test_reacquisitions_stop_old_stint(self):
        p={'id':'1','name':'A'}
        events=[{'type':'add','date':'2025-01-01','received':[p],'sent':[]},{'type':'drop','date':'2025-01-04','received':[],'sent':[p]},{'type':'add','date':'2025-01-08','received':[p],'sent':[]}]
        rows=[{'date':'2025-01-02','week':1,'playerId':'1','stats':{'PTS':10}},{'date':'2025-01-09','week':2,'playerId':'1','stats':{'PTS':30}}]
        result=waiver_stints(events,rows,['PTS']);self.assertEqual([r['stats']['PTS'] for r in result],[10,30])
    def test_missing_day_does_not_claim_complete_week(self):
        rows=[{'date':'2025-01-02','week':1,'playerId':'1','name':'A','position':'PG','stats':{'PTS':10}}]
        self.assertIsNone(player_week_rows(rows,['PTS'],[(1,'2025-01-03')])[0]['stats']['PTS'])
if __name__=='__main__':unittest.main()
