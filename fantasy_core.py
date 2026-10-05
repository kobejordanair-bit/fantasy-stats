"""Pure Yahoo parsing and season arithmetic. Unknown values remain unknown."""
from __future__ import annotations
import math
import re
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ALIASES = {'ST': 'STL', '3PM': '3PTM', '3PA': '3PTA', '3P%': '3PT%', 'TOV': 'TO'}
PAIRS = {'FG%': ('FGM', 'FGA'), 'FT%': ('FTM', 'FTA'), '3PT%': ('3PTM', '3PTA')}
HIDDEN = {'3': 'FGA', '4': 'FGM', '6': 'FTA', '7': 'FTM', '9': '3PTA', '10': '3PTM'}
BENCH = {'BN', 'IL', 'IL+', 'IR', 'IR+', 'NA'}


def valid_stat(value, key=''):
    return type(value) in {int,float} and math.isfinite(value) and 0<=value<=100000000 and ('%' not in key or value<=1)


def pair_problem(row, key):
    made,attempts=(row.get(k) for k in PAIRS[key])
    if made is None or attempts is None: return False
    if not valid_stat(made) or not valid_stat(attempts) or made>attempts: return True
    pct=row.get(key)
    if pct is None: return False
    return not valid_stat(pct,key) or (pct!=0 if attempts==0 else abs(pct-made/attempts)>0.000500001)

def resources(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for name, value in obj.items():
            if name != key:
                yield from resources(value, key)
    elif isinstance(obj, list):
        for value in obj:
            yield from resources(value, key)

def first(obj, key, default=None):
    return next(resources(obj, key), default)

def attributes(resource):
    block = resource[0] if isinstance(resource, list) and resource and isinstance(resource[0], list) else resource
    result = {}
    for item in block if isinstance(block, list) else [block]:
        if isinstance(item, dict):
            result.update(item)
    return result

def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value in {'', '-', 'N/A', 'None'}:
            return None
        if '/' in value:
            parts = value.split('/')
            if len(parts) != 2:
                return None
            made, attempt = map(number, parts)
            return made / attempt if made is not None and attempt is not None and attempt > 0 else None
        if value.endswith('%'):
            raw = number(value[:-1])
            return raw / 100 if raw is not None else None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None

def parse_stats(resource, category_map, *, coverage=None, period=None, block='player_stats'):
    raw = first(resource, block)
    if not isinstance(raw, dict):
        return {}
    found_coverage = raw.get('coverage_type') or raw.get('type')
    if coverage and found_coverage != coverage:
        return {}
    if period is not None and str(raw.get('week' if coverage == 'week' else 'date', '')) != str(period):
        return {}
    result = {}
    for stat in resources(raw.get('stats', []), 'stat'):
        if not isinstance(stat, dict):
            continue
        sid, value = str(stat.get('stat_id', '')), stat.get('value')
        key = category_map.get(sid) or HIDDEN.get(sid)
        if key:
            key = ALIASES.get(key, key)
            if '/' in str(value) and key in {'FGM/A', 'FTM/A', '3PTM/A'}:
                parts = str(value).split('/')
                if len(parts) == 2:
                    made, attempts = ('FGM', 'FGA') if key == 'FGM/A' else ('FTM', 'FTA') if key == 'FTM/A' else ('3PTM', '3PTA')
                    result[made], result[attempts] = map(number, parts)
            else:
                result[key] = number(value)
        if sid in HIDDEN:
            result[HIDDEN[sid]] = number(value)
    return result

def aggregate(rows, keys):
    rows = list(rows)
    counts = set(keys) | {key for pair in PAIRS.values() for key in pair}
    result = {}
    for key in counts:
        if '%' in key:
            continue
        values = [row.get(key) for row in rows]
        result[key] = sum(values) if rows and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in values) else None
    for key, (made, attempts) in PAIRS.items():
        if any(pair_problem(r,key) for r in rows):
            # Propagate invalid provenance through daily -> weekly -> season sums.
            # A null percentage alone could later be recomputed from corrupt totals.
            result[made]=None;result[attempts]=None
        if key in keys:
            m, a = result.get(made), result.get(attempts)
            result[key] = m / a if m is not None and a is not None and 0 <= m <= a and a > 0 and not any(pair_problem(r,key) for r in rows) else None
    return result

