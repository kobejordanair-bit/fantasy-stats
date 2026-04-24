#!/usr/bin/env python3
"""
Yahoo Fantasy Baseball - 交易分析器
支援聯盟：
  1. 第一屆大聯盟卡舖珠球盃錦標賽 (ID: 215967)
  2. SICCO_D_CUP (ID: 218188)
"""

import requests, json, os, time, webbrowser
from urllib.parse import urlparse, parse_qs
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID     = os.getenv("YAHOO_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET", "")

REDIRECT_URI = "https://localhost:8080"
AUTH_URL     = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL    = "https://api.login.yahoo.com/oauth2/get_token"
BASE_URL     = "https://fantasysports.yahooapis.com/fantasy/v2"

# ── 聯盟設定 ──────────────────────────────────────────────────────────────────
LEAGUES = {
    "1": {
        "name": "第一屆大聯盟卡舖珠球盃錦標賽",
        "id": "215967",
        "batter_cats": ["R", "H", "HR", "RBI", "SB", "BB", "K", "TB", "E", "OPS"],
        "pitcher_cats": ["W", "L", "HR", "K", "ERA", "WHIP", "K/9", "BB/9", "QS", "NSVH"],
        "negative": {"K", "E", "L", "HR_p", "ERA", "WHIP", "BB/9"},
    },
    "2": {
        "name": "SICCO_D_CUP",
        "id": "218188",
        "batter_cats": ["R", "H", "2B", "3B", "HR", "RBI", "SB", "BB", "OBP"],
        "pitcher_cats": ["W", "SV", "K", "HLD", "ERA", "WHIP", "K/BB", "K/9", "RW", "QS"],
        "negative": {"ERA", "WHIP"},
    },
}

# Yahoo stat_id → 縮寫對照（MLB 常用）
STAT_ID_MAP = {
    "7":  "R",    "8":  "H",    "12": "HR",   "13": "RBI",  "16": "SB",
    "18": "BB",   "19": "K",    "20": "TB",    "21": "E",    "55": "OPS",
    "56": "AVG",  "57": "OBP",  "58": "SLG",
    "28": "2B",   "29": "3B",
    # 投手
    "50": "IP",   "32": "W",    "33": "L",     "34": "SV",   "35": "HLD",
    "36": "ERA",  "37": "WHIP", "42": "K",     "48": "QS",
    "57": "K/9",  "60": "BB/9", "61": "K/BB",
    "83": "NSVH", "84": "RW",
    # Yahoo 有時用不同 ID
    "26": "GP",
}

AVG_CATS = {"AVG", "OBP", "SLG", "OPS", "ERA", "WHIP", "K/9", "BB/9", "K/BB"}


# ── OAuth ─────────────────────────────────────────────────────────────────────

def authorize():
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
    resp = requests.post(TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        auth=(CLIENT_ID, CLIENT_SECRET))
    if resp.status_code != 200:
        raise ValueError(f"Token 失敗: {resp.text}")
    print("授權成功！\n")
    return resp.json()["access_token"]


def api_get(token, path, retries=3):
    url = f"{BASE_URL}{path}?format=json"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if resp.status_code == 401:
                raise ValueError("Token 過期，請重新執行")
            if resp.status_code == 999:
                print("Yahoo 限速，等待 15 秒...")
                time.sleep(15)
                continue
            if resp.status_code != 200:
                raise ValueError(f"API 錯誤 {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        except ValueError:
            raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 5)
            else:
                raise


# ── 聯盟 / 球隊 ───────────────────────────────────────────────────────────────

def get_mlb_leagues(token):
    data = api_get(token, "/users;use_login=1/games;game_codes=mlb/leagues")
    games = data["fantasy_content"]["users"]["0"]["user"][1]["games"]
    leagues = []
    for i in range(games["count"]):
        game = games[str(i)]["game"]
        game_leagues = game[1].get("leagues", {})
        for j in range(game_leagues.get("count", 0)):
            lg = game_leagues[str(j)]["league"][0]
            leagues.append({
                "key": lg["league_key"],
                "name": lg["name"],
                "season": lg["season"],
                "id": lg.get("league_id", ""),
            })
    return leagues


