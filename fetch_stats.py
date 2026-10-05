#!/usr/bin/env python3
"""Yahoo NBA annual collector. Importing this module never logs in or accesses the network."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from fantasy_core import attributes, resources, first, season_label, validate_season
from yahoo_client import YahooClient, YahooError
from season_collector import Collector, key_checked
from season_exports import export_all

ROOT=Path(__file__).resolve().parent


def new_run(base,season,league='offline',team='export'):
    if season_label(season[:4])!=season: raise ValueError('球季格式必須為 YYYY-YY')
    if league!='offline': key_checked(league,'league')
    if team!='export': key_checked(team,'team')
    parent=Path(base).expanduser().resolve()/season/league/team
    parent.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    return Path(tempfile.mkdtemp(prefix=stamp,dir=parent))


def parser():
    p=argparse.ArgumentParser(description='Yahoo NBA 年度實際資料與 10 頁 Excel／CSV／離線 HTML 匯出')
    p.add_argument('--env-file',type=Path,default=ROOT/'.env',help='僅讀取此 env 檔；既有環境變數優先')
    p.add_argument('--token-file',type=Path,default=None,help='預設為家目錄 .fantasy-stats/yahoo-token.json，勿放 Git')
    p.add_argument('--check-config',action='store_true',help='只檢查設定是否存在，不登入、不顯示值')
    p.add_argument('--list-leagues',action='store_true',help='leagues 的相容別名；需既有授權')
    sub=p.add_subparsers(dest='command')
    sub.add_parser('check-config',help='只檢查本機設定，不連線、不顯示密鑰')
    login=sub.add_parser('login',help='手動授權；不自動擷取資料');login.add_argument('--no-browser',action='store_true')
    login.add_argument('--callback-file',type=Path,help='等待新的單次 UTF-8 callback 檔案；讀取後移除，請放在私人暫存目錄')
    login.add_argument('--callback-timeout',type=float,default=300,help='callback 檔案等待秒數，大於 0 且最多 1800，預設 300')
    sub.add_parser('leagues',help='列出登入帳號可讀取的 NBA 聯盟')
    teams=sub.add_parser('teams',help='列出指定聯盟隊伍及 Yahoo team key');teams.add_argument('--league',required=True)
    collect=sub.add_parser('collect',help='擷取某一球季的真實資料，保留每次獨立快照')
    collect.add_argument('--league',required=True);collect.add_argument('--team',required=True)
    collect.add_argument('--output',type=Path,default=ROOT/'reports')
    collect.add_argument('--resume',type=Path,help='從同帳號／聯盟／球隊的舊 run 複用已驗證快取，仍建立全新快照')
    collect.add_argument('--timezone',default='America/Los_Angeles',help='Yahoo 比賽日／交易日期口徑，預設美國太平洋時間')
    collect.add_argument('--skip-trade-stats',action='store_true',help='保留交易紀錄但暫不擷取雙方每日比較數據')
    export=sub.add_parser('export',help='只從 schema 1 年度 JSON 重建報表，完全離線')
    export.add_argument('json_file',type=Path);export.add_argument('--output',type=Path,default=ROOT/'reports')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.check_config: args.command='check-config'
    elif args.list_leagues: args.command='leagues'
    if not args.command: parser().error('請指定命令，例如 --check-config 或 --help')
    client=None;out=None
    try:
        if args.command=='export':
            if args.json_file.stat().st_size>5_000_000: raise ValueError('JSON 檔案過大')
            season=validate_season(json.loads(args.json_file.read_text(encoding='utf-8-sig')))
            out=new_run(args.output,season['season']);export_all(season,out)
            print('已離線匯出：'+str(out));return 0
        from dotenv import load_dotenv
        load_dotenv(args.env_file,override=False)
        if args.command!='check-config' and (not os.getenv('YAHOO_CLIENT_ID') or not os.getenv('YAHOO_CLIENT_SECRET')):
            raise YahooError('請設定 YAHOO_CLIENT_ID 與 YAHOO_CLIENT_SECRET。')
        token=args.token_file or Path(os.getenv('YAHOO_TOKEN_FILE',str(Path.home()/'.fantasy-stats'/'yahoo-token.json')))
        if args.command=='check-config':
            configured=bool(os.getenv('YAHOO_CLIENT_ID')) and bool(os.getenv('YAHOO_CLIENT_SECRET'))
            print('OAuth 應用程式設定：'+('齊全' if configured else '缺少 YAHOO_CLIENT_ID 或 YAHOO_CLIENT_SECRET'))
            print('本機 token：'+('檔案存在（未連線驗證）' if token.expanduser().is_file() else '尚未登入'))
            print('此檢查不登入、不建立 OAuth 應用程式，也不顯示任何密鑰。')
            return 0 if configured else 1
        client=YahooClient(os.getenv('YAHOO_CLIENT_ID',''),os.getenv('YAHOO_CLIENT_SECRET',''),
                           os.getenv('YAHOO_REDIRECT_URI','https://localhost:8080'),token)
        if args.command=='login':
            client.login(open_browser=not args.no_browser,callback_file=args.callback_file,
                         callback_timeout=args.callback_timeout)
            print('Yahoo 授權完成，token 僅存於本機。');return 0
        if args.command=='leagues':
            data=client.get('/users;use_login=1/games;game_codes=nba/leagues')
            leagues=list(resources(data,'league'))
            if not leagues: print('此 Yahoo 授權帳號沒有可讀取的 NBA 聯盟；請核對冠軍隊所屬帳號。')
            for raw in leagues:
                r=attributes(raw);print(f"{r.get('league_key','')}  {r.get('season','')}  {r.get('name','')}")
            return 0
        key_checked(args.league,'league')
        if args.command=='teams':
            data=client.get('/league/'+args.league+'/teams')
            for raw in resources(data,'team'):
                r=attributes(raw);owned='（你的球隊）' if str(r.get('is_owned_by_current_login'))=='1' else ''
                print(f"{r.get('team_key','')}  {r.get('name','')} {owned}")
            return 0
        key_checked(args.team,'team')
        meta=attributes(first(client.get('/league/'+args.league),'league',{}))
        season=season_label(meta['season']);out=new_run(args.output,season,args.league,args.team)
        collector=Collector(client,out,timezone_name=args.timezone,resume_from=args.resume)
        result=collector.collect(args.league,args.team,include_trades=not args.skip_trade_stats)
        export_all(result,out)
        (out/'run-status.json').write_text(json.dumps({'status':'complete','warnings':len(result['quality']['warnings'])}),encoding='utf-8')
        print('已保存年度資料：'+str(out))
        print('網站匯入請選 season.json；CSV／Excel／HTML 為離線報表。')
        if result['quality']['warnings']: print('有資料缺漏或口徑提醒，請查看 season.json 與 collection.json。')
        return 0
    except (YahooError,ValueError,KeyError,TypeError,OSError) as error:
        if out:
            (out/'run-status.json').write_text(json.dumps({'status':'incomplete','errorType':type(error).__name__}),encoding='utf-8')
        print('未完成：'+(str(error) if isinstance(error,(YahooError,ValueError)) else '本機設定或資料格式無法處理。'))
        if out: print('已擷取的原始資料保留在：'+str(out))
        return 1
    finally:
        if client: client.close()


if __name__=='__main__':
    import sys
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