def player_info(resource):
    attrs = attributes(resource)
    key = attrs.get('player_key')
    name = attrs.get('name', {})
    name = name.get('full', '') if isinstance(name, dict) else str(name)
    position = first(resource, 'selected_position', {})
    if isinstance(position, list):
        position = attributes(position)
    slot = position.get('position') if isinstance(position, dict) else None
    return {'id': key or '', 'name': name, 'position': attrs.get('display_position', ''),
            'slot': slot, 'counts': slot is not None and slot not in BENCH}

def waiver_stints(transactions, daily_rows, keys, missing_dates=(), timezone_name='America/Los_Angeles'):
    """Attribute only full days after acquisition and before next departure/reacquisition."""
    result = []
    for event in transactions:
        if event['type'] != 'add':
            continue
        start = event_date(event['date'], timezone_name)
        for player in event['received']:
            future = [event_date(t['date'], timezone_name) for t in transactions if t['date'] > event['date']
                      and any(p['id'] == player['id'] for p in t['sent'] + t['received'])]
            end = min(future) if future else '9999-12-31'
            rows = [r for r in daily_rows if r['playerId'] == player['id'] and start < r['date'] < end]
            totals = aggregate([r['stats'] for r in rows], keys)
            if any(start < day < end for _, day in missing_dates):
                totals = {key: None for key in totals}
            weeks = len({r['week'] for r in rows})
            result.append({'date': start, 'name': player['name'], 'fromWeek': min((r['week'] for r in rows), default=None),
                           'weeks': weeks, 'stats': totals,
                           'weeklyAverage': {k: (v if '%' in k else v / weeks) if v is not None and weeks else None for k, v in totals.items()},
                           'method': '每日已驗證先發貢獻；取得次日至離隊前一日，不重複歸因；日期時區 '+timezone_name})
    return result

def player_week_rows(daily, keys, missing_dates=()):
    groups = defaultdict(list)
    for row in daily:
        groups[(row['week'], row['playerId'])].append(row)
    output = []
    missing_weeks = {week for week, _ in missing_dates}
    for (week, player_id), rows in groups.items():
        totals = aggregate([r['stats'] for r in rows], keys)
        if week in missing_weeks:
            totals = {key: None for key in totals}
        output.append({'week': week, 'playerId': player_id, 'name': rows[0]['name'],
                       'position': rows[0]['position'], 'scope': 'active-lineup', 'stats': totals})
    return sorted(output, key=lambda r: (r['week'], r['playerId']))


def event_date(value, timezone_name='America/Los_Angeles'):
    from zoneinfo import ZoneInfo
    if len(value) == 10:
        return value
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('交易時間必須含時區')
    return stamp.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def season_label(year):
    year = int(year)
    if not 2000 <= year <= 2100:
        raise ValueError('不支援的球季年份')
    return f'{year}-{(year+1)%100:02}'


def blank_season(year, team_name):
    return dict(schemaVersion=1, franchiseId='my-team', season=season_label(year),
                teamName=team_name, leagueName='', leagueKey='', teamKey='', finalRank=None,
                regularSeasonRank=None, isChampion=False, notes='', categories=[], teamWeeks=[],
                playerWeeks=[], players=[], matchups=[], categoryRecord=[], transactions=[],
                tradeAnalysis=[], waiverAnalysis=[], source={'kind':'yahoo-api','capturedAt':'','files':[]},
                quality={'scope':'official-team','warnings':[], 'missingWeeks':[]})


