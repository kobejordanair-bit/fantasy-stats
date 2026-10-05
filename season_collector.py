"""Annual read-only collection: official team scores and daily player attribution stay separate."""
from __future__ import annotations
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import re
from fantasy_core import (ALIASES, PAIRS, attributes, resources, first, number, parse_stats,
                          player_info, blank_season, player_week_rows, player_totals,
                          waiver_stints, aggregate, event_date, validate_season)
from yahoo_client import YahooError

NEGATIVE={'TO','PF','TECH','FF'}


def key_checked(value, kind):
    pattern=r'\d+\.l\.\d+' if kind=='league' else r'\d+\.l\.\d+\.t\.\d+'
    if not re.fullmatch(pattern,value): raise ValueError('Yahoo '+kind+' key 格式不正確')
    return value


def dates_between(start,end):
    value=date.fromisoformat(start);last=date.fromisoformat(end)
    if last<value or (last-value).days>370: raise ValueError('Yahoo 日曆日期範圍不正確')
    while value<=last:
        yield value.isoformat();value+=timedelta(days=1)


class Collector:
    def __init__(self, client, run_dir, *, timezone_name='America/Los_Angeles', today=None, progress=print):
        self.client=client;self.run_dir=Path(run_dir);self.raw=self.run_dir/'raw'
        self.raw.mkdir(parents=True,exist_ok=True)
        self.timezone_name=timezone_name;self.today=today or datetime.now(ZoneInfo(timezone_name)).date()
        self.progress=progress;self.cache={};self.manifest=[];self.warnings=[]

    def warn(self,message):
        if message not in self.warnings: self.warnings.append(message)

    def get(self,path):
        if path in self.cache: return self.cache[path]
        data=self.client.get(path)
        content=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        name=hashlib.sha256(path.encode()).hexdigest()+'.json'
        (self.raw/name).write_bytes(content)
        self.manifest.append({'path':path,'file':'raw/'+name,'sha256':hashlib.sha256(content).hexdigest()})
        self.cache[path]=data
        return data

    def discover(self):
        data=self.get('/users;use_login=1/games;game_codes=nba/leagues')
        return [attributes(r) for r in resources(data,'league')]

    def categories(self,settings):
        mapping={};cats=[]
        for stat in resources(first(settings,'stat_categories',{}),'stat'):
            if not isinstance(stat,dict) or str(stat.get('enabled','1'))!='1': continue
            sid=str(stat.get('stat_id',''));label=stat.get('display_name') or stat.get('abbr') or stat.get('name')
            if not sid or not label: continue
            key=ALIASES.get(label,label);mapping[sid]=key
            if str(stat.get('is_only_display_stat','0'))=='1' or key in {'FGM/A','FTM/A','3PTM/A'}: continue
            if '/' in key or '%' in key and key not in PAIRS:
                raise YahooError('尚未支援此比例類別 '+key+'，停止以免把比例錯誤相加。')
            sort=str(stat.get('sort_order',''))
            lower=sort=='0' if sort in {'0','1'} else key in NEGATIVE
            if key not in {c['key'] for c in cats}:
                cats.append({'key':key,'name':key,'lowerIsBetter':lower,'kind':'percentage' if '%' in key else 'count'})
        if not cats: raise YahooError('Yahoo 未提供已啟用的聯盟類別。')
        return mapping,cats

    def calendar(self,game_key,start,end):
        data=self.get('/game/'+game_key+'/game_weeks')
        weeks=[]
        for r in resources(data,'game_week'):
            a=attributes(r)
            try:
                week=int(a['week']);begin=a['start'];finish=a['end']
                list(dates_between(begin,finish))
            except (ValueError,KeyError,TypeError): raise YahooError('Yahoo 週次日曆格式不正確。') from None
            if start<=week<=end: weeks.append({'week':week,'startDate':begin,'endDate':finish})
        if {w['week'] for w in weeks}!=set(range(start,end+1)):
            raise YahooError('Yahoo 週次日曆缺漏，停止以免猜測交易或比賽日期。')
        weeks.sort(key=lambda w:w['week'])
        if len({w['week'] for w in weeks})!=len(weeks): raise YahooError('Yahoo 日曆週次重複。')
        days=[day for w in weeks for day in dates_between(w['startDate'],w['endDate'])]
        if len(days)!=len(set(days)): raise YahooError('Yahoo 週次日期重疊，停止以免重複計算。')
        return weeks

    def scoreboard(self,league_key,team_key,week,mapping,categories):
        data=self.get(f'/league/{league_key}/scoreboard;week={week}')
        for m in resources(data,'matchup'):
            if not isinstance(m,dict): continue
            if m.get('week') is not None and str(m['week'])!=str(week):
                raise YahooError('Yahoo 比分板週次不符。')
            teams=[]
            for team in resources(m,'team'):
                a=attributes(team)
                teams.append({'key':a.get('team_key'),'name':a.get('name',''),
                              'stats':parse_stats(team,mapping,coverage='week',period=week,block='team_stats')})
            mine=next((t for t in teams if t['key']==team_key),None)
            if mine is None: continue
            other=next((t for t in teams if t['key']!=team_key),None)
            status={'postevent':'final','midevent':'live','preevent':'unknown'}.get(m.get('status'),'unknown')
            phase='playoffs' if str(m.get('is_playoffs'))=='1' else 'regular' if str(m.get('is_playoffs'))=='0' else 'unknown'
            result=None;category_results={}
            if status=='final':
                winner=m.get('winner_team_key')
                if str(m.get('is_tied','0'))=='1': result='T'
                elif winner==team_key: result='W'
                elif other and winner==other['key']: result='L'
                for row in resources(m.get('stat_winners',[]),'stat_winner'):
                    key=mapping.get(str(row.get('stat_id')))
                    if key not in {c['key'] for c in categories}: continue
                    if str(row.get('is_tied','0'))=='1': category_results[key]='T'
                    elif row.get('winner_team_key')==team_key: category_results[key]='W'
                    elif other and row.get('winner_team_key')==other['key']: category_results[key]='L'
            count=Counter(category_results.values());complete=len(category_results)==len(categories)
            return {'week':week,'opponent':other['name'] if other else '', 'status':status,'phase':phase,'result':result,
                    'categoryWins':count['W'] if complete else None,'categoryLosses':count['L'] if complete else None,
                    'categoryTies':count['T'] if complete else None,'stats':mine['stats'],
                    'opponentStats':other['stats'] if other else {},'categoryResults':category_results}
        return None

    def daily_lineup(self,team_key,week,day,mapping):
        data=self.get(f'/team/{team_key}/roster;date={day}/players/stats;type=date;date={day}')
        roster=first(data,'roster',{})
        coverage=first(roster,'coverage_type');actual_date=first(roster,'date')
        if coverage!='date' or str(actual_date)!=day:
            raise YahooError('Yahoo 名單日期未能驗證。')
        players=first(roster,'players')
        if players is None: raise YahooError('Yahoo 名單缺少 players。')
        rows=[]
        for p in resources(players,'player'):
            info=player_info(p)
            if not info['id'] or not info['name']: raise YahooError('球員識別資料缺漏。')
            selected=first(p,'selected_position',{})
            if isinstance(selected,list): selected=attributes(selected)
            verified=(isinstance(selected,dict) and selected.get('coverage_type')=='date'
                      and str(selected.get('date'))==day and info['slot'] is not None)
            if not verified: raise YahooError('球員 selected_position 日期未能驗證。')
            stats=parse_stats(p,mapping,coverage='date',period=day)
            if info['counts'] and not stats:
                raise YahooError('先發球員當日統計缺漏或日期未能驗證。')
            rows.append({'date':day,'week':week,'playerId':info['id'],'name':info['name'],
                         'position':info['position'],'slot':info['slot'],'active':info['counts'],
                         'stats':stats})
        if len({r['playerId'] for r in rows})!=len(rows): raise YahooError('單日名單包含重複球員。')
        return rows

    def transactions(self,league_key,team_key):
        # Yahoo documents this collection as all completed transactions. `start`
        # is not a documented transaction filter, so do not guess player pagination.
        data=self.get(f'/league/{league_key}/transactions;team_key={team_key}')
        container=first(data,'transactions')
        if container is None: raise YahooError('Yahoo 異動紀錄缺少 transactions。')
        batch=list(resources(container,'transaction'))
        if len(batch)>10000: raise YahooError('異動紀錄超過可處理上限，原始回應已保存。')
        if isinstance(container,dict) and 'count' in container and int(container['count'])!=len(batch):
            raise YahooError('Yahoo 異動紀錄筆數不符，未宣稱完整。')
        events=[];seen=set()
        for transaction in batch:
            a=attributes(transaction);tid=str(a.get('transaction_key',''))
            if not tid: raise YahooError('Yahoo 異動紀錄缺少 transaction_key。')
            if tid in seen: continue
            seen.add(tid)
            if a.get('status')!='successful': continue
            try: stamp=datetime.fromtimestamp(int(a['timestamp']),timezone.utc).isoformat().replace('+00:00','Z')
            except (KeyError,TypeError,ValueError,OverflowError):
                raise YahooError('Yahoo 異動時間不正確。') from None
            received=[];sent=[];opponents=set()
            for p in resources(transaction,'player'):
                info=player_info(p);tx=first(p,'transaction_data',{})
                if isinstance(tx,list): tx=attributes(tx)
                if isinstance(tx,dict) and 'transaction_data' in tx: tx=tx['transaction_data']
                if not isinstance(tx,dict) or not info['id'] or not info['name']:
                    raise YahooError('Yahoo 異動球員或來源資料缺漏。')
                ref={'id':info['id'],'name':info['name']}
                if tx.get('destination_team_key')==team_key:
                    received.append(ref)
                    if tx.get('source_team_name'): opponents.add(tx['source_team_name'])
                if tx.get('source_team_key')==team_key:
                    sent.append(ref)
                    if tx.get('destination_team_name'): opponents.add(tx['destination_team_name'])
            if a.get('type')=='trade':
                if received or sent: events.append({'id':tid,'date':stamp,'type':'trade','opponent':', '.join(sorted(opponents)),
                                                   'received':received,'sent':sent,'notes':''})
            else:
                if received: events.append({'id':tid+':add','date':stamp,'type':'add','opponent':'','received':received,'sent':[],'notes':''})
                if sent: events.append({'id':tid+':drop','date':stamp,'type':'drop','opponent':'','received':[],'sent':sent,'notes':''})
        return sorted(events,key=lambda r:(r['date'],r['id']))

    def trade_comparisons(self,events,calendar,mapping,categories):
        results=[];keys=[c['key'] for c in categories]
        eligible=[(w['week'],day) for w in calendar for day in dates_between(w['startDate'],w['endDate'])
                  if date.fromisoformat(w['endDate'])<self.today]
        for event in events:
            if event['type']!='trade': continue
            start=event_date(event['date'],self.timezone_name)
            period=[(week,day) for week,day in eligible if day>start]
            totals=[]
            for side in ['received','sent']:
                samples=[]
                for p in event[side]:
                    if not re.fullmatch(r'\d+\.p\.\d+',p['id']): raise YahooError('交易球員 key 格式不正確。')
                    for week,day in period:
                        try:
                            data=self.get(f'/player/{p["id"]}/stats;type=date;date={day}')
                            value=parse_stats(data,mapping,coverage='date',period=day)
                        except YahooError:
                            value={};self.warn('交易比較部分每日 NBA 資料缺漏；受影響類別維持未知。')
                        samples.append(value)
                totals.append(aggregate(samples,keys))
            comparison=[]
            for c in categories:
                left,right=(t.get(c['key']) for t in totals);result=None
                if left is not None and right is not None:
                    result='tie' if left==right else 'better' if (left<right if c['lowerIsBetter'] else left>right) else 'worse'
                comparison.append({'key':c['key'],'received':left,'sent':right,'result':result})
            results.append({'date':event['date'],'opponent':event['opponent'],
                            'received':', '.join(p['name'] for p in event['received']),
                            'sent':', '.join(p['name'] for p in event['sent']),
                            'fromWeek':period[0][0] if period else None,
                            'method':'双方同一日期窗之完整 NBA 表現；取得次日至擷取期間最後完整週，不限持有球隊；時區 '+self.timezone_name,
                            'categories':comparison})
        return results

    def collect(self,league_key,team_key,*,include_trades=True):
        key_checked(league_key,'league');key_checked(team_key,'team')
        if not team_key.startswith(league_key+'.t.'): raise ValueError('隊伍不屬於指定聯盟')
        metadata=attributes(first(self.get('/league/'+league_key),'league',{}))
        if metadata.get('game_code') not in {None,'nba'}: raise ValueError('只支援 NBA')
        year=int(metadata['season']);start=int(metadata['start_week']);end=int(metadata['end_week'])
        current=int(metadata.get('current_week') or end);end=min(end,current)
        if not 1<=start<=end<=60: raise YahooError('Yahoo 尚無可擷取的週次。')
        team=attributes(first(self.get('/team/'+team_key),'team',{}))
        if team.get('team_key')!=team_key or not team.get('name'): raise YahooError('Yahoo 球隊資料不符。')
        settings=self.get('/league/'+league_key+'/settings');mapping,categories=self.categories(settings)
        calendar=self.calendar(league_key.split('.')[0],start,end)
        season=blank_season(year,team['name']);season.update(leagueName=metadata.get('name',''),leagueKey=league_key,teamKey=team_key,categories=categories)
        keys=[c['key'] for c in categories];daily=[];missing=[];missing_weeks=[]
        for w in calendar:
            week=w['week'];self.progress(f'第 {week} 週：官方比分與每日名單')
            try:
                matchup=self.scoreboard(league_key,team_key,week,mapping,categories)
                if matchup:
                    season['matchups'].append(matchup)
                    season['teamWeeks'].append({**w,'status':matchup['status'],'phase':matchup['phase'],'stats':matchup['stats']})
                    if not matchup['stats']:
                        missing_weeks.append(week)
                        self.warn('部分官方球隊統計缺漏或覆蓋日期不符；保留官方勝負但不猜測數值。')
                else:
                    # Bye weeks have team statistics but no fabricated matchup or final status.
                    data=self.get(f'/team/{team_key}/stats;type=week;week={week}')
                    stats=parse_stats(data,mapping,coverage='week',period=week,block='team_stats')
                    season['teamWeeks'].append({**w,'status':'unknown','phase':'unknown','stats':stats})
                    self.warn('無對戰或輪空週保留官方隊伍統計，但不自行判定結算。')
            except YahooError:
                missing_weeks.append(week)
                season['teamWeeks'].append({**w,'status':'unknown','phase':'unknown','stats':{}})
                self.warn('部分官方比分讀取失敗；缺漏週次保留未知，可重新擷取。')
            if date.fromisoformat(w['endDate'])>=self.today:
                self.warn('進行中週次保留官方比分狀態；球員貢獻與交易比較只含已完整結束的週次。');continue
            for day in dates_between(w['startDate'],w['endDate']):
                try: daily.extend(self.daily_lineup(team_key,week,day,mapping))
                except YahooError: missing.append((week,day))
        # Preserve bench-only players in the roster. A verified bench slot contributes
        # exactly zero fantasy counts, irrespective of the player's NBA box score.
        contribution_keys=set(keys)|{k for pair in PAIRS.values() for k in pair}
        contributions=[r if r['active'] else {**r,'stats':{k:None if '%' in k else 0 for k in contribution_keys}} for r in daily]
        if missing: self.warn('部分每日名單或 selected_position 日期未能驗證；受影響整週球員總數與補人歸因保持未知，詳見 collection.json。')
        season['playerWeeks']=player_week_rows(contributions,keys,missing)
        season['players']=player_totals(season['playerWeeks'],keys)
        try: season['transactions']=self.transactions(league_key,team_key)
        except YahooError:
            self.warn('異動紀錄擷取失敗，未宣稱交易／補人完整；請重新擷取。')
        if include_trades:
            season['tradeAnalysis']=self.trade_comparisons(season['transactions'],calendar,mapping,categories)
        else: self.warn('本次選擇跳過交易表現擷取，交易紀錄仍保留。')
        season['waiverAnalysis']=waiver_stints(season['transactions'],contributions,keys,missing,self.timezone_name)
        for c in categories:
            values=[m['categoryResults'].get(c['key']) for m in season['matchups'] if m['status']=='final']
            counts=Counter(values)
            season['categoryRecord'].append({'key':c['key'],'wins':counts['W'],'losses':counts['L'],'ties':counts['T'],'status':'final'})
        # Rank is only final after Yahoo explicitly closes the league.
        if str(metadata.get('is_finished'))=='1':
            try:
                standings=self.get('/league/'+league_key+'/standings')
                for t in resources(standings,'team'):
                    if attributes(t).get('team_key')==team_key:
                        rank=number(first(first(t,'team_standings',{}),'rank'))
                        if rank is not None and rank.is_integer() and rank>=1:
                            season['finalRank']=int(rank);season['isChampion']=rank==1
            except YahooError: self.warn('官方最終名次無法取得，未推定冠軍。')
        captured=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        season['source'].update(capturedAt=captured,repository='fantasy-stats',files=[])
        season['quality']['warnings']=self.warnings[:50]
        season['quality']['missingWeeks']=sorted(set(missing_weeks)|{w for w,d in missing})
        season['notes']='球隊統計採官方 scoreboard；球員為每日已驗證 active lineup。兩者可能因 Yahoo 修正、出賽限制或缺漏而不同。日期時區 '+self.timezone_name
        evidence={'capturedAt':captured,'calendar':calendar,'dailyRows':daily,'missingDates':missing,'warnings':self.warnings}
        (self.run_dir/'collection.json').write_text(json.dumps(evidence,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
        manifest={'provider':'Yahoo Fantasy Sports API','requests':self.manifest}
        manifest_bytes=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
        (self.run_dir/'source-manifest.json').write_bytes(manifest_bytes)
        season['source']['files']=[{'name':'source-manifest.json','sha256':hashlib.sha256(manifest_bytes).hexdigest()}]
        return validate_season(season)