def get_all_teams(token, league_key):
    data = api_get(token, f"/league/{league_key}/teams")
    teams_raw = data["fantasy_content"]["league"][1]["teams"]
    teams = {}
    for i in range(teams_raw["count"]):
        t = teams_raw[str(i)]["team"][0]
        key = name = None
        for attr in t:
            if isinstance(attr, dict):
                if "team_key" in attr: key = attr["team_key"]
                if "name" in attr: name = attr["name"]
        if key and name:
            teams[key] = name
    return teams


def get_stat_map(token, league_key):
    """從 API 動態抓取 stat_id → 縮寫 對照表"""
    try:
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
            if sid:
                stat_map[sid] = name
        return stat_map
    except Exception:
        return STAT_ID_MAP.copy()


# ── 球員數據抓取 ──────────────────────────────────────────────────────────────

def parse_player_stats(player_data):
    """從 Yahoo player blob 解析 stat_id → value"""
    stats = {}

    def find_stats_block(obj):
        if isinstance(obj, dict):
            if "player_stats" in obj:
                return obj["player_stats"]
            for v in obj.values():
                r = find_stats_block(v)
                if r: return r
        elif isinstance(obj, list):
            for item in obj:
                r = find_stats_block(item)
                if r: return r
        return None

    block = find_stats_block(player_data)
    if not block:
        return stats

    coverage = block.get("coverage_type", "")
    raw = block.get("stats", [])
    s_list = ([v for k, v in raw.items() if str(k) != "count" and isinstance(v, dict)]
              if isinstance(raw, dict) else raw)

    for stat_obj in s_list:
        if not isinstance(stat_obj, dict) or "stat" not in stat_obj:
            continue
        s = stat_obj["stat"]
        sid = str(s.get("stat_id", ""))
        val = str(s.get("value", "0")).strip()
        if val in ("-", "", "None", "N/A"):
            val = "0"
        stats[sid] = val

    return stats


def sid_to_name(sid, stat_map):
    return stat_map.get(str(sid), STAT_ID_MAP.get(str(sid), str(sid)))


def fetch_player_stats(token, league_key, player_key, stat_type, stat_map, week=None):
    """
    stat_type: 'season' | 'last_week' | 'last_month' | 'average_season'
    抓取指定類型的球員統計，回傳 {縮寫: 值} dict
    """
    if stat_type == "season":
        url = f"/league/{league_key}/players;player_keys={player_key}/stats;type=season"
    elif stat_type == "last_week":
        url = f"/league/{league_key}/players;player_keys={player_key}/stats;type=lastweek"
    elif stat_type == "last_month":
        url = f"/league/{league_key}/players;player_keys={player_key}/stats;type=lastmonth"
    elif stat_type == "average_season":
        url = f"/league/{league_key}/players;player_keys={player_key}/stats;type=season_average"
    else:
        url = f"/league/{league_key}/players;player_keys={player_key}/stats;type=season"

    try:
        data = api_get(token, url, retries=2)
    except Exception:
        return {}

    def find_players_block(obj):
        if isinstance(obj, dict):
            if "0" in obj and isinstance(obj["0"], dict) and "player" in obj["0"]:
                return obj
            for v in obj.values():
                r = find_players_block(v)
                if r: return r
        elif isinstance(obj, list):
            for item in obj:
                r = find_players_block(item)
                if r: return r
        return None

    players = find_players_block(data)
    if not players or players.get("count", 0) == 0:
        return {}

    player_data = players["0"]["player"]
    raw_stats = parse_player_stats(player_data)

    result = {}
    for sid, val in raw_stats.items():
        name = sid_to_name(sid, stat_map)
        try:
            result[name] = float(val)
        except ValueError:
            result[name] = 0.0
    return result


def fetch_player_prev_season_stats(token, player_key, stat_map):
    """抓取上一個賽季的累積數據（透過 /player/ 根節點）"""
    try:
        data = api_get(token, f"/player/{player_key}/stats;type=season", retries=2)
    except Exception:
        return {}

    def find_players_block(obj):
        if isinstance(obj, dict):
            if "player_stats" in obj:
                return obj
            for v in obj.values():
                r = find_players_block(v)
                if r: return r
        elif isinstance(obj, list):
            for item in obj:
                r = find_players_block(item)
                if r: return r
        return None

    block = find_players_block(data)
    if not block:
        return {}

    raw_stats = parse_player_stats(data)
    result = {}
    for sid, val in raw_stats.items():
        name = sid_to_name(sid, stat_map)
        try:
            result[name] = float(val)
        except ValueError:
            result[name] = 0.0
    return result