def validate_season(season):
    """Fail before publishing an incompatible or lossy schema-1 season. No coercion to zero."""
    if season.get('schemaVersion') != 1 or not re.fullmatch(r'20\d\d-\d\d|2100-01', str(season.get('season', ''))):
        raise ValueError('需要年度 schemaVersion 1、YYYY-YY 球季')
    if season_label(season['season'][:4]) != season['season']:
        raise ValueError('球季年份不一致')
    for key, limit in [('teamName',160),('leagueName',160),('leagueKey',100),('teamKey',100),('notes',6000)]:
        value = season.get(key, '')
        if not isinstance(value, str) or len(value)>limit or '\x00' in value:
            raise ValueError('文字欄位格式不正確: '+key)
    if not season.get('teamName','').strip():
        raise ValueError('球隊名稱不可留空')
    for key in ['finalRank','regularSeasonRank']:
        v=season.get(key)
        if v is not None and (type(v) is not int or not 1<=v<=1000):
            raise ValueError('名次格式不正確')
    if season.get('isChampion') and season.get('finalRank') != 1:
        raise ValueError('冠軍需官方最終名次 1')
    limits={'categories':30,'teamWeeks':60,'playerWeeks':3000,'players':200,'matchups':60,
            'categoryRecord':30,'transactions':1200,'tradeAnalysis':300,'waiverAnalysis':1000}
    for key, limit in limits.items():
        if not isinstance(season.get(key),list) or len(season[key])>limit:
            raise ValueError(f'{key} 超過平台上限 {limit}，原始資料仍可留存，禁止截斷')
    catkeys=[c['key'] for c in season['categories']]
    if len(catkeys)!=len(set(catkeys)) or any(not isinstance(k,str) or not k or len(k)>30 or k in {'__proto__','constructor','prototype'} for k in catkeys):
        raise ValueError('無效或重複類別')
    for key in ['teamWeeks','matchups','playerWeeks']:
        ids=[]
        for row in season[key]:
            if type(row.get('week')) is not int or not 1<=row['week']<=60:
                raise ValueError('週次格式不正確')
            ids.append((row['week'],row.get('playerId') or row.get('name')) if key=='playerWeeks' else row['week'])
        if len(ids)!=len(set(ids)):
            raise ValueError('重複週次紀錄: '+key)
    def walk(value):
        if isinstance(value,dict):
            for key,v in value.items():
                if key in {'date','startDate','endDate','capturedAt'} and v:
                    if not isinstance(v,str) or len(v)>40 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})?)?',v):
                        raise ValueError('日期格式不正確')
                    try: datetime.fromisoformat(v.replace('Z','+00:00'))
                    except ValueError: raise ValueError('日期不是有效日曆日期') from None
                if key in {'stats','opponentStats','weeklyAverage'}:
                    if not isinstance(v,dict) or len(v)>40:
                        raise ValueError('統計欄位超過平台限制')
                    for stat,n in v.items():
                        if not isinstance(stat,str) or not stat.strip() or len(stat)>30 or stat in {'__proto__','constructor','prototype'}:
                            raise ValueError('無效統計名稱')
                        if n is not None and not valid_stat(n,stat):
                            raise ValueError('無效統計數字: '+stat)
                    aliases=[ALIASES.get(k,k) for k in v]
                    if len(aliases)!=len(set(aliases)): raise ValueError('統計別名重複')
                    for pct in PAIRS:
                        if pair_problem(v,pct): raise ValueError('命中數不可大於出手數，命中率必須與命中／出手一致')
                walk(v)
        elif isinstance(value,list):
            for v in value: walk(v)
    walk(season)
    def check_text(v,limit,required=False):
        if not isinstance(v,str) or len(v)>limit or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',v) or required and not v.strip():
            raise ValueError('文字欄位超過限制或含無效字元')
    for c in season['categories']: check_text(c.get('name',c['key']),60,True)
    for row in season['players']+season['playerWeeks']+season['waiverAnalysis']:
        check_text(row.get('name',''),160,True)
        check_text(row.get('id',row.get('playerId','')),100)
        check_text(row.get('position',''),50)
        if 'weeks' in row and (type(row['weeks']) is not int or not 0<=row['weeks']<=60): raise ValueError('在陣週數不正確')
    for row in season['transactions']:
        check_text(row.get('id',''),100);check_text(row.get('opponent',''),160);check_text(row.get('notes',''),2000)
        for side in ['received','sent']:
            if not isinstance(row[side],list) or len(row[side])>30: raise ValueError('交易球員筆數超過限制')
            for p in row[side]:
                check_text(p.get('id',''),80);check_text(p.get('name',''),160,True)
    for row in season['tradeAnalysis']:
        for side in ['received','sent']: check_text(row.get(side,''),1500)
        check_text(row.get('method',''),300);check_text(row.get('opponent',''),160)
        if len(row['categories'])>30: raise ValueError('交易類別超過限制')
        for side in ['received','sent']:
            values={r['key']:r.get(side) for r in row['categories']}
            if len(values)!=len(row['categories']) or any(v is not None and not valid_stat(v,k) for k,v in values.items()) or any(pair_problem(values,k) for k in PAIRS):
                raise ValueError('交易比較統計不正確')
    for row in season['waiverAnalysis']: check_text(row.get('method',''),300)
    for row in season['matchups']: check_text(row.get('opponent',''),160)
    source=season.get('source',{});quality=season.get('quality',{})
    for key,limit in [('kind',40),('repository',300),('commit',64)]:check_text(source.get(key,''),limit)
    if len(source.get('files',[]))>30 or len(quality.get('warnings',[]))>50: raise ValueError('來源或提醒筆數超過限制')
    for f in source.get('files',[]):check_text(f['name'],240,True);check_text(f.get('sha256',''),64)
    for warning in quality.get('warnings',[]):check_text(warning,1200)
    check_text(quality.get('scope',''),100)
    if len(json.dumps(season,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode())>1200000:
        raise ValueError('年度 JSON 超過平台 1.2 MB 上限；未截斷資料')
    return season


def player_totals(weekly, keys):
    groups=defaultdict(list)
    for row in weekly: groups[row['playerId']].append(row)
    return [{'id':pid,'name':rows[0]['name'],'position':rows[0]['position'],'status':'依擷取期間',
             'weeks':len(rows),'stats':aggregate([r['stats'] for r in rows],keys)} for pid,rows in groups.items()]


def awards(players, categories):
    """Observed totals only. Negative category leaders mean most mistakes, never best value."""
    leaders=[]
    for c in categories:
        key=c['key']; valid=[p for p in players if p['weeks']>0 and number(p['stats'].get(key)) is not None]
        total=max(valid,key=lambda p:p['stats'][key],default=None)
        avg=max(valid,key=lambda p:p['stats'][key] if '%' in key else p['stats'][key]/p['weeks'],default=None)
        leaders.append({'category':key,'label':'最多（負向類別）' if c['lowerIsBetter'] else '最高',
                        'totalName':total['name'] if total and '%' not in key else '',
                        'total':total['stats'][key] if total and '%' not in key else None,
                        'averageName':avg['name'] if avg else '',
                        'average':(avg['stats'][key] if '%' in key else avg['stats'][key]/avg['weeks']) if avg else None})
    counts=[c for c in categories if '%' not in c['key']]
    moments={}
    for c in counts:
        vals=[p['stats'][c['key']] for p in players if number(p['stats'].get(c['key'])) is not None]
        mean=sum(vals)/len(vals) if vals else 0
        sd=(sum((v-mean)**2 for v in vals)/len(vals))**0.5 if vals else 0
        moments[c['key']]=(mean,sd)
    mvp=[]
    for p in players:
        breakdown={}
        for c in counts:
            value=number(p['stats'].get(c['key']));mean,sd=moments[c['key']]
            breakdown[c['key']]=None if value is None else ((value-mean)/sd if sd else 0)*(-1 if c['lowerIsBetter'] else 1)
        complete=bool(counts) and all(v is not None for v in breakdown.values())
        mvp.append({'name':p['name'],'weeks':p['weeks'],'score':sum(breakdown.values()) if complete else None,'categories':breakdown})
    mvp.sort(key=lambda p:(p['score'] is not None,p['score'] or 0),reverse=True)
    return leaders,mvp
