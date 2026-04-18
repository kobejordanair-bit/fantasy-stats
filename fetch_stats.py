#!/usr/bin/env python3
"""
Yahoo Fantasy Basketball - 整季完整分析
"""

import html as html_lib
import requests, json, csv, os, time, webbrowser
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

# ==================== 填入你的資料 ====================
CLIENT_ID     = os.getenv("YAHOO_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET", "")
# ======================================================

REDIRECT_URI = "https://localhost:8080"
AUTH_URL     = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL    = "https://api.login.yahoo.com/oauth2/get_token"
BASE_URL     = "https://fantasysports.yahooapis.com/fantasy/v2"
TOKEN_FILE   = ".yahoo_token.json"

PCT_COLS = {
    "FG%":  ("FGM", "FGA"),
    "3PT%": ("3PTM", "3PTA"),
    "FT%":  ("FTM", "FTA"),
}
HIDDEN_STATS = {
    "3": "FGA", "4": "FGM",
    "6": "FTA", "7": "FTM",
    "9": "3PTA", "10": "3PTM",
}
NEGATIVE_COLS = {"TO", "PF", "TECH", "FF"}


def _find_players_block(obj):
    """遞迴尋找 Yahoo API 回傳結構中的 players 字典（含 count + 索引鍵）。"""
    if isinstance(obj, dict):
        if "0" in obj and isinstance(obj["0"], dict) and "player" in obj["0"]:
            return obj
        for v in obj.values():
            r = _find_players_block(v)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_players_block(item)
            if r:
                return r
    return None


def _find_team_stats(obj):
    """遞迴尋找 Yahoo API 回傳結構中的 team_stats 區塊。"""
    if isinstance(obj, dict):
        if "team_stats" in obj:
            return obj["team_stats"]
        for v in obj.values():
            r = _find_team_stats(v)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_team_stats(item)
            if r:
                return r
    return None


def _save_token(data):
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)


def _do_oauth_flow():
    auth_url = (f"{AUTH_URL}?client_id={CLIENT_ID}"
                f"&redirect_uri={REDIRECT_URI}&response_type=code")
    print("\n" + "=" * 55)
    print("瀏覽器即將開啟，請登入 Yahoo 並授權。")
    print("授權後跳到無法連線頁面，把完整 URL 貼到這裡。")
    print("=" * 55)
    input("\n按 Enter 開啟瀏覽器...")
    webbrowser.open(auth_url)
    redirect_url = input("\n貼上完整 URL：").strip()
    code = parse_qs(urlparse(redirect_url).query).get("code", [None])[0]
    if not code:
        raise ValueError("找不到 code")
    print("\n正在取得 token...")
    resp = requests.post(TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        auth=(CLIENT_ID, CLIENT_SECRET))
    if resp.status_code != 200:
        raise ValueError(f"Token 失敗: {resp.text}")
    data = resp.json()
    _save_token(data)
    print("授權成功！\n")
    return data["access_token"]


def authorize():
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                saved = json.load(f)
            if time.time() < saved.get("expires_at", 0) - 60:
                print("使用已儲存的 token\n")
                return saved["access_token"]
            if "refresh_token" in saved:
                print("Token 已過期，嘗試自動更新...")
                resp = requests.post(TOKEN_URL,
                    data={"grant_type": "refresh_token",
                          "refresh_token": saved["refresh_token"]},
                    auth=(CLIENT_ID, CLIENT_SECRET))
                if resp.status_code == 200:
                    data = resp.json()
                    _save_token(data)
                    print("Token 更新成功！\n")
                    return data["access_token"]
                print(f"Refresh 失敗（{resp.status_code}），重新授權...")
        except Exception as e:
            print(f"Token 載入失敗（{e}），重新授權...")
    return _do_oauth_flow()


