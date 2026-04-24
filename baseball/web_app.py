#!/usr/bin/env python3
"""
Yahoo Fantasy Baseball 交易分析器 - Web 介面
啟動後自動開啟瀏覽器，可在網頁上直接輸入球員進行交易分析。

使用前請確認 Yahoo Developer App 的 Redirect URI 包含：
    http://localhost:5000/callback
"""

import os, sys, secrets, threading, webbrowser
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, session, jsonify, redirect
import requests as http

from baseball_trade_analyzer import (
    search_players_web, run_analysis,
    get_mlb_leagues, get_stat_map,
    LEAGUES, CLIENT_ID, CLIENT_SECRET,
    AUTH_URL, TOKEN_URL,
)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

WEB_REDIRECT_URI = "http://localhost:5000/callback"

_store = {}   # token, leagues, stat_maps

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>交易分析器</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 18px 28px; border-bottom: 1px solid #334155;
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.3rem; font-weight: 700; color: #f8fafc; }
  header span { font-size: .8rem; background: #0ea5e9; color: #fff;
                padding: 2px 8px; border-radius: 12px; }
  #league-bar { background: #1e293b; padding: 10px 28px; border-bottom: 1px solid #334155;
                display: flex; align-items: center; gap: 10px; font-size: .9rem; }
  #league-bar label { color: #94a3b8; }
  #league-select { background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
                   padding: 5px 10px; border-radius: 6px; font-size: .9rem; }
  main { max-width: 1000px; margin: 28px auto; padding: 0 20px; }
  .trade-grid { display: grid; grid-template-columns: 1fr 48px 1fr; gap: 0; }
  .side-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }
  .side-card h2 { font-size: 1rem; font-weight: 600; margin-bottom: 14px;
                  display: flex; align-items: center; gap: 6px; }
  .give h2 { color: #f87171; }
  .get  h2 { color: #4ade80; }
  .arrow-col { display: flex; align-items: center; justify-content: center;
               color: #64748b; font-size: 1.4rem; }
  .search-wrap { position: relative; margin-bottom: 10px; }
  .search-wrap input { width: 100%; background: #0f172a; border: 1px solid #334155;
                       color: #e2e8f0; padding: 8px 12px; border-radius: 8px;
                       font-size: .9rem; outline: none; }
  .search-wrap input:focus { border-color: #0ea5e9; }
  .dropdown { position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 99;
              background: #1e293b; border: 1px solid #334155; border-radius: 8px;
              overflow: hidden; box-shadow: 0 8px 20px rgba(0,0,0,.4); display: none; }
  .dropdown.open { display: block; }
  .dropdown-item { padding: 9px 12px; cursor: pointer; font-size: .88rem;
                   display: flex; justify-content: space-between; align-items: center; }
  .dropdown-item:hover { background: #334155; }
  .dropdown-item .pos { color: #64748b; font-size: .78rem; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; min-height: 36px; }
  .chip { background: #334155; border-radius: 20px; padding: 4px 10px 4px 12px;
          font-size: .83rem; display: flex; align-items: center; gap: 6px; }
  .chip button { background: none; border: none; color: #94a3b8; cursor: pointer;
                 font-size: .9rem; line-height: 1; padding: 0; }
  .chip button:hover { color: #f87171; }
  .analyze-btn { display: block; width: 100%; margin: 22px 0; padding: 13px;
                 background: linear-gradient(135deg, #0ea5e9, #6366f1);
                 border: none; border-radius: 10px; color: #fff; font-size: 1rem;
                 font-weight: 600; cursor: pointer; transition: opacity .2s; }
  .analyze-btn:hover { opacity: .88; }
  .analyze-btn:disabled { opacity: .4; cursor: not-allowed; }
  #loading { text-align: center; color: #64748b; padding: 20px; display: none; }
  .spinner { display: inline-block; width: 22px; height: 22px; border: 3px solid #334155;
             border-top-color: #0ea5e9; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #results { margin-top: 8px; }
  .result-header { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
                   padding: 16px 20px; margin-bottom: 16px; }
  .result-header .labels { display: flex; gap: 16px; align-items: center;
                            font-size: .95rem; flex-wrap: wrap; }
  .result-header .give-lbl { color: #f87171; font-weight: 600; }
  .result-header .get-lbl  { color: #4ade80; font-weight: 600; }
  .result-header .arrow { color: #64748b; }
  .period-tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
  .tab-btn { padding: 6px 14px; border-radius: 20px; border: 1px solid #334155;
             background: transparent; color: #94a3b8; cursor: pointer; font-size: .85rem; }
  .tab-btn.active { background: #0ea5e9; border-color: #0ea5e9; color: #fff; }
  .period-panel { display: none; }
  .period-panel.active { display: block; }
  .stats-section { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
                   padding: 16px; margin-bottom: 12px; }
  .stats-section h3 { font-size: .85rem; color: #64748b; margin-bottom: 12px;
                       text-transform: uppercase; letter-spacing: .05em; }
  table { width: 100%; border-collapse: collapse; font-size: .88rem; }
  th { color: #64748b; font-weight: 500; text-align: right; padding: 4px 8px; font-size: .8rem; }
  th:first-child { text-align: left; }
  td { padding: 6px 8px; text-align: right; border-top: 1px solid #1e293b; }
  td:first-child { text-align: left; font-weight: 500; color: #cbd5e1; }
  tr:hover td { background: #334155; }
  .win  { color: #4ade80; font-weight: 600; }
  .lose { color: #f87171; font-weight: 600; }
  .tie  { color: #64748b; }
  .summary-row { margin-top: 10px; padding: 10px 14px; border-radius: 8px;
                 font-size: .9rem; font-weight: 600; text-align: center; }
  .summary-win  { background: rgba(74,222,128,.12); color: #4ade80; }
  .summary-lose { background: rgba(248,113,113,.12); color: #f87171; }
  .summary-tie  { background: rgba(100,116,139,.12); color: #94a3b8; }
  #error-box { background: rgba(248,113,113,.1); border: 1px solid #f87171;
               border-radius: 8px; padding: 12px 16px; color: #f87171;
               margin-bottom: 16px; display: none; font-size: .9rem; }
</style>
</head>
<body>
<header>
  <h1>⚾ 交易分析器</h1>
  <span>Yahoo Fantasy Baseball</span>
</header>
<div id="league-bar">
  <label for="league-select">聯盟</label>
  <select id="league-select"><option value="">載入中...</option></select>
</div>
<main>
  <div id="error-box"></div>
  <div class="trade-grid">
    <div class="side-card give">
      <h2>📤 你送出的球員</h2>
      <div class="search-wrap">
        <input id="give-search" type="text" placeholder="輸入球員名字搜尋..." autocomplete="off">
        <div id="give-dropdown" class="dropdown"></div>
      </div>
      <div id="give-chips" class="chips"></div>
    </div>
    <div class="arrow-col">⇌</div>
    <div class="side-card get">
      <h2>📥 你收到的球員</h2>
      <div class="search-wrap">
        <input id="get-search" type="text" placeholder="輸入球員名字搜尋..." autocomplete="off">
        <div id="get-dropdown" class="dropdown"></div>
      </div>
      <div id="get-chips" class="chips"></div>
    </div>
  </div>
  <button class="analyze-btn" id="analyze-btn">開始分析</button>
  <div id="loading"><div class="spinner"></div><p style="margin-top:10px">抓取球員數據中，請稍候...</p></div>
  <div id="results"></div>
</main>

<script>
const givePlayers = [], getPlayers = [];
let debounceTimer = null;

function getLeagueKey() {
  return document.getElementById("league-select").value;
}

async function loadLeagues() {
  const r = await fetch("/api/leagues");
  if (!r.ok) { window.location = "/auth"; return; }
  const leagues = await r.json();
  const sel = document.getElementById("league-select");
  sel.innerHTML = leagues.map(l =>
    `<option value="${l.key}">${l.name} (${l.season})</option>`
  ).join("");
}

function setupSearch(inputId, dropdownId, players, chips) {
  const input = document.getElementById(inputId);
  const dropdown = document.getElementById(dropdownId);

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < 2) { dropdown.classList.remove("open"); return; }
    debounceTimer = setTimeout(() => doSearch(q, dropdown, players, chips, input), 300);
  });

  input.addEventListener("keydown", e => {
    if (e.key === "Escape") dropdown.classList.remove("open");
  });

  document.addEventListener("click", e => {
    if (!input.contains(e.target) && !dropdown.contains(e.target))
      dropdown.classList.remove("open");
  });
}

async function doSearch(q, dropdown, players, chipsEl, input) {
  const leagueKey = getLeagueKey();
  if (!leagueKey) return;
  const r = await fetch(`/api/search?name=${encodeURIComponent(q)}&league_key=${encodeURIComponent(leagueKey)}`);
  const results = await r.json();
  if (!results.length) { dropdown.classList.remove("open"); return; }

  dropdown.innerHTML = results.map(p =>
    `<div class="dropdown-item" data-key="${p.key}" data-name="${p.name}" data-pos="${p.position}" data-team="${p.team}">
      <span>${p.name}</span>
      <span class="pos">${p.team} · ${p.position}</span>
    </div>`
  ).join("");

  dropdown.querySelectorAll(".dropdown-item").forEach(item => {
    item.addEventListener("click", () => {
      addPlayer(item.dataset.key, item.dataset.name, players, chipsEl);
      input.value = "";
      dropdown.classList.remove("open");
    });
  });
  dropdown.classList.add("open");
}

function addPlayer(key, name, players, chipsEl) {
  if (players.find(p => p.key === key)) return;
  players.push({ key, name });
  renderChips(players, chipsEl);
}

function removePlayer(key, players, chipsEl) {
  const idx = players.findIndex(p => p.key === key);
  if (idx !== -1) players.splice(idx, 1);
  renderChips(players, chipsEl);
}

function renderChips(players, chipsEl) {
  chipsEl.innerHTML = players.map(p =>
    `<div class="chip">
      <span>${p.name}</span>
      <button onclick="removePlayer('${p.key}', ${chipsEl.id === 'give-chips' ? 'givePlayers' : 'getPlayers'}, document.getElementById('${chipsEl.id}'))" title="移除">✕</button>
    </div>`
  ).join("");
}

document.getElementById("analyze-btn").addEventListener("click", async () => {
  const leagueKey = getLeagueKey();
  if (!leagueKey)      return showError("請先選擇聯盟");
  if (!givePlayers.length) return showError("請加入至少一位「送出」球員");
  if (!getPlayers.length)  return showError("請加入至少一位「收到」球員");
  clearError();

  document.getElementById("analyze-btn").disabled = true;
  document.getElementById("loading").style.display = "block";
  document.getElementById("results").innerHTML = "";

  try {
    const r = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ league_key: leagueKey, give: givePlayers, get: getPlayers }),
    });
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    renderResults(data);
  } catch (e) {
    showError("分析失敗：" + e.message);
  } finally {
    document.getElementById("analyze-btn").disabled = false;
    document.getElementById("loading").style.display = "none";
  }
});

function renderResults(data) {
  const periods = ["本季", "近14天", "近30天", "上季"];
  const giveLabel = data.give, getLabel = data.get;

  let html = `<div class="result-header">
    <div class="labels">
      <span class="give-lbl">📤 ${giveLabel}</span>
      <span class="arrow">⇌</span>
      <span class="get-lbl">📥 ${getLabel}</span>
    </div>
  </div>`;

  html += `<div class="period-tabs">` +
    periods.map((p, i) =>
      `<button class="tab-btn${i === 0 ? ' active' : ''}" onclick="switchTab(${i})">${p}</button>`
    ).join("") + `</div>`;

  periods.forEach((period, i) => {
    const a = data.analysis[period];
    const ow = a.overall_win, ol = a.overall_lose, ot = a.overall_tie;
    const verdict = ow > ol ? `收到方佔優（${ow}勝 ${ol}負 ${ot}平）`
                 : ol > ow ? `送出方佔優（${ol}勝 ${ow}負 ${ot}平）`
                 : `勢均力敵（${ow}勝 ${ol}負 ${ot}平）`;
    const summaryClass = ow > ol ? "summary-win" : ol > ow ? "summary-lose" : "summary-tie";

    html += `<div class="period-panel${i === 0 ? ' active' : ''}" id="panel-${i}">`;
    html += renderTable("打者", a.batter, giveLabel, getLabel);
    html += renderTable("投手", a.pitcher, giveLabel, getLabel);
    html += `<div class="summary-row ${summaryClass}">★ 綜合評估：${verdict}</div>`;
    html += `</div>`;
  });

  document.getElementById("results").innerHTML = html;
}

function renderTable(title, section, giveLabel, getLabel) {
  const rows = section.detail.map(r => {
    const gv = r[giveLabel], rv = r[getLabel];
    const fmtG = r.is_avg ? gv.toFixed(3) : Math.round(gv);
    const fmtR = r.is_avg ? rv.toFixed(3) : Math.round(rv);
    const isWin  = r.outcome === "get";
    const isLose = r.outcome === "give";
    const cls = isWin ? "win" : isLose ? "lose" : "tie";
    const icon = isWin ? "✅" : isLose ? "❌" : "—";
    return `<tr>
      <td>${r.cat}</td>
      <td class="${isLose ? 'lose' : ''}">${fmtG}</td>
      <td class="${isWin  ? 'win'  : ''}">${fmtR}</td>
      <td class="${cls}">${icon}</td>
    </tr>`;
  }).join("");

  const w = section.win, l = section.lose, t = section.tie;
  return `<div class="stats-section">
    <h3>${title} <span style="color:#94a3b8;font-size:.75rem;text-transform:none;margin-left:6px">${w}優 ${l}劣 ${t}平</span></h3>
    <table>
      <thead><tr>
        <th>類別</th>
        <th>送出</th>
        <th>收到</th>
        <th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

function switchTab(idx) {
  document.querySelectorAll(".tab-btn").forEach((b, i) =>
    b.classList.toggle("active", i === idx));
  document.querySelectorAll(".period-panel").forEach((p, i) =>
    p.classList.toggle("active", i === idx));
}

function showError(msg) {
  const el = document.getElementById("error-box");
  el.textContent = msg;
  el.style.display = "block";
}
function clearError() {
  document.getElementById("error-box").style.display = "none";
}

loadLeagues();
setupSearch("give-search", "give-dropdown", givePlayers, document.getElementById("give-chips"));
setupSearch("get-search",  "get-dropdown",  getPlayers,  document.getElementById("get-chips"));
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if not _store.get("token"):
        return redirect("/auth")
    return HTML


@app.route("/auth")
def auth():
    if not CLIENT_ID:
        return "請先設定 YAHOO_CLIENT_ID 環境變數", 500
    url = (f"{AUTH_URL}?client_id={CLIENT_ID}"
           f"&redirect_uri={WEB_REDIRECT_URI}&response_type=code")
    return redirect(url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "授權失敗：找不到 code", 400
    resp = http.post(TOKEN_URL,
                     data={"grant_type": "authorization_code", "code": code,
                           "redirect_uri": WEB_REDIRECT_URI},
                     auth=(CLIENT_ID, CLIENT_SECRET))
    if resp.status_code != 200:
        return f"Token 取得失敗：{resp.text}", 400
    _store["token"] = resp.json()["access_token"]
    return redirect("/")


@app.route("/api/leagues")
def api_leagues():
    token = _store.get("token")
    if not token:
        return jsonify({"error": "未授權"}), 401
    if "leagues" not in _store:
        _store["leagues"] = get_mlb_leagues(token)
    return jsonify(_store["leagues"])


@app.route("/api/search")
def api_search():
    token = _store.get("token")
    if not token:
        return jsonify({"error": "未授權"}), 401
    name = request.args.get("name", "").strip()
    league_key = request.args.get("league_key", "").strip()
    if not name or not league_key:
        return jsonify([])
    return jsonify(search_players_web(token, league_key, name))


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    token = _store.get("token")
    if not token:
        return jsonify({"error": "未授權"}), 401

    body = request.get_json(force=True)
    league_key  = body.get("league_key", "")
    give_players = body.get("give", [])   # [{"key": ..., "name": ...}]
    get_players  = body.get("get",  [])

    if not league_key or not give_players or not get_players:
        return jsonify({"error": "缺少必要參數"}), 400

    leagues = _store.get("leagues", [])
    league_id = next((str(lg.get("id", "")) for lg in leagues if lg["key"] == league_key), "")
    league_cfg = next((cfg for cfg in LEAGUES.values() if cfg["id"] == league_id), LEAGUES["1"])

    stat_maps = _store.setdefault("stat_maps", {})
    if league_key not in stat_maps:
        stat_maps[league_key] = get_stat_map(token, league_key)
    stat_map = stat_maps[league_key]

    result = run_analysis(token, league_key, league_cfg, stat_map, give_players, get_players)
    return jsonify(result)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not CLIENT_ID:
        print("⚠️  請先設定 YAHOO_CLIENT_ID 和 YAHOO_CLIENT_SECRET 環境變數")
        sys.exit(1)

    print("=" * 55)
    print("Yahoo Fantasy Baseball 交易分析器 - Web 介面")
    print("=" * 55)
    print("重要：請確認 Yahoo Developer App 的 Redirect URI 已加入：")
    print("  http://localhost:5000/callback")
    print("=" * 55)
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(host="localhost", port=5000, debug=False)