def search_player(token, league_key, name):
    """搜尋球員，回傳 player_key 和顯示名稱"""
    try:
        search = name.replace(" ", "%20")
        data = api_get(token, f"/league/{league_key}/players;search={search}", retries=2)

        def find_players_block(obj):
            if isinstance(obj, dict):
                if "0" in obj and isinstance(obj["0"], dict) and "player" in obj["0"]:
                    return obj
                for v in obj.values():
                    r = find_players_block(v)
                    if r: return r
            elif isinstance(obj, list):
                for item in obj:
                    r = find_players_block(item)
                    if r: return r
            return None

        players = find_players_block(data)
        if not players or players.get("count", 0) == 0:
            return None, None

        results = []
        for i in range(min(players["count"], 5)):
            p = players[str(i)]["player"][0]
            pkey = pname = pos = None
            for attr in p:
                if isinstance(attr, dict):
                    if "player_key" in attr: pkey = attr["player_key"]
                    if "name" in attr and isinstance(attr["name"], dict):
                        pname = attr["name"].get("full")
                    if "display_position" in attr: pos = attr["display_position"]
            if pkey and pname:
                results.append((pkey, pname, pos or ""))

        if not results:
            return None, None
        if len(results) == 1:
            return results[0][0], results[0][1]

        print(f"\n找到 {len(results)} 位球員：")
        for i, (k, n, p) in enumerate(results):
            print(f"  {i+1}. {n}（{p}）")
        try:
            choice = int(input("選擇編號（預設 1）: ").strip() or "1") - 1
            return results[choice][0], results[choice][1]
        except Exception:
            return results[0][0], results[0][1]

    except Exception:
        return None, None


# ── 交易評估核心 ──────────────────────────────────────────────────────────────

def to_float(val, default=0.0):
    try:
        return float(val)
    except Exception:
        return default


def compare_stats(give_stats, get_stats, cats, negative_cats, label_give, label_get):
    """逐類別比較兩組數據，回傳比較結果"""
    results = []
    my_win = my_lose = tie = 0

    for cat in cats:
        g_val = to_float(give_stats.get(cat, 0))
        r_val = to_float(get_stats.get(cat, 0))

        is_negative = cat in negative_cats
        if g_val == r_val:
            winner = "平"
            tie += 1
        elif (g_val > r_val) != is_negative:
            winner = f"{label_get} 較優"
            my_win += 1
        else:
            winner = f"{label_give} 較優"
            my_lose += 1

        results.append({
            "cat": cat,
            label_give: g_val,
            label_get: r_val,
            "result": winner,
            "is_avg": cat in AVG_CATS,
        })

    return results, my_win, my_lose, tie


def fetch_all_stats_for_player(token, league_key, player_key, player_name, stat_map):
    """抓取一位球員的本季、最近14天、最近30天、上季數據"""
    print(f"    抓取 {player_name} 的數據...", end=" ", flush=True)
    stats = {}

    stats["本季"] = fetch_player_stats(token, league_key, player_key, "season", stat_map)
    time.sleep(0.3)
    stats["近14天"] = fetch_player_stats(token, league_key, player_key, "last_week", stat_map)
    time.sleep(0.3)
    stats["近30天"] = fetch_player_stats(token, league_key, player_key, "last_month", stat_map)
    time.sleep(0.3)
    stats["上季"] = fetch_player_prev_season_stats(token, player_key, stat_map)
    time.sleep(0.3)

    print("✓")
    return stats


def fmt_val(val, is_avg):
    if is_avg:
        return f"{val:.3f}"
    return f"{val:.0f}"