def api_get(token, path, retries=3):
    url = f"{BASE_URL}{path}?format=json"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if resp.status_code == 401:
                raise ValueError("Token 過期，請重新執行")
            if resp.status_code == 999:
                print(f"\n    Yahoo 限速，等待 15 秒...")
                time.sleep(15)
                continue
            if resp.status_code != 200:
                raise ValueError(f"API 錯誤 {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        except ValueError:
            raise
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 5
                print(f"\n    連線失敗，{wait} 秒後重試...")
                time.sleep(wait)
            else:
                raise


def get_leagues(token):
    data = api_get(token, "/users;use_login=1/games;game_codes=nba/leagues")
    games = data["fantasy_content"]["users"]["0"]["user"][1]["games"]
    leagues = []
    for i in range(games["count"]):
        game = games[str(i)]["game"]
        game_leagues = game[1].get("leagues", {})
        for j in range(game_leagues.get("count", 0)):
            lg = game_leagues[str(j)]["league"][0]
            leagues.append({"key": lg["league_key"], "name": lg["name"],
                            "season": lg["season"], "num_teams": lg.get("num_teams", "?")})
    return leagues


def get_my_team_key(token, league_key):
    user_data = api_get(token, "/users;use_login=1")
    my_guid = user_data["fantasy_content"]["users"]["0"]["user"][0]["guid"]
    data = api_get(token, f"/league/{league_key}/teams")
    teams = data["fantasy_content"]["league"][1]["teams"]
    for i in range(teams["count"]):
        team_raw = teams[str(i)]["team"][0]
        team_key = team_name = None
        is_mine = False
        for attr in team_raw:
            if not isinstance(attr, dict): continue
            if "team_key" in attr: team_key = attr["team_key"]
            if "name" in attr: team_name = attr["name"]
            if "managers" in attr:
                managers = attr["managers"]
                mgr_list = managers if isinstance(managers, list) else [
                    managers[k] for k in managers if k != "count" and isinstance(managers[k], dict)]
                for mgr_item in mgr_list:
                    mgr = mgr_item.get("manager", mgr_item)
                    if mgr.get("guid") == my_guid:
                        is_mine = True
        if is_mine:
            print(f"找到你的隊伍：{team_name}（{team_key}）")
            return team_key, team_name
    raise ValueError("找不到你的隊伍")


def get_week_range(token, league_key):
    data = api_get(token, f"/league/{league_key}")
    lg = data["fantasy_content"]["league"][0]
    start = int(lg.get("start_week", 1))
    end = int(lg.get("end_week", 24))
    current = int(lg.get("current_week", end))
    last = min(end, current)
    print(f"聯盟週數：第 {start} 週 ～ 第 {last} 週")
    return start, last


def get_stat_categories(token, league_key):
    data = api_get(token, f"/league/{league_key}/settings")
    settings_raw = data["fantasy_content"]["league"][1]["settings"]
    stat_cats_block = {}
    if isinstance(settings_raw, dict):
        stat_cats_block = settings_raw
    elif isinstance(settings_raw, list):
        for item in settings_raw:
            if isinstance(item, dict) and "stat_categories" in item:
                stat_cats_block = item
                break
    stat_list = stat_cats_block.get("stat_categories", {}).get("stats", [])
    stat_map = {}
    for item in stat_list:
        s = item.get("stat", {})
        sid = str(s.get("stat_id", ""))
        name = s.get("display_name") or s.get("abbr") or s.get("name") or sid
        if str(s.get("enabled", "1")) == "1" and sid:
            stat_map[sid] = name
    print(f"統計類別（{len(stat_map)} 項）：{', '.join(stat_map.values())}")
    return stat_map


def get_player_info(player_data):
    pid = name = pos = pkey = None
    info_block = player_data[0] if isinstance(player_data, list) else []
    if isinstance(info_block, list):
        for item in info_block:
            if not isinstance(item, dict): continue
            if "player_id" in item: pid = str(item["player_id"])
            if "player_key" in item: pkey = item["player_key"]
            if "name" in item and isinstance(item["name"], dict):
                name = item["name"].get("full")
            if "display_position" in item: pos = item["display_position"]
    return pid, name, pos or "UTIL", pkey


def parse_stats(player_data, stat_map):
    """
    解析 player_data 裡的統計數字。
    過濾 Yahoo 偷塞的整季數據（coverage_type=season），
    但不對數值做任何「是否為零」的判斷，讓真實零出賽保持為零。
    """
    stats = {}

    def find_player_stats(obj):
        if isinstance(obj, dict):
            if "player_stats" in obj: return obj["player_stats"]
            for k, v in obj.items():
                if k != "player_projected_stats":
                    r = find_player_stats(v)
                    if r: return r
        elif isinstance(obj, list):
            for item in obj:
                r = find_player_stats(item)
                if r: return r
        return None

    p_stats = find_player_stats(player_data)
    if not p_stats: return stats

    # 過濾整季累積數據，只要週次數據
    coverage = p_stats.get("coverage_type", "")
    if coverage == "season" or str(p_stats.get("type", "")) == "season":
        return {}

    raw = p_stats.get("stats", [])
    s_list = [v for k, v in raw.items() if str(k) != "count" and isinstance(v, dict)] \
             if isinstance(raw, dict) else raw

    for stat_obj in s_list:
        if not isinstance(stat_obj, dict) or "stat" not in stat_obj: continue
        s = stat_obj["stat"]
        sid = str(s.get("stat_id", ""))
        raw_val = str(s.get("value", "0")).strip()
        val = "0" if raw_val in ("-", "", "None", "N/A") else raw_val
        if sid in stat_map:
            col = stat_map[sid]
            stats[col] = val
            if "/" in val:
                try:
                    made_s, att_s = val.split("/")
                    made, att = float(made_s), float(att_s)
                    if col.startswith("3"):
                        stats["3PTM"] = str(made); stats["3PTA"] = str(att)
                    elif "FT" in col:
                        stats["FTM"] = str(made); stats["FTA"] = str(att)
                    else:
                        stats["FGM"] = str(made); stats["FGA"] = str(att)
                except ValueError:
                    pass
        if sid in HIDDEN_STATS:
            stats[HIDDEN_STATS[sid]] = val
    return stats


def get_roster_week_stats(token, team_key, week, stat_map):
    url = f"/team/{team_key}/roster;week={week}/players/stats;type=week;week={week}"
    data = api_get(token, url)
    result = {}
    player_blobs = []
    week_date = None

    def find_date(obj):
        nonlocal week_date
        if isinstance(obj, dict):
            if "date" in obj and week_date is None:
                week_date = obj["date"]
            for v in obj.values(): find_date(v)
        elif isinstance(obj, list):
            for item in obj: find_date(item)

    find_date(data)

    def hunt_players(obj):
        if isinstance(obj, dict):
            if ("count" in obj and "0" in obj
                    and isinstance(obj["0"], dict) and "player" in obj["0"]):
                for j in range(int(obj["count"])):
                    player_blobs.append(obj[str(j)]["player"])
                return
            for v in obj.values(): hunt_players(v)
        elif isinstance(obj, list):
            for item in obj: hunt_players(item)

    hunt_players(data)

    name_to_pkey = {}
    for player_data in player_blobs:
        pid, name, pos, pkey = get_player_info(player_data)
        stats = parse_stats(player_data, stat_map)
        if pid and name:
            result[pid] = {"name": name, "pos": pos, "stats": stats, "player_key": pkey}
            if name and pkey:
                name_to_pkey[name] = pkey

    return result, week_date, name_to_pkey


def is_pct_col(col): return "%" in col
def to_float(val, default=0.0):
    try:
        # 防呆處理：有時候 Yahoo 會用 "12/25" 這種字串來表示命中率
        if isinstance(val, str) and "/" in val:
            m_s, a_s = val.split("/")
            return float(m_s) / float(a_s) if float(a_s) > 0 else default
        return float(val)
    except (ValueError, TypeError, ZeroDivisionError):
        return default


def fetch_all_weeks(token, team_key, start_week, end_week, stat_map):
    player_totals = defaultdict(lambda: {"name": "", "pos": "", "weeks": 0})
    team_weekly_summary = []
    player_weekly_detail = []
    stat_cols = list(stat_map.values())
    week_dates = {}
    global_name_to_pkey = {}

    try:
        final_roster, _, _ = get_roster_week_stats(token, team_key, end_week, stat_map)
        final_pids = set(final_roster.keys())
    except Exception:
        final_pids = set()

    for week in range(start_week, end_week + 1):
        print(f"  第 {week:>2} 週...", end=" ", flush=True)
        try:
            week_data, week_date, name_to_pkey = get_roster_week_stats(token, team_key, week, stat_map)
            global_name_to_pkey.update(name_to_pkey)
            if week_date:
                week_dates[week] = week_date
            if not week_data:
                print("✗ 無數據"); continue

            week_team_acc = defaultdict(float)
            for pid, info in week_data.items():
                name, pos, stats = info["name"], info["pos"], info["stats"]
                p_week_row = {"week": week, "name": name, "pos": pos}
                for col, val in stats.items():
                    fval = to_float(val)
                    p_week_row[col] = fval
                    if is_pct_col(col): continue
                    player_totals[pid][col] = player_totals[pid].get(col, 0.0) + fval
                    week_team_acc[col] += fval
                player_totals[pid]["name"] = name
                player_totals[pid]["pos"] = pos
                player_totals[pid]["weeks"] += 1
                player_totals[pid]["status"] = "在陣" if pid in final_pids else "已釋出"
                player_weekly_detail.append(p_week_row)

            week_row = {"week": week}
            for col in stat_cols:
                if is_pct_col(col):
                    made_col, att_col = PCT_COLS.get(col, (None, None))
                    if made_col and att_col:
                        att = week_team_acc.get(att_col, 0)
                        week_row[col] = round(week_team_acc.get(made_col, 0) / att, 4) if att > 0 else 0.0
                    else:
                        week_row[col] = 0.0
                else:
                    week_row[col] = week_team_acc.get(col, 0.0)
            team_weekly_summary.append(week_row)
            print(f"✓ {len(week_data)} 人")
        except Exception as e:
            print(f"✗ 失敗（{e}）")

    print(f"\n重新計算整季命中率...")
    for pid, d in player_totals.items():
        for pct_col, (made_col, att_col) in PCT_COLS.items():
            if pct_col not in stat_cols: continue
            att = d.get(att_col, 0.0)
            made = d.get(made_col, 0.0)
            d[pct_col] = round(made / att, 4) if att > 0 else 0.0

    return player_totals, team_weekly_summary, player_weekly_detail, stat_cols, week_dates, global_name_to_pkey


def fetch_matchups(token, league_key, team_key, start_week, end_week, stat_cols, stat_map):
    matchups = []
    cat_record = defaultdict(lambda: {"win": 0, "lose": 0, "tie": 0})

    for week in range(start_week, end_week + 1):
        print(f"  對戰第 {week:>2} 週...", end=" ", flush=True)
        try:
            data = api_get(token, f"/league/{league_key}/scoreboard;week={week}")
            matchups_raw = data["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]

            for i in range(matchups_raw["count"]):
                m = matchups_raw[str(i)]["matchup"]
                teams_in_match = m["0"]["teams"]
                my_team = opp_team = None

                for j in range(teams_in_match["count"]):
                    t_data = teams_in_match[str(j)]["team"]
                    t_key = t_name = None
                    t_stats = {}

                    # 取得基本資訊
                    for attr in t_data[0]:
                        if isinstance(attr, dict):
                            if "team_key" in attr: t_key = attr["team_key"]
                            if "name" in attr: t_name = attr["name"]

                    ts_block = _find_team_stats(t_data)
                    if ts_block and "stats" in ts_block:
                        raw_s = ts_block["stats"]
                        s_list = [v for k, v in raw_s.items() if str(k) != "count" and isinstance(v, dict)] if isinstance(raw_s, dict) else raw_s
                        for stat_obj in s_list:
                            if isinstance(stat_obj, dict) and "stat" in stat_obj:
                                s = stat_obj["stat"]
                                t_stats[str(s.get("stat_id", ""))] = s.get("value", "0")

                    obj = {"key": t_key, "name": t_name, "stats": t_stats}
                    if t_key == team_key: my_team = obj
                    else: opp_team = obj

                if not my_team or not opp_team: continue

                cat_results = {}
                # 1. 嘗試使用官方的 stat_winners (已結算的週次通常有)
                stat_winners_list = m.get("stat_winners", [])
                if isinstance(stat_winners_list, dict):
                    # 處理 Yahoo API 偶爾將空 list 轉為 dict 的怪異結構
                    stat_winners_list = [v for k, v in stat_winners_list.items() if str(k) != "count" and isinstance(v, dict)]

                if stat_winners_list:
                    for sw in stat_winners_list:
                        sw_data = sw.get("stat_winner", {})
                        sid = str(sw_data.get("stat_id", ""))
                        winner_key = sw_data.get("winner_team_key", "")
                        is_tied = str(sw_data.get("is_tied", "0")) == "1"
                        if is_tied: cat_results[sid] = "T"
                        elif winner_key == team_key: cat_results[sid] = "W"
                        else: cat_results[sid] = "L"
                else:
                    # 2. 終極備案：當週正在進行中，Yahoo 尚未產生 stat_winners，我們手動對決！
                    for sid, col_name in stat_map.items():
                        my_v = to_float(my_team["stats"].get(sid, 0))
                        opp_v = to_float(opp_team["stats"].get(sid, 0))

                        if my_v == opp_v:
                            cat_results[sid] = "T"
                        else:
                            is_negative = col_name in NEGATIVE_COLS
                            if my_v > opp_v:
                                cat_results[sid] = "L" if is_negative else "W"
                            else:
                                cat_results[sid] = "W" if is_negative else "L"

                w = sum(1 for v in cat_results.values() if v == "W")
                l = sum(1 for v in cat_results.values() if v == "L")
                t = sum(1 for v in cat_results.values() if v == "T")
                overall = "W" if w > l else ("L" if l > w else "T")

                for sid, result in cat_results.items():
                    if result == "W": cat_record[sid]["win"] += 1
                    elif result == "L": cat_record[sid]["lose"] += 1
                    else: cat_record[sid]["tie"] += 1

                matchups.append({"week": week, "opponent": opp_team["name"],
                                 "result": overall, "cat_win": w, "cat_lose": l, "cat_tie": t})
                label = "勝" if overall == "W" else "敗" if overall == "L" else "平"
                print(f"{label} ({w}-{l}-{t} vs {opp_team['name']})")
                break
        except Exception as e:
            print(f"✗ 失敗（{e}）")

    return matchups, cat_record


def fetch_transactions(token, league_key, team_key):
    print("\n抓取交易紀錄...", end=" ", flush=True)
    try:
        # 【關鍵修正】移除 types=add,drop,trade 的限制，直接抓取該隊「所有」異動紀錄，避免漏掉自由市場 (FA) 的操作
        data = api_get(token, f"/league/{league_key}/transactions;team_key={team_key};count=500")
        trans_raw = data["fantasy_content"]["league"][1]["transactions"]
    except Exception as e:
        print(f"✗ 失敗（{e}）")
        return [], []

    trades = []
    waivers = []
    drops = []
    count = trans_raw.get("count", 0)
    print(f"共 {count} 筆")

    for i in range(count):
        try:
            t = trans_raw[str(i)]["transaction"]
            t_info = t[0]
            t_type = t_info.get("type", "")
            t_status = t_info.get("status", "successful")
            if t_status == "vetoed": continue
            t_ts = int(t_info.get("timestamp", 0))
            ts_str = datetime.fromtimestamp(t_ts, tz=timezone.utc).strftime("%Y-%m-%d") if t_ts else ""

            players_raw = t[1].get("players", {}) if len(t) > 1 else {}
            players_involved = []
            for j in range(players_raw.get("count", 0)):
                p = players_raw[str(j)]["player"]
                p_info = p[0]
                
                # 【防呆解析 transaction_data】Yahoo有時回傳Dict有時回傳List，直接用[0]會觸發KeyError導致被吞掉
                p_tx = {}
                if len(p) > 1 and isinstance(p[1], dict) and "transaction_data" in p[1]:
                    raw_tx = p[1]["transaction_data"]
                    if isinstance(raw_tx, list) and len(raw_tx) > 0:
                        p_tx = raw_tx[0]
                    elif isinstance(raw_tx, dict):
                        p_tx = raw_tx
                        
                    # 避免雙重巢狀
                    if "transaction_data" in p_tx:
                        inner_tx = p_tx["transaction_data"]
                        if isinstance(inner_tx, list) and len(inner_tx) > 0:
                            p_tx = inner_tx[0]
                        elif isinstance(inner_tx, dict):
                            p_tx = inner_tx

                p_name = p_key = None
                for attr in p_info:
                    if isinstance(attr, dict):
                        if "name" in attr: p_name = attr["name"].get("full")
                        if "player_key" in attr: p_key = attr["player_key"]

                players_involved.append({
                    "name": p_name, "player_key": p_key,
                    "dest_type": p_tx.get("destination_type", ""),
                    "dest_team": p_tx.get("destination_team_name", ""),
                    "dest_key": p_tx.get("destination_team_key", ""),
                    "src_type": p_tx.get("source_type", ""),
                    "src_team": p_tx.get("source_team_name", ""),
                    "src_key":  p_tx.get("source_team_key", ""),
                })

            if t_type == "trade":
                received = [{"name": p["name"], "player_key": p["player_key"]}
                            for p in players_involved if p["dest_key"] == team_key]
                sent = [{"name": p["name"], "player_key": p["player_key"], "dest_key": p["dest_key"]}
                        for p in players_involved
                        if p["dest_key"] != team_key and p["dest_type"] == "team"]
                opp_name = next((p["dest_team"] for p in players_involved
                                 if p["dest_key"] != team_key and p["dest_type"] == "team"), "未知")
                if received or sent:
                    trades.append({
                        "date": ts_str, "timestamp": t_ts,
                        "received": received, "sent": sent,
                        "opponent": opp_name,
                    })

            else:
                for p in players_involved:
                    if p["dest_key"] == team_key and p["dest_type"] == "team":
                        waivers.append({
                            "date": ts_str, "timestamp": t_ts,
                            "player": p["name"], "player_key": p["player_key"],
                        })
                    elif p["src_key"] == team_key and p["src_type"] == "team":
                        drops.append({
                            "timestamp": t_ts,
                            "player": p["name"],
                        })
        except Exception:
            continue

    return trades, waivers, drops


def lookup_player_key(token, league_key, player_name):
    try:
        search_name = player_name.replace(" ", "%20")
        data = api_get(token, f"/league/{league_key}/players;search={search_name}")

        players = _find_players_block(data)
        if not players or players.get("count", 0) == 0:
            return None
        p_info = players["0"]["player"][0]
        for attr in p_info:
            if isinstance(attr, dict) and "player_key" in attr:
                return attr["player_key"]
    except Exception:
        pass
    return None


def fetch_player_weekly_stats(token, league_key, player_key, weeks, stat_map):
    """
    修正：把被改爛的 API 端點修回來，移除無效的 /players 端點，改用針對性更強的 /player/ 根節點
    """
    result = {}
    for week in weeks:
        stats = {}
        urls = [
            f"/league/{league_key}/players;player_keys={player_key}/stats;type=week;week={week}",
            f"/player/{player_key}/stats;type=week;week={week}",
        ]
        for url in urls:
            try:
                data = api_get(token, url, retries=1)
                players = _find_players_block(data)
                if players and players.get("count", 0) > 0:
                    player_data = players["0"]["player"]
                    s = parse_stats(player_data, stat_map)
                    # 判斷是否有實際數據：dict 有 key 就算有效，零出賽也保留
                    if s:
                        stats = s
                        break
            except Exception:
                continue
        if stats:
            result[week] = stats
        time.sleep(0.3)
    return result


def date_str_to_timestamp(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


def ts_to_last_week_before(drop_ts, week_ts_list):
    """找出 drop_ts 前最後一個已開始的週次（即球員那段在陣的最後一週）。"""
    last_week = None
    for week, ts in week_ts_list:
        if ts <= drop_ts:
            last_week = week
        else:
            break
    return last_week


def ts_to_first_week_after(trade_ts, week_ts_list):
    """把 Unix timestamp 對應到交易後的第一個週次"""
    for week, ts in week_ts_list:
        if ts >= trade_ts:
            return week
    return None


def sum_player_stats_from_detail(player_name, from_week, player_lookup, compare_cols):
    """
    從 player_weekly_detail 加總球員從 from_week 起的累積數據。
    aux 補充欄位（FGM/FGA 等）只在不屬於 compare_cols 時才額外加，
    避免 3PTM 等欄位同時出現在兩個列表造成重複計算。
    """
    totals = defaultdict(float)
    for week, row in sorted(player_lookup[player_name].items()):
        if week < from_week: continue
        for col in compare_cols:
            if not is_pct_col(col):
                totals[col] += to_float(row.get(col, 0))
        # 命中率計算所需的輔助欄位，只補充不在 compare_cols 裡的
        for aux in ("FGM", "FGA", "3PTM", "3PTA", "FTM", "FTA"):
            if aux not in compare_cols:
                totals[aux] += to_float(row.get(aux, 0))
    return dict(totals)


def recalc_pct(totals, compare_cols):
    for pct_col, (made_col, att_col) in PCT_COLS.items():
        if pct_col in compare_cols:
            att = to_float(totals.get(att_col, 0))
            made = to_float(totals.get(made_col, 0))
            totals[pct_col] = round(made / att, 4) if att > 0 else 0.0
    return totals


def calc_trade_roi(token, league_key, trades, player_weekly_detail, week_dates, stat_cols, stat_map,
                   start_week, end_week, global_name_to_pkey):
    compare_cols = [c for c in stat_cols
                    if c not in ("FGA", "FTA", "3PTA", "GP", "FGM/A")]

    week_ts_list = sorted([(w, date_str_to_timestamp(d)) for w, d in week_dates.items()],
                          key=lambda x: x[1])

    player_lookup = defaultdict(dict)
    for r in player_weekly_detail:
        player_lookup[r["name"]][r["week"]] = r

    # 建立對手名單快取，避免重複對同一個隊伍呼叫 API (強制回歸！)
    global_team_week_cache = {}
    def get_cached_team_week_stats(team_k, w):
        cache_key = f"{team_k}_{w}"
        if cache_key not in global_team_week_cache:
            try:
                wd, _, _ = get_roster_week_stats(token, team_k, w, stat_map)
                global_team_week_cache[cache_key] = wd
            except Exception:
                global_team_week_cache[cache_key] = {}
        return global_team_week_cache[cache_key]

    def sum_from_api_or_cache(player_key, dest_team_key, from_week):
        """
        把被拔掉的雙重保險機制補回來：先用全域查，查不到空殼再從對手名單硬挖。
        """
        weeks_to_fetch = list(range(from_week, end_week + 1))
        totals = defaultdict(float)
        
        player_pid = player_key.split('.')[-1] if player_key else None
        weekly_global = fetch_player_weekly_stats(token, league_key, player_key, weeks_to_fetch, stat_map)
        
        for week in weeks_to_fetch:
            used_global = False
            if week in weekly_global and weekly_global[week]:
                stats = weekly_global[week]
                if any(to_float(v) > 0 for k, v in stats.items() if not is_pct_col(k)):
                    used_global = True
                    for col, val in stats.items():
                        if not is_pct_col(col): totals[col] += to_float(val)
            
            # 第二重（終極殺手鐧）：如果全域查詢失敗，去他被交易到的球隊名單裡面翻
            if not used_global and dest_team_key and player_pid:
                roster_data = get_cached_team_week_stats(dest_team_key, week)
                if player_pid in roster_data:
                    stats = roster_data[player_pid]["stats"]
                    for col, val in stats.items():
                        if not is_pct_col(col): totals[col] += to_float(val)

        return dict(totals)

    roi_results = []

    for idx, trade in enumerate(trades):
        trade_ts = trade["timestamp"]
        first_week = ts_to_first_week_after(trade_ts, week_ts_list)
        if first_week is None:
            continue

        received_list = trade["received"]
        sent_list = trade["sent"]
        print(f"  交易 {idx+1}/{len(trades)}（{trade['date']}）計算中...")

        # 我方收到：從 player_weekly_detail 查（在我陣上才算）
        received_totals = defaultdict(float)
        for p in received_list:
            stats = sum_player_stats_from_detail(p["name"], first_week, player_lookup, compare_cols)
            for col, val in stats.items():
                received_totals[col] += val
        received_totals = recalc_pct(dict(received_totals), compare_cols)

        # 我方送出：從雙保險 API 抓實際表現
        sent_totals = defaultdict(float)
        for p in sent_list:
            pkey = p.get("player_key")
            dest_team_key = p.get("dest_key") # 拿回原本應該要有的目標隊伍金鑰
            
            if not pkey and p["name"]:
                pkey = global_name_to_pkey.get(p["name"])
            if not pkey and p["name"]:
                print(f"    搜尋 {p['name']} 的 player_key...")
                pkey = lookup_player_key(token, league_key, p["name"])
                if pkey:
                    global_name_to_pkey[p["name"]] = pkey
                else:
                    print(f"    ⚠️ 找不到 {p['name']} 的 player_key，跳過")
                    continue
            if not pkey: continue

            print(f"    抓取 {p['name']} 第 {first_week}～{end_week} 週數據...", flush=True)
            stats = sum_from_api_or_cache(pkey, dest_team_key, first_week)
            for col, val in stats.items():
                sent_totals[col] += val
        sent_totals = recalc_pct(dict(sent_totals), compare_cols)

        cat_compare = {}
        my_win = my_lose = tie = 0
        for col in compare_cols:
            if col in ("FGA", "FTA", "3PTA"): continue
            r_val = to_float(received_totals.get(col, 0))
            s_val = to_float(sent_totals.get(col, 0))
            if col in NEGATIVE_COLS:
                result = "我方優" if r_val < s_val else ("對方優" if r_val > s_val else "平")
                if r_val < s_val: my_win += 1
                elif r_val > s_val: my_lose += 1
                else: tie += 1
            else:
                result = "我方優" if r_val > s_val else ("對方優" if r_val < s_val else "平")
                if r_val > s_val: my_win += 1
                elif r_val < s_val: my_lose += 1
                else: tie += 1
            cat_compare[col] = {"received": r_val, "sent": s_val, "result": result}

        overall = "我方優" if my_win > my_lose else ("對方優" if my_lose > my_win else "平")
        roi_results.append({
            "date": trade["date"], "opponent": trade["opponent"],
            "received": ", ".join(p["name"] for p in received_list if p["name"]),
            "sent": ", ".join(p["name"] for p in sent_list if p["name"]),
            "from_week": first_week,
            "my_win": my_win, "my_lose": my_lose, "tie": tie,
            "overall": overall, "cat_compare": cat_compare,
        })

    return roi_results, compare_cols


# ==================== Waiver 撿人評估 ====================

def calc_waiver_roi(waivers, drops, player_weekly_detail, week_dates, stat_cols):
    """
    對每筆撿人計算：從撿入那週起，該球員在我陣上的累積數據與週均。
    同一個球員可能被撿入多次，各自計算。
    """
    compare_cols = [c for c in stat_cols
                    if c not in ("FGA", "FTA", "3PTA", "GP", "FGM/A")
                    and not is_pct_col(c)]

    week_ts_list = sorted([(w, date_str_to_timestamp(d)) for w, d in week_dates.items()],
                          key=lambda x: x[1])

    player_lookup = defaultdict(dict)
    for r in player_weekly_detail:
        player_lookup[r["name"]][r["week"]] = r

    # 建立放棄紀錄查詢表：player_name -> 放棄 timestamp 清單（排序）
    drop_lookup = defaultdict(list)
    for d in drops:
        if d.get("player"):
            drop_lookup[d["player"]].append(d["timestamp"])
    for name in drop_lookup:
        drop_lookup[name].sort()

    waiver_results = []

    for waiver in waivers:
        name = waiver.get("player")
        if not name: continue

        pickup_ts = waiver["timestamp"]
        first_week = ts_to_first_week_after(pickup_ts, week_ts_list)
        if first_week is None: continue

        # 找這次撿入後的第一次放棄，確定本段在陣的結束週次
        next_drops = [t for t in drop_lookup.get(name, []) if t > pickup_ts]
        if next_drops:
            stint_last_week = ts_to_last_week_before(min(next_drops), week_ts_list)
        else:
            stint_last_week = None  # 至今仍在陣，不設上限

        totals = defaultdict(float)
        weeks_on_roster = 0

        for week, row in sorted(player_lookup[name].items()):
            if week < first_week: continue
            if stint_last_week is not None and week > stint_last_week: continue
            weeks_on_roster += 1
            for col in compare_cols:
                totals[col] += to_float(row.get(col, 0))
            for aux, (made_col, att_col) in [
                ("FGM", ("FGM", "FGA")), ("FGA", ("FGM", "FGA")),
                ("3PTM", ("3PTM", "3PTA")), ("3PTA", ("3PTM", "3PTA")),
                ("FTM", ("FTM", "FTA")), ("FTA", ("FTM", "FTA")),
            ]:
                totals[aux] += to_float(row.get(aux, 0))

        # 重算命中率
        for pct_col, (made_col, att_col) in PCT_COLS.items():
            if pct_col in stat_cols:
                att = totals.get(att_col, 0)
                made = totals.get(made_col, 0)
                totals[pct_col] = round(made / att, 4) if att > 0 else 0.0

        if weeks_on_roster == 0:
            continue

        # 計算週均（命中率不做週均，保持整段期間的真實命中率）
        weekly_avg = {}
        for col in compare_cols:
            weekly_avg[col] = round(totals[col] / weeks_on_roster, 2)
        for pct_col in PCT_COLS:
            if pct_col in stat_cols:
                weekly_avg[pct_col] = totals.get(pct_col, 0.0)

        waiver_results.append({
            "date": waiver["date"],
            "player": name,
            "from_week": first_week,
            "weeks_on_roster": weeks_on_roster,
            "totals": dict(totals),
            "weekly_avg": weekly_avg,
            "pts_total": totals.get("PTS", 0),
            "pts_avg": weekly_avg.get("PTS", 0),
        })

    # 依週均得分排序（代理整體貢獻值）
    waiver_results.sort(key=lambda x: x["pts_avg"], reverse=True)
    return waiver_results, compare_cols


def export_csvs(team_name, player_totals, team_weekly_summary,
                player_weekly_detail, stat_cols,
                matchups, cat_record, trades, waivers, stat_map,
                roi_results, compare_cols,
                waiver_results, waiver_compare_cols):

    sort_col = "PTS" if "PTS" in stat_cols else (stat_cols[0] if stat_cols else None)
    ranked = sorted(player_totals.items(),
                    key=lambda x: x[1].get(sort_col, 0) if sort_col else 0, reverse=True)

    def fmt(col, val):
        if is_pct_col(col) and isinstance(val, float): return f"{val:.1%}"
        if isinstance(val, float): return round(val, 1)
        return val

    with open("season_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["排名", "球員", "位置", "狀態", "在陣週數"] + stat_cols)
        for rank, (pid, d) in enumerate(ranked, 1):
            row = [rank, d["name"], d["pos"], d.get("status", "未知"), d["weeks"]]
            row += [fmt(c, d.get(c, 0)) for c in stat_cols]
            w.writerow(row)

    with open("season_weekly_average.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["排名", "球員", "位置", "狀態", "在陣週數"] + [f"週均 {c}" for c in stat_cols])
        for rank, (pid, d) in enumerate(ranked, 1):
            wks = float(d.get("weeks", 1)) or 1
            row = [rank, d["name"], d["pos"], d.get("status", "未知"), int(wks)]
            for c in stat_cols:
                val = d.get(c, 0)
                row.append(fmt(c, val) if is_pct_col(c) else round(to_float(val) / wks, 2))
            w.writerow(row)

    with open("team_weekly_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["週次"] + stat_cols)
        for r in sorted(team_weekly_summary, key=lambda x: x["week"]):
            w.writerow([r["week"]] + [fmt(c, r.get(c, 0)) for c in stat_cols])

    with open("player_weekly_detail.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["週次", "球員", "位置"] + stat_cols)
        for r in sorted(player_weekly_detail, key=lambda x: x["week"]):
            row = [r["week"], r["name"], r["pos"]]
            row += [fmt(c, r.get(c, 0)) for c in stat_cols]
            w.writerow(row)

    with open("matchup_results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["週次", "對手", "結果", "類別勝", "類別敗", "類別平"])
        for m in matchups:
            w.writerow([m["week"], m["opponent"], m["result"],
                        m["cat_win"], m["cat_lose"], m["cat_tie"]])

    with open("category_record.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["類別", "勝", "敗", "平", "勝率"])
        for sid, rec in cat_record.items():
            name = stat_map.get(sid, sid)
            total = rec["win"] + rec["lose"] + rec["tie"]
            rate = f"{rec['win']/total:.1%}" if total > 0 else "-"
            w.writerow([name, rec["win"], rec["lose"], rec["tie"], rate])

    with open("trade_history.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["時間", "交易對手", "我方收到", "我方送出"])
        for t in trades:
            w.writerow([t["date"], t["opponent"],
                        ", ".join(p["name"] for p in t["received"] if p["name"]),
                        ", ".join(p["name"] for p in t["sent"] if p["name"])])

    with open("waiver_history.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["時間", "球員"])
        for t in waivers:
            w.writerow([t["date"], t["player"]])

    display_cols = [c for c in compare_cols if c not in ("FGA", "FTA", "3PTA")]
    with open("trade_roi.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        header = ["日期", "交易對手", "我方收到", "我方送出", "從第N週起算",
                  "總評", "我方優勢類別", "對方優勢類別"]
        for col in display_cols:
            header += [f"我方_{col}", f"對方_{col}", f"{col}勝負"]
        w.writerow(header)
        for r in roi_results:
            row = [r["date"], r["opponent"], r["received"], r["sent"],
                   r["from_week"], r["overall"], r["my_win"], r["my_lose"]]
            for col in display_cols:
                cc = r["cat_compare"].get(col, {})
                rv = cc.get("received", 0)
                sv = cc.get("sent", 0)
                res = cc.get("result", "-")
                if is_pct_col(col):
                    row += [f"{rv:.1%}", f"{sv:.1%}", res]
                else:
                    row += [round(rv, 1), round(sv, 1), res]
            w.writerow(row)

    # Waiver ROI CSV
    w_display_cols = [c for c in waiver_compare_cols if c not in ("FGA", "FTA", "3PTA")]
    pct_display = [c for c in stat_cols if is_pct_col(c)]
    with open("waiver_roi.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        total_header = ["排名", "撿入日期", "球員", "從第N週", "在陣週數"]
        for col in w_display_cols + pct_display:
            total_header += [f"累積_{col}"]
        for col in w_display_cols:
            total_header += [f"週均_{col}"]
        for col in pct_display:
            total_header += [f"整段_{col}"]
        w.writerow(total_header)

        for rank, r in enumerate(waiver_results, 1):
            row = [rank, r["date"], r["player"], r["from_week"], r["weeks_on_roster"]]
            for col in w_display_cols + pct_display:
                val = r["totals"].get(col, 0)
                row.append(f"{val:.1%}" if is_pct_col(col) else round(val, 1))
            for col in w_display_cols:
                row.append(round(r["weekly_avg"].get(col, 0), 2))
            for col in pct_display:
                val = r["weekly_avg"].get(col, 0)
                row.append(f"{val:.1%}" if isinstance(val, float) else val)
            w.writerow(row)

    print(f"\n✅ 匯出完畢：")
    for fn in ["season_summary.csv", "season_weekly_average.csv",
               "team_weekly_summary.csv", "player_weekly_detail.csv",
               "matchup_results.csv", "category_record.csv",
               "trade_history.csv", "waiver_history.csv",
               "trade_roi.csv", "waiver_roi.csv"]:
        print(f"   {fn}")


def generate_html_dashboard(team_name, team_weekly_summary, player_weekly_detail,
                             stat_cols, matchups, cat_record, trades,
                             roi_results, compare_cols, stat_map,
                             waiver_results, waiver_compare_cols, player_totals):
    weeks = sorted(set(r["week"] for r in team_weekly_summary))
    num_cols = [c for c in stat_cols if c not in ("FGA", "FTA", "3PTA", "GP", "FGM/A")]

    team_data = {}
    for c in num_cols:
        team_data[c] = [next((r.get(c, 0) for r in team_weekly_summary if r["week"] == w), 0)
                        for w in weeks]

    players_data = {}
    for r in player_weekly_detail:
        name = r["name"]
        if name not in players_data:
            players_data[name] = {c: [None] * len(weeks) for c in num_cols}
        if r["week"] in weeks:
            idx = weeks.index(r["week"])
            for c in num_cols:
                players_data[name][c][idx] = to_float(r.get(c, 0))

    matchup_data = [{"week": m["week"], "opp": m["opponent"], "result": m["result"],
                     "score": f"{m['cat_win']}-{m['cat_lose']}-{m['cat_tie']}"}
                    for m in matchups]

    cat_data = []
    for sid, rec in cat_record.items():
        name = stat_map.get(sid, sid)
        total = rec["win"] + rec["lose"] + rec["tie"]
        rate = round(rec["win"] / total, 3) if total > 0 else 0
        cat_data.append({"name": name, "win": rec["win"], "lose": rec["lose"],
                          "tie": rec["tie"], "rate": rate})
    cat_data.sort(key=lambda x: x["rate"], reverse=True)

    display_cols = [c for c in compare_cols if c not in ("FGA", "FTA", "3PTA")]
    roi_data = []
    for r in roi_results:
        entry = {
            "date": r["date"], "opponent": r["opponent"],
            "received": r["received"], "sent": r["sent"],
            "from_week": r["from_week"], "overall": r["overall"],
            "my_win": r["my_win"], "my_lose": r["my_lose"], "tie": r["tie"],
            "cats": []
        }
        for col in display_cols:
            cc = r["cat_compare"].get(col, {})
            rv = cc.get("received", 0)
            sv = cc.get("sent", 0)
            entry["cats"].append({
                "col": col,
                "received": f"{rv:.1%}" if is_pct_col(col) else round(rv, 1),
                "sent": f"{sv:.1%}" if is_pct_col(col) else round(sv, 1),
                "result": cc.get("result", "-")
            })
        roi_data.append(entry)

    # Waiver 數據準備
    key_cols = ["PTS", "REB", "AST", "ST", "BLK", "3PTM", "TO"]
    waiver_display = [c for c in key_cols if c in waiver_compare_cols]
    waiver_data = []
    for r in waiver_results:
        entry = {
            "date": r["date"], "player": r["player"],
            "from_week": r["from_week"], "weeks": r["weeks_on_roster"],
            "pts_avg": round(r["pts_avg"], 1),
            "stats": []
        }
        for col in waiver_display:
            entry["stats"].append({
                "col": col,
                "total": round(r["totals"].get(col, 0), 1),
                "avg": round(r["weekly_avg"].get(col, 0), 1)
            })
        waiver_data.append(entry)

    # ── 數據王 ──────────────────────────────────────────────────
    display_cols = [c for c in stat_cols if c not in ("FGA", "FTA", "3PTA", "GP", "FGM/A")]
    active_players = [(pid, d) for pid, d in player_totals.items() if d.get("weeks", 0) > 0]

    cat_leaders = []
    for col in display_cols:
        is_neg = col in NEGATIVE_COLS
        is_pct = is_pct_col(col)
        entry = {"col": col, "is_neg": is_neg, "total_name": None, "total_val": None,
                 "avg_name": "-", "avg_val": "-"}
        if not active_players:
            cat_leaders.append(entry)
            continue
        if not is_pct:
            # 負面類別取最高值（黑榜）；正面類別取最高值（榮譽榜），兩者都是 reverse=True
            ranked_t = sorted(active_players,
                              key=lambda x: to_float(x[1].get(col, 0)),
                              reverse=True)
            best = ranked_t[0][1]
            entry["total_name"] = best["name"]
            entry["total_val"] = round(to_float(best.get(col, 0)), 1)
        if is_pct:
            ranked_a = sorted(active_players,
                              key=lambda x: to_float(x[1].get(col, 0)),
                              reverse=not is_neg)
        else:
            # 週均：負面類別同樣取最高（最多），正面取最高（最多）
            ranked_a = sorted(active_players,
                              key=lambda x: to_float(x[1].get(col, 0)) / max(x[1].get("weeks", 1), 1),
                              reverse=True)
        best_a = ranked_a[0][1]
        entry["avg_name"] = best_a["name"]
        val_a = to_float(best_a.get(col, 0))
        if is_pct:
            entry["avg_val"] = f"{val_a:.1%}"
        else:
            entry["avg_val"] = round(val_a / max(best_a.get("weeks", 1), 1), 2)
        cat_leaders.append(entry)

    # ── MVP Z-score ──────────────────────────────────────────────
    mvp_cols = [c for c in stat_cols
                if c not in ("FGA", "FTA", "3PTA", "GP", "FGM/A") and not is_pct_col(c)]

    col_mean_std = {}
    for col in mvp_cols:
        vals = [to_float(d.get(col, 0)) for _, d in active_players]
        mean = sum(vals) / len(vals) if vals else 0
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5 if vals else 1
        col_mean_std[col] = (mean, std if std > 0 else 1)

    mvp_data = []
    for pid, d in active_players:
        z_total = 0.0
        z_breakdown = {}
        for col in mvp_cols:
            mean, std = col_mean_std[col]
            z = (to_float(d.get(col, 0)) - mean) / std
            if col in NEGATIVE_COLS:
                z = -z
            z_breakdown[col] = round(z, 3)
            z_total += z
        mvp_data.append({
            "name": d["name"], "pos": d["pos"],
            "weeks": d.get("weeks", 0), "status": d.get("status", ""),
            "z_total": round(z_total, 3), "z_breakdown": z_breakdown,
        })
    mvp_data.sort(key=lambda x: x["z_total"], reverse=True)

    dashboard = {
        "teamName": team_name,
        "weeks": [f"W{w}" for w in weeks],
        "categories": num_cols,
        "team": team_data,
        "players": players_data,
        "matchups": matchup_data,
        "catRecord": cat_data,
        "trades": [{"date": t["date"], "opponent": t["opponent"],
                    "received": ", ".join(p["name"] for p in t["received"] if p["name"]),
                    "sent": ", ".join(p["name"] for p in t["sent"] if p["name"])}
                   for t in trades],
        "roiData": roi_data,
        "waiverData": waiver_data,
        "waiverCols": waiver_display,
        "catLeaders": cat_leaders,
        "mvpData": mvp_data,
        "mvpCols": mvp_cols,
    }

    safe_name = html_lib.escape(team_name)
    dashboard_json = json.dumps(dashboard, ensure_ascii=False).replace("</script>", r"<\/script>")

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>{safe_name} - Fantasy 戰情室</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
h1 {{ text-align: center; color: #38bdf8; font-size: 1.8rem; margin-bottom: 8px; }}
.subtitle {{ text-align: center; color: #64748b; margin-bottom: 32px; font-size: 0.9rem; }}
.tabs {{ display: flex; gap: 8px; max-width: 1400px; margin: 0 auto 24px; flex-wrap: wrap; }}
.tab {{ padding: 8px 18px; border-radius: 8px; cursor: pointer; background: #1e293b;
        color: #94a3b8; border: 1px solid #334155; font-size: 0.9rem; transition: all 0.2s; }}
.tab.active {{ background: #38bdf8; color: #0f172a; border-color: #38bdf8; font-weight: 600; }}
.panel {{ display: none; max-width: 1400px; margin: 0 auto; }}
.panel.active {{ display: block; }}
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
@media(max-width: 900px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
.card {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
.card-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }}
h2 {{ font-size: 1rem; color: #f1f5f9; font-weight: 600; }}
select {{ background: #334155; color: #f1f5f9; border: 1px solid #475569;
          border-radius: 6px; padding: 6px 10px; font-size: 0.85rem; outline: none; }}
.chart-wrap {{ position: relative; height: 300px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
th {{ text-align: left; padding: 8px 10px; color: #94a3b8; border-bottom: 1px solid #334155; font-weight: 500; white-space: nowrap; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #1e293b; }}
tr:hover td {{ background: #263548; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
.badge-W, .badge-我方優 {{ background: #064e3b; color: #34d399; }}
.badge-L, .badge-對方優 {{ background: #450a0a; color: #f87171; }}
.badge-T, .badge-平 {{ background: #1e3a5f; color: #60a5fa; }}
.bar-bg {{ background: #334155; border-radius: 4px; height: 8px; }}
.bar-fill {{ background: #38bdf8; border-radius: 4px; height: 8px; }}
.cat-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin-top: 12px; }}
.cat-item {{ background: #0f172a; border-radius: 8px; padding: 10px; font-size: 0.8rem; }}
.cat-item .col-name {{ color: #94a3b8; margin-bottom: 4px; }}
.roi-card {{ background: #0f172a; border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
.roi-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }}
.rank-badge {{ display: inline-flex; align-items: center; justify-content: center;
               width: 28px; height: 28px; border-radius: 50%; font-weight: 700;
               font-size: 0.8rem; background: #334155; color: #94a3b8; flex-shrink: 0; }}
.rank-badge.top1 {{ background: #78350f; color: #fbbf24; }}
.rank-badge.top2 {{ background: #374151; color: #d1d5db; }}
.rank-badge.top3 {{ background: #431407; color: #fb923c; }}
.waiver-card {{ background: #0f172a; border-radius: 10px; padding: 14px; margin-bottom: 12px;
                display: flex; gap: 16px; align-items: flex-start; }}
.waiver-stat-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }}
.waiver-stat {{ background: #1e293b; border-radius: 6px; padding: 6px 10px; font-size: 0.78rem; text-align: center; }}
.waiver-stat .label {{ color: #64748b; font-size: 0.7rem; }}
.waiver-stat .val {{ color: #f1f5f9; font-weight: 600; }}
.waiver-stat .sub {{ color: #94a3b8; font-size: 0.68rem; }}
</style>
</head>
<body>
<h1>🏀 {safe_name}</h1>
<p class="subtitle">賽季完整戰情分析儀表板</p>
<div class="tabs">
  <div class="tab active" onclick="showPanel('stats',this)">📊 球員統計</div>
  <div class="tab" onclick="showPanel('matchups',this)">⚔️ 對戰記錄</div>
  <div class="tab" onclick="showPanel('categories',this)">🏆 類別勝率</div>
  <div class="tab" onclick="showPanel('trades',this)">🔄 交易紀錄</div>
  <div class="tab" onclick="showPanel('roi',this)">📈 交易 ROI</div>
  <div class="tab" onclick="showPanel('waiver',this)">🎯 FA/Waiver 評估</div>
  <div class="tab" onclick="showPanel('leaders',this)">🏅 數據王</div>
  <div class="tab" onclick="showPanel('mvp',this)">🧮 MVP</div>
</div>

<div id="panel-stats" class="panel active">
  <div class="grid2">
    <div class="card">
      <div class="card-head"><h2>球隊週走勢</h2><select id="teamCat"></select></div>
      <div class="chart-wrap"><canvas id="teamChart"></canvas></div>
    </div>
    <div class="card">
      <div class="card-head">
        <h2>球員逐週表現</h2>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <select id="playerSel"></select><select id="playerCat"></select>
        </div>
      </div>
      <div class="chart-wrap"><canvas id="playerChart"></canvas></div>
    </div>
  </div>
</div>

<div id="panel-matchups" class="panel">
  <div class="card">
    <div class="card-head"><h2>⚔️ 每週對戰結果</h2></div>
    <table><thead><tr><th>週次</th><th>對手</th><th>結果</th><th>類別比分</th></tr></thead>
    <tbody id="matchupBody"></tbody></table>
  </div>
</div>

<div id="panel-categories" class="panel">
  <div class="card">
    <div class="card-head"><h2>🏆 整季類別勝率排行</h2></div>
    <div id="catList"></div>
  </div>
</div>

<div id="panel-trades" class="panel">
  <div class="card">
    <div class="card-head"><h2>🔄 交易紀錄</h2></div>
    <table><thead><tr><th>時間</th><th>交易對手</th><th>我方收到</th><th>我方送出</th></tr></thead>
    <tbody id="tradeBody"></tbody></table>
  </div>
</div>

<div id="panel-roi" class="panel">
  <div class="card">
    <div class="card-head"><h2>📈 交易 ROI — 逐類別比較</h2></div>
    <p style="color:#64748b;font-size:0.82rem;margin-bottom:16px">
      我方收到：交易後在我陣上的累積數據｜我方送出：交易後的實際表現（不限隊伍）
    </p>
    <div id="roiList"></div>
  </div>
</div>

<div id="panel-waiver" class="panel">
  <div class="card">
    <div class="card-head"><h2>🎯 FA/Waiver 撿人評估（依撿入後週均 PTS 排序）</h2></div>
    <p style="color:#64748b;font-size:0.82rem;margin-bottom:16px">
      數據為撿入後該球員在我陣上期間的累積與週均，同一球員若多次撿入則分開計算。
    </p>
    <div id="waiverList"></div>
  </div>
</div>

<div id="panel-leaders" class="panel">
  <div class="card">
    <div class="card-head"><h2>🏅 各項目數據王</h2></div>
    <p style="color:#64748b;font-size:0.82rem;margin-bottom:16px">
      <b style="color:#fbbf24">🥇 正向類別</b>：整季累積／週均最高者奪王。
      <b style="color:#f87171">💀 負向類別（TO、PF 等）</b>：累積／週均最高者上黑榜，失誤最多、犯規最勤，別有滋味。
      百分比類別（FG%、FT%）不列累積王，僅比較整段命中率。
    </p>
    <table><thead><tr>
      <th>項目</th>
      <th>🥇💀 累積之王</th><th>累積值</th>
      <th>🥇💀 週均之王</th><th>週均值</th>
    </tr></thead>
    <tbody id="leadersBody"></tbody></table>
  </div>
</div>

<div id="panel-mvp" class="panel">
  <div class="card">
    <div class="card-head"><h2>🧮 MVP 排行（Z-score 綜合分）</h2></div>
    <p style="color:#64748b;font-size:0.82rem;margin-bottom:16px">
      <b style="color:#f1f5f9">Z-score 是什麼？</b>
      把每個球員的各項數據，跟所有隊友的平均值比較，計算「超越平均幾個標準差」。
      例如全隊平均 PTS 是 300，標準差 50，你的球員拿了 400，Z-score 就是 +2.0。
      各類別的 Z-score 加總後，數字越高代表在越多項目上超越隊友，整體貢獻越突出。
      負向類別（TO、PF 等）已自動反轉——失誤越少拿越高分。
      <b style="color:#94a3b8">百分比欄位（FG%/FT%）暫不納入，避免出賽少的球員命中率失真。</b>
    </p>
    <div id="mvpList"></div>
  </div>
</div>

<script>
const D = {dashboard_json};

function showPanel(id, el) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  el.classList.add('active');
}}

Chart.defaults.color = '#94a3b8';
const baseOpts = {{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ display: false }} }},
  scales: {{ y: {{ beginAtZero: true, grid: {{ color: '#334155' }} }}, x: {{ grid: {{ display: false }} }} }}
}};

function populate(sel, items) {{ items.forEach(v => sel.add(new Option(v, v))); }}
populate(document.getElementById('teamCat'), D.categories);
populate(document.getElementById('playerCat'), D.categories);
populate(document.getElementById('playerSel'), Object.keys(D.players).sort());

const teamChart = new Chart(document.getElementById('teamChart'), {{
  type: 'line',
  data: {{ labels: D.weeks, datasets: [{{ data: D.team[D.categories[0]],
    borderColor: '#38bdf8', backgroundColor: 'rgba(56,189,248,0.1)',
    borderWidth: 2, tension: 0.3, fill: true, pointRadius: 4 }}] }},
  options: baseOpts
}});
const playerChart = new Chart(document.getElementById('playerChart'), {{
  type: 'bar',
  data: {{ labels: D.weeks, datasets: [{{ data: [], backgroundColor: '#10b981', borderRadius: 4 }}] }},
  options: baseOpts
}});
function refreshPlayerChart() {{
  const p = document.getElementById('playerSel').value;
  const c = document.getElementById('playerCat').value;
  playerChart.data.datasets[0].data = (D.players[p] || {{}})[c] || [];
  playerChart.update();
}}
document.getElementById('teamCat').addEventListener('change', e => {{
  teamChart.data.datasets[0].data = D.team[e.target.value]; teamChart.update();
}});
document.getElementById('playerSel').addEventListener('change', refreshPlayerChart);
document.getElementById('playerCat').addEventListener('change', refreshPlayerChart);
refreshPlayerChart();

const mtBody = document.getElementById('matchupBody');
D.matchups.forEach(m => {{
  const cls = m.result==='W'?'badge-W':m.result==='L'?'badge-L':'badge-T';
  const label = m.result==='W'?'勝':m.result==='L'?'敗':'平';
  mtBody.innerHTML += `<tr><td>第${{m.week}}週</td><td>${{m.opp}}</td>
    <td><span class="badge ${{cls}}">${{label}}</span></td><td>${{m.score}}</td></tr>`;
}});

const catList = document.getElementById('catList');
D.catRecord.forEach(c => {{
  const pct = Math.round(c.rate * 100);
  catList.innerHTML += `<div style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;margin-bottom:4px">
      <span>${{c.name}}</span>
      <span style="color:#94a3b8">${{c.win}}勝 ${{c.lose}}敗 ${{c.tie}}平 — ${{pct}}%</span>
    </div>
    <div class="bar-bg"><div class="bar-fill" style="width:${{pct}}%"></div></div></div>`;
}});

const trBody = document.getElementById('tradeBody');
D.trades.forEach(t => {{
  trBody.innerHTML += `<tr>
    <td style="color:#64748b;font-size:0.8rem">${{t.date}}</td>
    <td>${{t.opponent}}</td>
    <td style="color:#34d399">${{t.received}}</td>
    <td style="color:#f87171">${{t.sent}}</td></tr>`;
}});

const roiList = document.getElementById('roiList');
D.roiData.forEach(r => {{
  const cls = r.overall==='我方優'?'badge-我方優':r.overall==='對方優'?'badge-對方優':'badge-平';
  let catsHtml = '<div class="cat-grid">';
  r.cats.forEach(c => {{
    const color = c.result==='我方優'?'#34d399':c.result==='對方優'?'#f87171':'#60a5fa';
    catsHtml += `<div class="cat-item">
      <div class="col-name">${{c.col}}</div>
      <div style="display:flex;justify-content:space-between">
        <span style="color:#34d399">${{c.received}}</span>
        <span style="color:#475569">vs</span>
        <span style="color:#f87171">${{c.sent}}</span>
      </div>
      <div style="color:${{color}};font-size:0.75rem;margin-top:2px">${{c.result}}</div>
    </div>`;
  }});
  catsHtml += '</div>';
  roiList.innerHTML += `<div class="roi-card">
    <div class="roi-header">
      <div>
        <div style="font-size:0.75rem;color:#64748b;margin-bottom:4px">
          ${{r.date}} · 第${{r.from_week}}週後起算 · 對手：${{r.opponent}}
        </div>
        <div style="font-size:0.85rem">
          <span style="color:#34d399">收：${{r.received}}</span>
          <span style="color:#475569;margin:0 8px">⟺</span>
          <span style="color:#f87171">送：${{r.sent}}</span>
        </div>
      </div>
      <div style="text-align:right">
        <span class="badge ${{cls}}">${{r.overall}}</span>
        <div style="font-size:0.75rem;color:#64748b;margin-top:4px">
          ${{r.my_win}}項優 / ${{r.my_lose}}項劣 / ${{r.tie}}項平
        </div>
      </div>
    </div>
    ${{catsHtml}}
  </div>`;
}});

// 數據王
const leadersBody = document.getElementById('leadersBody');
D.catLeaders.forEach(c => {{
  const negTag = c.is_neg ? '<span style="font-size:0.7rem;color:#f87171;margin-left:4px">💀黑榜</span>' : '';
  const totalColor = c.is_neg ? '#f87171' : '#fbbf24';
  const totalIcon  = c.is_neg ? '💀' : '🥇';
  const avgIcon    = c.is_neg ? '💀' : '📊';
  const avgColor   = c.is_neg ? '#f87171' : '#34d399';
  const totalCells = c.total_name !== null
    ? `<td style="color:${{totalColor}};font-weight:600">${{totalIcon}} ${{c.total_name}}</td><td>${{c.total_val}}</td>`
    : `<td style="color:#475569" colspan="2">—</td>`;
  leadersBody.innerHTML += `<tr>
    <td>${{c.col}}${{negTag}}</td>
    ${{totalCells}}
    <td style="color:${{avgColor}};font-weight:600">${{avgIcon}} ${{c.avg_name}}</td>
    <td>${{c.avg_val}}</td>
  </tr>`;
}});

// MVP
const mvpList = document.getElementById('mvpList');
D.mvpData.forEach((p, idx) => {{
  const rankClass = idx === 0 ? 'top1' : idx === 1 ? 'top2' : idx === 2 ? 'top3' : '';
  const zColor = p.z_total >= 0 ? '#34d399' : '#f87171';
  const zSign = p.z_total >= 0 ? '+' : '';
  let barsHtml = '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">';
  D.mvpCols.forEach(col => {{
    const z = p.z_breakdown[col] || 0;
    const barColor = z >= 0 ? '#34d399' : '#f87171';
    const sign = z >= 0 ? '+' : '';
    barsHtml += `<div style="background:#0f172a;border-radius:6px;padding:6px 10px;font-size:0.75rem;min-width:72px;text-align:center">
      <div style="color:#64748b">${{col}}</div>
      <div style="color:${{barColor}};font-weight:600">${{sign}}${{z.toFixed(2)}}</div>
    </div>`;
  }});
  barsHtml += '</div>';
  const releasedTag = p.status === '已釋出' ? '<span style="color:#f87171;font-size:0.73rem;margin-left:6px">已釋出</span>' : '';
  mvpList.innerHTML += `<div class="roi-card" style="margin-bottom:16px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
      <div class="rank-badge ${{rankClass}}">${{idx+1}}</div>
      <div style="flex:1">
        <span style="font-weight:700;font-size:1rem">${{p.name}}</span>
        <span style="color:#64748b;font-size:0.8rem;margin-left:8px">${{p.pos}} · ${{p.weeks}} 週</span>
        ${{releasedTag}}
      </div>
      <div style="text-align:right">
        <div style="font-size:1.1rem;font-weight:700;color:${{zColor}}">${{zSign}}${{p.z_total.toFixed(2)}}</div>
        <div style="font-size:0.72rem;color:#64748b">Z-score 綜合分</div>
      </div>
    </div>
    ${{barsHtml}}
  </div>`;
}});

// Waiver 評估
const waiverList = document.getElementById('waiverList');
D.waiverData.forEach((r, idx) => {{
  const rankClass = idx === 0 ? 'top1' : idx === 1 ? 'top2' : idx === 2 ? 'top3' : '';
  let statsHtml = '<div class="waiver-stat-row">';
  r.stats.forEach(s => {{
    statsHtml += `<div class="waiver-stat">
      <div class="label">${{s.col}}</div>
      <div class="val">${{s.avg}}</div>
      <div class="sub">週均｜共${{s.total}}</div>
    </div>`;
  }});
  statsHtml += `<div class="waiver-stat">
    <div class="label">PTS週均</div>
    <div class="val" style="color:#38bdf8">${{r.pts_avg}}</div>
    <div class="sub">在陣${{r.weeks}}週</div>
  </div>`;
  statsHtml += '</div>';
  waiverList.innerHTML += `<div class="waiver-card">
    <div class="rank-badge ${{rankClass}}">${{idx+1}}</div>
    <div style="flex:1">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
        <span style="font-weight:600;font-size:0.95rem">${{r.player}}</span>
        <span style="font-size:0.75rem;color:#64748b">${{r.date}} 撿入 · 第${{r.from_week}}週起</span>
      </div>
      ${{statsHtml}}
    </div>
  </div>`;
}});
</script>
</body>
</html>"""

    path = os.path.join(os.getcwd(), "fantasy_dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    print("Yahoo Fantasy Basketball 整季完整分析")
    print("=" * 55)
    if CLIENT_ID == "填入你的_Client_ID":
        print("⚠️  請先填入 CLIENT_ID 和 CLIENT_SECRET")
        return

    token = authorize()

    print("正在取得聯盟列表...")
    leagues = get_leagues(token)
    if not leagues:
        print("找不到 NBA Fantasy 聯盟"); return

    print("\n你的聯盟：")
    for i, lg in enumerate(leagues):
        print(f"  {i+1}. {lg['name']}（{lg['season']} 賽季，{lg['num_teams']} 隊）")

    chosen = leagues[0] if len(leagues) == 1 else leagues[int(input("\n選擇聯盟編號：")) - 1]
    print(f"\n選擇：{chosen['name']}")
    league_key = chosen["key"]

    print("\n正在確認你的隊伍...")
    team_key, team_name = get_my_team_key(token, league_key)

    start_week, end_week = get_week_range(token, league_key)
    if end_week < start_week:
        print("目前沒有已結束的週次"); return

    print("\n正在取得統計類別...")
    stat_map = get_stat_categories(token, league_key)

    print(f"\n【1/5】球員統計（第 {start_week}～{end_week} 週）...")
    player_totals, team_weekly_summary, player_weekly_detail, stat_cols, week_dates, global_name_to_pkey = \
        fetch_all_weeks(token, team_key, start_week, end_week, stat_map)

    print(f"\n【2/5】對戰結果...")
    # 將 stat_map 傳進去給手動對決功能使用
    matchups, cat_record = fetch_matchups(
        token, league_key, team_key, start_week, end_week, stat_cols, stat_map)

    print(f"\n【3/5】交易與 Waiver 紀錄...")
    trades, waivers, drops = fetch_transactions(token, league_key, team_key)

    print(f"\n【4/5】計算交易 ROI...")
    roi_results, compare_cols = calc_trade_roi(
        token, league_key, trades, player_weekly_detail, week_dates, stat_cols, stat_map,
        start_week, end_week, global_name_to_pkey)
    print(f"  共 {len(roi_results)} 筆交易分析完成")

    print(f"\n【5/5】計算 Waiver 撿人評估...")
    waiver_results, waiver_compare_cols = calc_waiver_roi(
        waivers, drops, player_weekly_detail, week_dates, stat_cols)
    print(f"  共 {len(waiver_results)} 筆撿人紀錄分析完成")

    if not player_totals:
        print("沒有球員數據"); return

    print("\n匯出 CSV...")
    export_csvs(team_name, player_totals, team_weekly_summary,
                player_weekly_detail, stat_cols,
                matchups, cat_record, trades, waivers, stat_map,
                roi_results, compare_cols,
                waiver_results, waiver_compare_cols)

    print("\n生成 HTML 儀表板...")
    html_path = generate_html_dashboard(
        team_name, team_weekly_summary, player_weekly_detail,
        stat_cols, matchups, cat_record, trades,
        roi_results, compare_cols, stat_map,
        waiver_results, waiver_compare_cols, player_totals)
    webbrowser.open(f"file://{html_path}")
    print(f"   fantasy_dashboard.html 已開啟")

    wins = sum(1 for m in matchups if m["result"] == "W")
    losses = sum(1 for m in matchups if m["result"] == "L")
    ties = sum(1 for m in matchups if m["result"] == "T")
    print(f"\n整季戰績：{wins} 勝 {losses} 敗 {ties} 平")

    sort_col = "PTS" if "PTS" in stat_cols else stat_cols[0]
    ranked = sorted(player_totals.items(), key=lambda x: x[1].get(sort_col, 0), reverse=True)
    print(f"\n球員整季前 10 名（依 {sort_col}）：")
    for rank, (pid, d) in enumerate(ranked[:10], 1):
        pts = to_float(d.get("PTS", d.get(sort_col, 0)))
        print(f"  {rank:>2}. {d['name']:<22} {pts:>7.0f} {sort_col}  {d['weeks']} 週  {d.get('status','')}")

    if waiver_results:
        print(f"\nWaiver 撿人前 5 名（依週均 PTS）：")
        for r in waiver_results[:5]:
            print(f"  {r['player']:<22} 週均 {r['pts_avg']:>6.1f} PTS  在陣 {r['weeks_on_roster']} 週  ({r['date']} 撿入)")


if __name__ == "__main__":
    main()