def print_comparison(results, my_win, my_lose, tie, label_give, label_get, period):
    width = 44
    print(f"\n  {'─'*width}")
    print(f"  【{period}】 {label_get} 收到 vs {label_give} 送出")
    print(f"  {'─'*width}")
    print(f"  {'類別':<8} {'收到':>10} {'送出':>10}  結果")
    print(f"  {'─'*width}")
    for r in results:
        give_s = fmt_val(r[label_give], r["is_avg"])
        get_s  = fmt_val(r[label_get],  r["is_avg"])
        result_icon = (
            "✅" if f"{label_get}" in r["result"] else
            "❌" if f"{label_give}" in r["result"] else
            "〰️"
        )
        print(f"  {r['cat']:<8} {get_s:>10} {give_s:>10}  {result_icon} {r['result']}")
    print(f"  {'─'*width}")
    overall = "收到方優勢" if my_win > my_lose else "送出方優勢" if my_lose > my_win else "勢均力敵"
    print(f"  總評：{my_win}項優 / {my_lose}項劣 / {tie}項平 → {overall}")


def analyze_trade(token, league_key, league_cfg, stat_map):
    batter_cats  = league_cfg["batter_cats"]
    pitcher_cats = league_cfg["pitcher_cats"]
    negative     = league_cfg["negative"]
    all_cats     = batter_cats + pitcher_cats

    print("\n" + "=" * 50)
    print("交易分析")
    print("=" * 50)
    print("請輸入交易雙方的球員（名字用逗號分開）")

    give_input = input("\n【你送出的球員】：").strip()
    get_input  = input("【你收到的球員】：").strip()

    give_names = [n.strip() for n in give_input.split(",") if n.strip()]
    get_names  = [n.strip() for n in get_input.split(",") if n.strip()]

    if not give_names or not get_names:
        print("輸入不完整，請重新執行")
        return

    # 搜尋球員
    give_players = []
    get_players  = []

    print("\n搜尋球員中...")
    for name in give_names:
        pkey, pname = search_player(token, league_key, name)
        if pkey:
            give_players.append((pkey, pname))
        else:
            print(f"  ⚠️ 找不到「{name}」，跳過")

    for name in get_names:
        pkey, pname = search_player(token, league_key, name)
        if pkey:
            get_players.append((pkey, pname))
        else:
            print(f"  ⚠️ 找不到「{name}」，跳過")

    if not give_players or not get_players:
        print("球員搜尋失敗，請重試")
        return

    # 抓取數據
    print("\n抓取球員數據（本季 / 近14天 / 近30天 / 上季）...")
    give_stats_all = {}
    get_stats_all  = {}

    for pkey, pname in give_players:
        give_stats_all[pname] = fetch_all_stats_for_player(token, league_key, pkey, pname, stat_map)

    for pkey, pname in get_players:
        get_stats_all[pname] = fetch_all_stats_for_player(token, league_key, pkey, pname, stat_map)

    # 合計多球員數據
    def sum_stats(players_stats_dict, period):
        totals = defaultdict(float)
        for pname, periods in players_stats_dict.items():
            for cat, val in periods.get(period, {}).items():
                if cat not in AVG_CATS:
                    totals[cat] += to_float(val)
        # 平均類別不能直接加總，取最後一個球員的（多球員交易時僅供參考）
        for pname, periods in players_stats_dict.items():
            for cat in AVG_CATS:
                if cat in periods.get(period, {}):
                    totals[cat] = to_float(periods[period][cat])
        return dict(totals)

    periods = ["本季", "近14天", "近30天", "上季"]

    print("\n" + "=" * 50)
    give_label = " + ".join(p[1] for p in give_players)
    get_label  = " + ".join(p[1] for p in get_players)
    print(f"送出：{give_label}")
    print(f"收到：{get_label}")

    for period in periods:
        give_totals = sum_stats(give_stats_all, period)
        get_totals  = sum_stats(get_stats_all,  period)

        # 打者分析
        b_results, b_win, b_lose, b_tie = compare_stats(
            give_totals, get_totals, batter_cats, negative, give_label, get_label)
        # 投手分析
        p_results, p_win, p_lose, p_tie = compare_stats(
            give_totals, get_totals, pitcher_cats, negative, give_label, get_label)

        total_win  = b_win  + p_win
        total_lose = b_lose + p_lose
        total_tie  = b_tie  + p_tie

        print(f"\n{'━'*50}")
        print(f"  ▸ 打者類別（{period}）")
        print_comparison(b_results, b_win, b_lose, b_tie, give_label, get_label, "打者")
        print(f"\n  ▸ 投手類別（{period}）")
        print_comparison(p_results, p_win, p_lose, p_tie, give_label, get_label, "投手")

        overall = "收到方優勢" if total_win > total_lose else "送出方優勢" if total_lose > total_win else "勢均力敵"
        print(f"\n  ★ {period} 綜合：{total_win}優 / {total_lose}劣 / {total_tie}平 → {overall}")

    # 個別球員詳細數據
    print(f"\n{'='*50}")
    print("個別球員數據")
    print(f"{'='*50}")
    all_individual = list(give_stats_all.items()) + list(get_stats_all.items())
    for pname, p_periods in all_individual:
        side = "送出" if pname in [p[1] for p in give_players] else "收到"
        print(f"\n  [{side}] {pname}")
        for period in periods:
            period_data = p_periods.get(period, {})
            if not period_data:
                print(f"    {period}：無數據")
                continue
            vals = []
            for cat in all_cats:
                if cat in period_data:
                    v = period_data[cat]
                    vals.append(f"{cat}={fmt_val(v, cat in AVG_CATS)}")
            print(f"    {period}：{', '.join(vals) if vals else '無相關類別數據'}")

    # 存結果到 JSON
    result = {
        "give": give_label,
        "get": get_label,
        "analysis": {}
    }
    for period in periods:
        give_t = sum_stats(give_stats_all, period)
        get_t  = sum_stats(get_stats_all, period)
        b_res, b_w, b_l, b_t = compare_stats(give_t, get_t, batter_cats, negative, give_label, get_label)
        p_res, p_w, p_l, p_t = compare_stats(give_t, get_t, pitcher_cats, negative, give_label, get_label)
        result["analysis"][period] = {
            "batter": {"win": b_w, "lose": b_l, "tie": b_t, "detail": b_res},
            "pitcher": {"win": p_w, "lose": p_l, "tie": p_t, "detail": p_res},
            "overall_win": b_w + p_w,
            "overall_lose": b_l + p_l,
        }

    out_path = os.path.join(os.path.dirname(__file__), "trade_analysis_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 詳細結果已存至 trade_analysis_result.json")


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    print("Yahoo Fantasy Baseball 交易分析器")
    print("=" * 50)

    if not CLIENT_ID:
        print("⚠️  請先設定 YAHOO_CLIENT_ID 和 YAHOO_CLIENT_SECRET")
        print("   可在 .env 檔案中設定，或直接在 terminal 設定環境變數")
        return

    token = authorize()

    # 選擇聯盟
    print("正在取得你的 MLB Fantasy 聯盟...")
    api_leagues = get_mlb_leagues(token)

    print("\n你的聯盟：")
    for i, lg in enumerate(api_leagues):
        marker = ""
        for cfg in LEAGUES.values():
            if cfg["id"] == str(lg["id"]):
                marker = f"  ← {cfg['name']}"
                break
        print(f"  {i+1}. {lg['name']}（{lg['season']}）{marker}")

    if not api_leagues:
        print("找不到 MLB Fantasy 聯盟"); return

    idx = 0
    if len(api_leagues) > 1:
        idx = int(input("\n選擇聯盟編號：").strip()) - 1

    chosen_league = api_leagues[idx]
    league_key    = chosen_league["key"]
    league_id     = str(chosen_league.get("id", ""))
    print(f"\n選擇：{chosen_league['name']}")

    # 找到對應的聯盟設定
    league_cfg = None
    for cfg in LEAGUES.values():
        if cfg["id"] == league_id:
            league_cfg = cfg
            break

    if not league_cfg:
        print("⚠️  此聯盟無預設設定，使用第一個盟的類別設定")
        league_cfg = LEAGUES["1"]

    print(f"  打者類別：{', '.join(league_cfg['batter_cats'])}")
    print(f"  投手類別：{', '.join(league_cfg['pitcher_cats'])}")

    print("\n取得統計類別對照表...")
    stat_map = get_stat_map(token, league_key)

    while True:
        analyze_trade(token, league_key, league_cfg, stat_map)
        again = input("\n再分析一筆交易？(y/n，預設 y)：").strip().lower()
        if again == "n":
            break

    print("\n分析完成，再見！")


if __name__ == "__main__":
    main()
