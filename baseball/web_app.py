#!/usr/bin/env python3
"""
Yahoo Fantasy Baseball 交易分析器 - Web 介面
授權方式與籃球版相同：開啟 Yahoo 授權後把跳轉網址貼回頁面，不需要 HTTPS。
"""

import os, sys, secrets, threading, webbrowser, time
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, redirect
import requests as http

from baseball_trade_analyzer import (
    search_players_web, run_analysis,
    get_mlb_leagues, get_stat_map,
    get_my_team_key, get_team_roster, get_standings,
    fetch_player_stats,
    lookup_mlbam_id, fetch_savant_percentiles, fetch_fangraphs_stats,
    LEAGUES, CLIENT_ID, CLIENT_SECRET,
    AUTH_URL, TOKEN_URL, AVG_CATS,
)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# 與籃球版共用同一個 redirect URI，不需要本地 HTTPS
WEB_REDIRECT_URI = "https://localhost:8080"

_store = {}   # token, leagues, stat_maps

# ── 授權頁面 ──────────────────────────────────────────────────────────────────

AUTH_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>授權 - 交易分析器</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f172a; color: #e2e8f0; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px;
          padding: 40px; max-width: 480px; width: 100%; }
  h1 { font-size: 1.3rem; margin-bottom: 8px; }
  .sub { color: #64748b; font-size: .9rem; margin-bottom: 28px; }
  .step { display: flex; gap: 12px; margin-bottom: 20px; align-items: flex-start; }
  .step-num { background: #0ea5e9; color: #fff; border-radius: 50%;
              width: 24px; height: 24px; display: flex; align-items: center;
              justify-content: center; font-size: .8rem; font-weight: 700; flex-shrink: 0; }
  .step-text { font-size: .9rem; color: #cbd5e1; line-height: 1.5; }
  .auth-btn { display: block; width: 100%; padding: 12px;
              background: linear-gradient(135deg, #0ea5e9, #6366f1);
              border: none; border-radius: 10px; color: #fff; font-size: 1rem;
              font-weight: 600; cursor: pointer; margin: 24px 0 20px; text-align: center;
              text-decoration: none; }
  .auth-btn:hover { opacity: .88; }
  .paste-wrap { display: none; }
  .paste-wrap label { font-size: .85rem; color: #94a3b8; display: block; margin-bottom: 6px; }
  .paste-wrap input { width: 100%; background: #0f172a; border: 1px solid #334155;
                      color: #e2e8f0; padding: 10px 12px; border-radius: 8px;
                      font-size: .85rem; outline: none; }
  .paste-wrap input:focus { border-color: #0ea5e9; }
  .confirm-btn { display: block; width: 100%; padding: 11px; margin-top: 10px;
                 background: #0ea5e9; border: none; border-radius: 8px;
                 color: #fff; font-size: .95rem; font-weight: 600; cursor: pointer; }
  .confirm-btn:hover { background: #0284c7; }
  #err { color: #f87171; font-size: .85rem; margin-top: 8px; display: none; }
</style>
</head>
<body>
<div class="card">
  <h1>⚾ 交易分析器</h1>
  <p class="sub">Yahoo Fantasy Baseball — 首次使用需授權</p>

  <div class="step">
    <div class="step-num">1</div>
    <div class="step-text">點下方按鈕，在新分頁登入 Yahoo 並允許授權</div>
  </div>
  <div class="step">
    <div class="step-num">2</div>
    <div class="step-text">授權後會跳到一個<strong>無法連線</strong>的頁面，把瀏覽器<strong>網址列的完整網址</strong>複製起來</div>
  </div>
  <div class="step">
    <div class="step-num">3</div>
    <div class="step-text">貼到下方輸入框，按確認</div>
  </div>

  <a id="auth-link" class="auth-btn" href="{auth_url}" target="_blank"
     onclick="document.querySelector('.paste-wrap').style.display='block'">
    開啟 Yahoo 授權頁面
  </a>

  <div class="paste-wrap">
    <label>貼上授權後的完整網址</label>
    <input id="url-input" type="text" placeholder="https://localhost:8080/?code=...">
    <button class="confirm-btn" onclick="submitUrl()">確認授權</button>
    <div id="err"></div>
  </div>
</div>
<script>
async function submitUrl() {
  const url = document.getElementById("url-input").value.trim();
  const err = document.getElementById("err");
  err.style.display = "none";
  if (!url) { err.textContent = "請貼上網址"; err.style.display = "block"; return; }
  const r = await fetch("/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await r.json();
  if (data.ok) { window.location = "/"; }
  else { err.textContent = data.error || "授權失敗，請重試"; err.style.display = "block"; }
}
</script>
</body>
</html>"""

# ── 主頁面 ────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚾ Fantasy Baseball</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header { background: #1e293b; padding: 14px 28px; border-bottom: 1px solid #334155;
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.3rem; font-weight: 700; color: #f8fafc; flex: 1; }
  nav { display: flex; gap: 4px; }
  .nav-btn { padding: 7px 16px; border-radius: 8px; border: 1px solid #334155;
             background: transparent; color: #94a3b8; cursor: pointer; font-size: .88rem;
             font-weight: 500; transition: all .15s; }
  .nav-btn:hover { background: #1e293b; color: #e2e8f0; }
  .nav-btn.active { background: #0ea5e9; border-color: #0ea5e9; color: #fff; }
  #league-bar { background: #1e293b; padding: 9px 28px; border-bottom: 1px solid #334155;
                display: flex; align-items: center; gap: 10px; font-size: .9rem; }
  #league-bar label { color: #94a3b8; }
  #league-select { background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
                   padding: 5px 10px; border-radius: 6px; font-size: .9rem; }
  main { max-width: 1080px; margin: 28px auto; padding: 0 20px; }
  .view { display: none; }
  .view.active { display: block; }
  /* ── 交易分析 ── */
  .trade-grid { display: grid; grid-template-columns: 1fr 48px 1fr; }
  .side-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }
  .side-card h2 { font-size: 1rem; font-weight: 600; margin-bottom: 14px; }
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
  .loading-box { text-align: center; color: #64748b; padding: 32px; display: none; }
  .spinner { display: inline-block; width: 22px; height: 22px; border: 3px solid #334155;
             border-top-color: #0ea5e9; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .result-header { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
                   padding: 16px 20px; margin-bottom: 16px; }
  .result-header .labels { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
  .give-lbl { color: #f87171; font-weight: 600; }
  .get-lbl  { color: #4ade80; font-weight: 600; }
  .period-tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
  .tab-btn { padding: 6px 14px; border-radius: 20px; border: 1px solid #334155;
             background: transparent; color: #94a3b8; cursor: pointer; font-size: .85rem; }
  .tab-btn.active { background: #0ea5e9; border-color: #0ea5e9; color: #fff; }
  .period-panel { display: none; }
  .period-panel.active { display: block; }
  /* ── 通用表格 ── */
  .section-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
                  padding: 16px; margin-bottom: 12px; }
  .section-card h3 { font-size: .85rem; color: #64748b; margin-bottom: 12px;
                     text-transform: uppercase; letter-spacing: .05em; }
  .tbl-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: .87rem; white-space: nowrap; }
  th { color: #64748b; font-weight: 500; text-align: right; padding: 5px 10px; font-size: .78rem; }
  th:first-child, th:nth-child(2), th:nth-child(3) { text-align: left; }
  td { padding: 6px 10px; text-align: right; border-top: 1px solid #0f172a; }
  td:first-child { text-align: left; font-weight: 600; color: #f1f5f9; }
  td:nth-child(2), td:nth-child(3) { text-align: left; color: #94a3b8; font-size: .82rem; }
  tr:hover td { background: #263347; }
  .win  { color: #4ade80; font-weight: 700; }
  .lose { color: #f87171; font-weight: 700; }
  .tie  { color: #64748b; }
  .summary-row { margin-top: 10px; padding: 10px 14px; border-radius: 8px;
                 font-size: .9rem; font-weight: 600; text-align: center; }
  .summary-win  { background: rgba(74,222,128,.12); color: #4ade80; }
  .summary-lose { background: rgba(248,113,113,.12); color: #f87171; }
  .summary-tie  { background: rgba(100,116,139,.12); color: #94a3b8; }
  #error-box { background: rgba(248,113,113,.1); border: 1px solid #f87171;
               border-radius: 8px; padding: 12px 16px; color: #f87171;
               margin-bottom: 16px; display: none; font-size: .9rem; }
  /* ── 積分榜排名色 ── */
  .rank-top { color: #4ade80; font-weight: 700; }
  .rank-bot { color: #f87171; font-weight: 700; }
  .status-dl { color: #f87171; font-size: .78rem; }
  .load-btn { padding: 9px 20px; background: #1e293b; border: 1px solid #334155;
              border-radius: 8px; color: #e2e8f0; cursor: pointer; font-size: .9rem; }
  .load-btn:hover { border-color: #0ea5e9; color: #0ea5e9; }
  /* ── 進階數據 ── */
  .adv-player-bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
                    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
                    padding: 14px 18px; margin-bottom: 16px; }
  .adv-player-name { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; }
  .adv-player-meta { font-size: .8rem; color: #64748b; margin-top: 2px; }
  .adv-badge { padding: 3px 11px; border-radius: 20px; font-size: .78rem; font-weight: 600; }
  .adv-badge.batter  { background: rgba(14,165,233,.15); color: #0ea5e9; }
  .adv-badge.pitcher { background: rgba(99,102,241,.15);  color: #818cf8; }
  .pct-section-title { font-size: .73rem; color: #64748b; text-transform: uppercase;
                       letter-spacing: .07em; margin: 14px 0 4px; padding-bottom: 4px;
                       border-bottom: 1px solid #1e293b; }
  .pct-row { display: flex; align-items: center; gap: 10px; padding: 4px 0; }
  .pct-label { width: 148px; font-size: .82rem; color: #94a3b8;
               text-align: right; flex-shrink: 0; }
  .pct-track { flex: 1; position: relative; height: 6px;
               background: #0f172a; border-radius: 3px; border: 1px solid #334155; }
  .pct-dot { position: absolute; top: 50%; transform: translate(-50%, -50%);
             width: 22px; height: 22px; border-radius: 50%;
             display: flex; align-items: center; justify-content: center;
             font-size: .63rem; font-weight: 700; color: #fff; }
  .pct-val { width: 54px; font-size: .82rem; color: #e2e8f0; flex-shrink: 0; }
  .fg-grid { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 0; }
  .fg-cell { background: #0f172a; border-radius: 8px; padding: 8px 12px;
             min-width: 68px; text-align: center; }
  .fg-key  { display: block; font-size: .7rem; color: #64748b; margin-bottom: 3px; }
  .fg-val  { display: block; font-size: .95rem; font-weight: 700; color: #f1f5f9; }
</style>
</head>
<body>
<header>
  <h1>⚾ Fantasy Baseball</h1>
  <nav>
    <button class="nav-btn active" onclick="switchView('trade')">交易分析</button>
    <button class="nav-btn" onclick="switchView('roster')">我的名單</button>
    <button class="nav-btn" onclick="switchView('standings')">聯盟排名</button>
    <button class="nav-btn" onclick="switchView('advanced')">進階數據</button>
  </nav>
</header>
<div id="league-bar">
  <label for="league-select">聯盟</label>
  <select id="league-select" onchange="onLeagueChange()"><option value="">載入中...</option></select>
</div>
<main>
  <div id="error-box"></div>

  <!-- ── 交易分析 ── -->
  <div id="view-trade" class="view active">
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
    <div id="trade-loading" class="loading-box">
      <div class="spinner"></div><p style="margin-top:10px">抓取球員數據中，請稍候...</p>
    </div>
    <div id="trade-results"></div>
  </div>

  <!-- ── 我的名單 ── -->
  <div id="view-roster" class="view">
    <div style="margin-bottom:16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button class="load-btn" onclick="loadRoster()">載入 / 重新整理</button>
      <button class="load-btn" id="roster-csv-btn" onclick="exportRosterCSV()" style="display:none">匯出 CSV</button>
      <span style="color:#64748b;font-size:.85rem">首次載入約需 30–60 秒</span>
    </div>
    <div id="roster-loading" class="loading-box">
      <div class="spinner"></div><p style="margin-top:10px">抓取名單與數據中...</p>
    </div>
    <div id="roster-content"></div>
  </div>

  <!-- ── 聯盟排名 ── -->
  <div id="view-standings" class="view">
    <div style="margin-bottom:16px;display:flex;gap:10px;align-items:center">
      <button class="load-btn" onclick="loadStandings()">載入 / 重新整理</button>
      <button class="load-btn" id="standings-csv-btn" onclick="exportStandingsCSV()" style="display:none">匯出 CSV</button>
    </div>
    <div id="standings-loading" class="loading-box">
      <div class="spinner"></div><p style="margin-top:10px">抓取積分榜中...</p>
    </div>
    <div id="standings-content"></div>
  </div>

  <!-- ── 進階數據 ── -->
  <div id="view-advanced" class="view">
    <div style="margin-bottom:14px">
      <div class="search-wrap" style="max-width:380px">
        <input id="adv-search" type="text" placeholder="輸入球員名字搜尋..." autocomplete="off">
        <div id="adv-dropdown" class="dropdown"></div>
      </div>
      <p style="color:#64748b;font-size:.82rem;margin-top:6px">
        選擇球員後自動載入 Savant 百分位排名 + FanGraphs 本季數據
      </p>
    </div>
    <div id="adv-loading" class="loading-box">
      <div class="spinner"></div><p style="margin-top:10px">抓取進階數據中，請稍候...</p>
    </div>
    <div id="adv-content"></div>
  </div>
</main>

<script>
const givePlayers = [], getPlayers = [];
let debounceTimer = null;
let rosterData = null, standingsData = null;

// ── 導航 ──────────────────────────────────────────────────────────────────────
function switchView(name) {
  document.querySelectorAll(".view").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
  document.getElementById("view-" + name).classList.add("active");
  event.currentTarget.classList.add("active");
}

function getLeagueKey() {
  return document.getElementById("league-select").value;
}

function onLeagueChange() {
  rosterData = null; standingsData = null;
  document.getElementById("roster-content").innerHTML = "";
  document.getElementById("standings-content").innerHTML = "";
  document.getElementById("roster-csv-btn").style.display = "none";
  document.getElementById("standings-csv-btn").style.display = "none";
}

// ── 聯盟載入 ─────────────────────────────────────────────────────────────────
async function loadLeagues() {
  const r = await fetch("/api/leagues");
  if (!r.ok) { window.location = "/auth"; return; }
  const leagues = await r.json();
  const sel = document.getElementById("league-select");
  sel.innerHTML = leagues.map(l =>
    `<option value="${l.key}">${l.name} (${l.season})</option>`
  ).join("");
}

// ── 球員搜尋 ─────────────────────────────────────────────────────────────────
function setupSearch(inputId, dropdownId, players, chipsId) {
  const input    = document.getElementById(inputId);
  const dropdown = document.getElementById(dropdownId);
  const chipsEl  = document.getElementById(chipsId);

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < 2) { dropdown.classList.remove("open"); return; }
    debounceTimer = setTimeout(() => doSearch(q, dropdown, players, chipsEl, input), 300);
  });
  input.addEventListener("keydown", e => { if (e.key === "Escape") dropdown.classList.remove("open"); });
  document.addEventListener("click", e => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.classList.remove("open");
  });
}

async function doSearch(q, dropdown, players, chipsEl, input) {
  const lk = getLeagueKey();
  if (!lk) return;
  const r = await fetch(`/api/search?name=${encodeURIComponent(q)}&league_key=${encodeURIComponent(lk)}`);
  const results = await r.json();
  if (!results.length) { dropdown.classList.remove("open"); return; }
  dropdown.innerHTML = results.map(p =>
    `<div class="dropdown-item" data-key="${p.key}" data-name="${p.name}">
       <span>${p.name}</span><span class="pos">${p.team} · ${p.position}</span>
     </div>`
  ).join("");
  dropdown.querySelectorAll(".dropdown-item").forEach(item => {
    item.addEventListener("click", () => {
      addPlayer(item.dataset.key, item.dataset.name, players, chipsEl);
      input.value = ""; dropdown.classList.remove("open");
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
    `<div class="chip"><span>${p.name}</span>
     <button onclick="removePlayer('${p.key}',${chipsEl.id==='give-chips'?'givePlayers':'getPlayers'},document.getElementById('${chipsEl.id}'))" title="移除">✕</button>
     </div>`
  ).join("");
}

// ── 交易分析 ─────────────────────────────────────────────────────────────────
document.getElementById("analyze-btn").addEventListener("click", async () => {
  const lk = getLeagueKey();
  if (!lk)               return showError("請先選擇聯盟");
  if (!givePlayers.length) return showError("請加入至少一位「送出」球員");
  if (!getPlayers.length)  return showError("請加入至少一位「收到」球員");
  clearError();
  document.getElementById("analyze-btn").disabled = true;
  document.getElementById("trade-loading").style.display = "block";
  document.getElementById("trade-results").innerHTML = "";
  try {
    const r = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ league_key: lk, give: givePlayers, get: getPlayers }),
    });
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    renderTradeResults(data);
  } catch (e) { showError("分析失敗：" + e.message); }
  finally {
    document.getElementById("analyze-btn").disabled = false;
    document.getElementById("trade-loading").style.display = "none";
  }
});

function renderTradeResults(data) {
  const periods = ["本季", "近14天", "近30天"];
  const gL = data.give, rL = data.get;
  let html = `<div class="result-header"><div class="labels">
    <span class="give-lbl">📤 ${gL}</span><span style="color:#64748b">⇌</span>
    <span class="get-lbl">📥 ${rL}</span></div></div>`;
  html += `<div class="period-tabs">` +
    periods.map((p,i) => `<button class="tab-btn${i===0?' active':''}" onclick="switchPeriodTab(${i},'trade')">${p}</button>`).join("") +
    `</div>`;
  periods.forEach((period, i) => {
    const a = data.analysis[period];
    const ow = a.overall_win, ol = a.overall_lose, ot = a.overall_tie;
    const verdict = ow>ol ? `收到方佔優（${ow}勝 ${ol}負 ${ot}平）`
                 : ol>ow ? `送出方佔優（${ol}勝 ${ow}負 ${ot}平）`
                 : `勢均力敵（${ow}勝 ${ol}負 ${ot}平）`;
    const sc = ow>ol?"summary-win":ol>ow?"summary-lose":"summary-tie";
    html += `<div class="period-panel trade-panel${i===0?' active':''}" id="trade-panel-${i}">`;
    html += renderCatTable("打者", a.batter, gL, rL);
    html += renderCatTable("投手", a.pitcher, gL, rL);
    html += `<div class="summary-row ${sc}">★ 綜合評估：${verdict}</div></div>`;
  });
  document.getElementById("trade-results").innerHTML = html;
}

function renderCatTable(title, section, gL, rL) {
  const rows = section.detail.map(r => {
    const gv = r[gL], rv = r[rL];
    const fG = r.is_avg ? gv.toFixed(3) : Math.round(gv);
    const fR = r.is_avg ? rv.toFixed(3) : Math.round(rv);
    const isWin = r.outcome==="get", isLose = r.outcome==="give";
    const cls = isWin?"win":isLose?"lose":"tie";
    return `<tr><td>${r.cat}</td>
      <td class="${isLose?'lose':''}">${fG}</td>
      <td class="${isWin?'win':''}">${fR}</td>
      <td class="${cls}">${isWin?"✅":isLose?"❌":"—"}</td></tr>`;
  }).join("");
  return `<div class="section-card">
    <h3>${title} <span style="color:#94a3b8;font-size:.75rem;text-transform:none;margin-left:6px">${section.win}優 ${section.lose}劣 ${section.tie}平</span></h3>
    <div class="tbl-wrap"><table>
      <thead><tr><th>類別</th><th>送出</th><th>收到</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div></div>`;
}

function switchPeriodTab(idx, prefix) {
  document.querySelectorAll(`.${prefix}-panel`).forEach((p,i) => p.classList.toggle("active", i===idx));
  const tabs = document.querySelectorAll(".period-tabs .tab-btn");
  tabs.forEach((b,i) => b.classList.toggle("active", i===idx));
}

// ── 我的名單 ─────────────────────────────────────────────────────────────────
async function loadRoster() {
  const lk = getLeagueKey();
  if (!lk) return;
  document.getElementById("roster-loading").style.display = "block";
  document.getElementById("roster-content").innerHTML = "";
  try {
    const r = await fetch(`/api/my_roster?league_key=${encodeURIComponent(lk)}`);
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    renderRoster(data);
  } catch(e) {
    document.getElementById("roster-content").innerHTML =
      `<p style="color:#f87171">載入失敗：${e.message}</p>`;
  } finally {
    document.getElementById("roster-loading").style.display = "none";
  }
}

function renderRoster(data) {
  rosterData = data;
  document.getElementById("roster-csv-btn").style.display = "inline-block";
  const periods = ["本季", "近14天", "近30天"];
  const cats = data.cats;
  let html = `<div class="period-tabs">` +
    periods.map((p,i) => `<button class="tab-btn${i===0?' active':''}" onclick="switchRosterTab(${i})">${p}</button>`).join("") +
    `</div>`;

  periods.forEach((period, pi) => {
    html += `<div class="roster-panel period-panel${pi===0?' active':''}" id="roster-panel-${pi}">
      <div class="section-card"><div class="tbl-wrap"><table>
      <thead><tr>
        <th>球員</th><th>守位</th><th>球隊</th>`;
    cats.forEach(c => { html += `<th>${c}</th>`; });
    html += `</tr></thead><tbody>`;
    data.players.forEach(p => {
      const stats = p.stats[period] || {};
      const statusHtml = p.status && p.status !== "A"
        ? ` <span class="status-dl">${p.status}</span>` : "";
      html += `<tr><td>${p.name}${statusHtml}</td>
        <td>${p.position}</td><td>${p.team}</td>`;
      cats.forEach(c => {
        const v = stats[c];
        const isAvg = data.avg_cats.includes(c);
        html += `<td>${v == null ? "—" : isAvg ? Number(v).toFixed(3) : Math.round(v)}</td>`;
      });
      html += `</tr>`;
    });
    html += `</tbody></table></div></div></div>`;
  });
  document.getElementById("roster-content").innerHTML = html;
}

function switchRosterTab(idx) {
  document.querySelectorAll(".roster-panel").forEach((p,i) => p.classList.toggle("active", i===idx));
  document.querySelectorAll("#view-roster .tab-btn").forEach((b,i) => b.classList.toggle("active", i===idx));
}

// ── 聯盟排名 ─────────────────────────────────────────────────────────────────
async function loadStandings() {
  const lk = getLeagueKey();
  if (!lk) return;
  document.getElementById("standings-loading").style.display = "block";
  document.getElementById("standings-content").innerHTML = "";
  try {
    const r = await fetch(`/api/standings?league_key=${encodeURIComponent(lk)}`);
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    renderStandings(data);
  } catch(e) {
    document.getElementById("standings-content").innerHTML =
      `<p style="color:#f87171">載入失敗：${e.message}</p>`;
  } finally {
    document.getElementById("standings-loading").style.display = "none";
  }
}

function renderStandings(data) {
  standingsData = data;
  document.getElementById("standings-csv-btn").style.display = "inline-block";
  const teams = data.teams, cats = data.cats, neg = data.negative_cats;
  const n = teams.length;

  // 各類別計算排名（1 = 最好）
  const catRanks = {};
  cats.forEach(c => {
    const sorted = [...teams].sort((a,b) =>
      neg.includes(c) ? (a.stats[c]||0)-(b.stats[c]||0) : (b.stats[c]||0)-(a.stats[c]||0));
    catRanks[c] = {};
    sorted.forEach((t,i) => { catRanks[c][t.team_key] = i+1; });
  });

  let html = `<div class="section-card"><div class="tbl-wrap"><table>
    <thead><tr><th>排名</th><th>球隊</th><th>W-L-T</th>`;
  cats.forEach(c => { html += `<th>${c}</th>`; });
  html += `</tr></thead><tbody>`;

  teams.sort((a,b) => (a.rank||99)-(b.rank||99)).forEach(t => {
    const rankCls = (t.rank<=3)?"rank-top":(t.rank>=n-2)?"rank-bot":"";
    html += `<tr><td class="${rankCls}">${t.rank??""}</td>
      <td>${t.name}</td>
      <td style="color:#94a3b8">${t.wins}-${t.losses}-${t.ties}</td>`;
    cats.forEach(c => {
      const v = t.stats[c];
      const r = catRanks[c][t.team_key];
      const cls = r<=3?"rank-top":r>=n-2?"rank-bot":"";
      const isAvg = data.avg_cats.includes(c);
      const disp = v==null?"—": isAvg ? Number(v).toFixed(3) : Math.round(v);
      html += `<td class="${cls}" title="排名 ${r}">${disp}</td>`;
    });
    html += `</tr>`;
  });
  html += `</tbody></table></div></div>`;
  document.getElementById("standings-content").innerHTML = html;
}

// ── 進階數據 ─────────────────────────────────────────────────────────────────
let advPlayer = null;

function setupAdvSearch() {
  const input    = document.getElementById("adv-search");
  const dropdown = document.getElementById("adv-dropdown");
  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < 2) { dropdown.classList.remove("open"); return; }
    debounceTimer = setTimeout(async () => {
      const lk = getLeagueKey();
      if (!lk) return;
      const r = await fetch(`/api/search?name=${encodeURIComponent(q)}&league_key=${encodeURIComponent(lk)}`);
      const results = await r.json();
      if (!results.length) { dropdown.classList.remove("open"); return; }
      dropdown.innerHTML = results.map(p =>
        `<div class="dropdown-item" data-key="${p.key}" data-name="${p.name}" data-pos="${p.position}">
           <span>${p.name}</span><span class="pos">${p.team} · ${p.position}</span>
         </div>`
      ).join("");
      dropdown.querySelectorAll(".dropdown-item").forEach(item => {
        item.addEventListener("click", () => {
          advPlayer = { key: item.dataset.key, name: item.dataset.name, position: item.dataset.pos };
          input.value = item.dataset.name;
          dropdown.classList.remove("open");
          loadAdvancedStats();
        });
      });
      dropdown.classList.add("open");
    }, 300);
  });
  input.addEventListener("keydown", e => { if (e.key === "Escape") dropdown.classList.remove("open"); });
  document.addEventListener("click", e => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) dropdown.classList.remove("open");
  });
}

async function loadAdvancedStats() {
  if (!advPlayer) return;
  const lk = getLeagueKey();
  document.getElementById("adv-loading").style.display = "block";
  document.getElementById("adv-content").innerHTML = "";
  try {
    const r = await fetch(
      `/api/savant?name=${encodeURIComponent(advPlayer.name)}&position=${encodeURIComponent(advPlayer.position)}&league_key=${encodeURIComponent(lk)}`
    );
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    renderAdvancedStats(data);
  } catch(e) {
    document.getElementById("adv-content").innerHTML =
      `<p style="color:#f87171">載入失敗：${e.message}</p>`;
  } finally {
    document.getElementById("adv-loading").style.display = "none";
  }
}

function renderAdvancedStats(data) {
  const isP = data.player_type === "pitcher";
  let html = `<div class="adv-player-bar">
    <div>
      <div class="adv-player-name">${data.name}</div>
      <div class="adv-player-meta">MLBAM ID: ${data.mlbam_id}</div>
    </div>
    <span class="adv-badge ${data.player_type}">${isP ? "投手" : "打者"}</span>
  </div>`;

  // Savant 百分位
  const sv = data.savant;
  if (sv && sv.sections && sv.sections.length) {
    html += `<div class="section-card"><h3>Baseball Savant 百分位排名</h3>`;
    sv.sections.forEach(sec => {
      html += `<div class="pct-section-title">${sec.name}</div>`;
      sec.stats.forEach(st => {
        const p = st.percentile;
        const color = p >= 70 ? "#ef4444" : p <= 30 ? "#3b82f6" : "#64748b";
        const valStr = st.value != null ? st.value : "";
        html += `<div class="pct-row">
          <span class="pct-label">${st.label}</span>
          <div class="pct-track">
            <div class="pct-dot" style="left:${p}%;background:${color}">${p}</div>
          </div>
          <span class="pct-val">${valStr}</span>
        </div>`;
      });
    });
    html += `</div>`;
  } else if (sv && sv.error) {
    html += `<p style="color:#f87171;margin-bottom:12px">Savant 載入失敗：${sv.error}</p>`;
  }

  // FanGraphs
  const fg = data.fangraphs;
  if (fg && Object.keys(fg).length) {
    const meta = [fg.Name, fg.Team].filter(Boolean).join(" · ");
    const entries = Object.entries(fg).filter(([k]) => !["Name","Team"].includes(k));
    html += `<div class="section-card">
      <h3>FanGraphs 本季${meta ? ` <span style="font-weight:400;color:#94a3b8;text-transform:none;font-size:.85rem">${meta}</span>` : ""}</h3>
      <div class="fg-grid">
        ${entries.map(([k,v]) => `<div class="fg-cell"><span class="fg-key">${k}</span><span class="fg-val">${v}</span></div>`).join("")}
      </div>
    </div>`;
  }

  document.getElementById("adv-content").innerHTML = html;
}

// ── CSV 匯出 ──────────────────────────────────────────────────────────────────
function downloadCSV(filename, rows) {
  const csv = rows.map(r =>
    r.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")
  ).join("\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

function exportRosterCSV() {
  if (!rosterData) return;
  const periods = ["本季", "近14天", "近30天"];
  const activeIdx = [...document.querySelectorAll("#view-roster .tab-btn")]
    .findIndex(b => b.classList.contains("active"));
  const period = periods[activeIdx >= 0 ? activeIdx : 0];
  const cats = rosterData.cats;
  const rows = [["球員", "守位", "球隊", "狀態", ...cats]];
  rosterData.players.forEach(p => {
    const stats = p.stats[period] || {};
    rows.push([
      p.name, p.position, p.team, p.status,
      ...cats.map(c => {
        const v = stats[c];
        if (v == null) return "";
        return rosterData.avg_cats.includes(c) ? Number(v).toFixed(3) : Math.round(v);
      }),
    ]);
  });
  downloadCSV(`我的名單_${period}.csv`, rows);
}

function exportStandingsCSV() {
  if (!standingsData) return;
  const cats = standingsData.cats;
  const rows = [["排名", "球隊", "W", "L", "T", ...cats]];
  [...standingsData.teams].sort((a, b) => (a.rank || 99) - (b.rank || 99)).forEach(t => {
    rows.push([
      t.rank ?? "", t.name, t.wins, t.losses, t.ties,
      ...cats.map(c => {
        const v = t.stats[c];
        if (v == null) return "";
        return standingsData.avg_cats.includes(c) ? Number(v).toFixed(3) : Math.round(v);
      }),
    ]);
  });
  downloadCSV("聯盟排名.csv", rows);
}

// ── 工具 ─────────────────────────────────────────────────────────────────────
function showError(msg) {
  const el = document.getElementById("error-box");
  el.textContent = msg; el.style.display = "block";
}
function clearError() { document.getElementById("error-box").style.display = "none"; }

loadLeagues();
setupSearch("give-search", "give-dropdown", givePlayers, "give-chips");
setupSearch("get-search",  "get-dropdown",  getPlayers,  "get-chips");
setupAdvSearch();
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
    auth_url = (f"{AUTH_URL}?client_id={CLIENT_ID}"
                f"&redirect_uri={WEB_REDIRECT_URI}&response_type=code")
    return AUTH_HTML.replace("{auth_url}", auth_url)


@app.route("/api/token", methods=["POST"])
def api_token():
    body = request.get_json(force=True)
    redirect_url = body.get("url", "").strip()
    code = parse_qs(urlparse(redirect_url).query).get("code", [None])[0]
    if not code:
        return jsonify({"error": "網址中找不到 code，請確認貼上的是授權後的完整網址"}), 400
    resp = http.post(TOKEN_URL,
                     data={"grant_type": "authorization_code", "code": code,
                           "redirect_uri": WEB_REDIRECT_URI},
                     auth=(CLIENT_ID, CLIENT_SECRET))
    if resp.status_code != 200:
        return jsonify({"error": f"Token 取得失敗：{resp.text}"}), 400
    _store["token"] = resp.json()["access_token"]
    _store.pop("leagues", None)
    _store.pop("stat_maps", None)
    return jsonify({"ok": True})


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
    league_key   = body.get("league_key", "")
    give_players = body.get("give", [])
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


@app.route("/api/savant")
def api_savant():
    token = _store.get("token")
    if not token:
        return jsonify({"error": "未授權"}), 401
    name     = request.args.get("name",     "").strip()
    position = request.args.get("position", "").strip()
    if not name:
        return jsonify({"error": "缺少球員名稱"}), 400

    pitcher_pos = {"SP", "RP", "P"}
    player_type = "pitcher" if any(p in position.upper() for p in pitcher_pos) else "batter"

    try:
        mlbam_id = lookup_mlbam_id(name)
    except ImportError as e:
        return jsonify({"error": str(e)}), 500
    if not mlbam_id:
        return jsonify({"error": f"找不到「{name}」的 MLBAM ID，請確認英文拼寫"}), 404

    savant_data = fetch_savant_percentiles(mlbam_id, player_type)
    fg_data     = fetch_fangraphs_stats(name, player_type)

    return jsonify({
        "name": name, "player_type": player_type,
        "mlbam_id": mlbam_id,
        "savant": savant_data, "fangraphs": fg_data,
    })


@app.route("/api/my_roster")
def api_my_roster():
    token = _store.get("token")
    if not token:
        return jsonify({"error": "未授權"}), 401
    league_key = request.args.get("league_key", "").strip()
    if not league_key:
        return jsonify({"error": "缺少 league_key"}), 400

    my_teams = get_my_team_key(token)
    team_key = my_teams.get(league_key)
    if not team_key:
        return jsonify({"error": "找不到此聯盟中的球隊"}), 404

    stat_maps = _store.setdefault("stat_maps", {})
    if league_key not in stat_maps:
        stat_maps[league_key] = get_stat_map(token, league_key)
    stat_map = stat_maps[league_key]

    leagues = _store.get("leagues", [])
    league_id = next((str(lg.get("id", "")) for lg in leagues if lg["key"] == league_key), "")
    league_cfg = next((cfg for cfg in LEAGUES.values() if cfg["id"] == league_id), LEAGUES["1"])
    all_cats = league_cfg["batter_cats"] + league_cfg["pitcher_cats"]

    roster = get_team_roster(token, team_key)
    stat_types = {"本季": "season", "近14天": "last_week", "近30天": "last_month"}

    players_out = []
    for player in roster:
        player_stats = {}
        for period, stype in stat_types.items():
            player_stats[period] = fetch_player_stats(
                token, league_key, player["key"], stype, stat_map)
            time.sleep(0.2)
        players_out.append({
            "name": player["name"], "position": player["position"],
            "team": player["team"], "status": player["status"],
            "stats": player_stats,
        })

    avg_cats = [c for c in all_cats if c in AVG_CATS]
    return jsonify({"players": players_out, "cats": all_cats, "avg_cats": avg_cats})


@app.route("/api/standings")
def api_standings():
    token = _store.get("token")
    if not token:
        return jsonify({"error": "未授權"}), 401
    league_key = request.args.get("league_key", "").strip()
    if not league_key:
        return jsonify({"error": "缺少 league_key"}), 400

    stat_maps = _store.setdefault("stat_maps", {})
    if league_key not in stat_maps:
        stat_maps[league_key] = get_stat_map(token, league_key)
    stat_map = stat_maps[league_key]

    leagues = _store.get("leagues", [])
    league_id = next((str(lg.get("id", "")) for lg in leagues if lg["key"] == league_key), "")
    league_cfg = next((cfg for cfg in LEAGUES.values() if cfg["id"] == league_id), LEAGUES["1"])
    all_cats = league_cfg["batter_cats"] + league_cfg["pitcher_cats"]
    negative_cats = list(league_cfg["negative"])
    avg_cats = [c for c in all_cats if c in AVG_CATS]

    teams = get_standings(token, league_key, stat_map)
    return jsonify({
        "teams": teams, "cats": all_cats,
        "negative_cats": negative_cats, "avg_cats": avg_cats,
    })


@app.route("/api/team_roster")
def api_team_roster():
    token = _store.get("token")
    if not token:
        return jsonify({"error": "未授權"}), 401
    league_key = request.args.get("league_key", "").strip()
    team_key   = request.args.get("team_key",   "").strip()
    if not league_key or not team_key:
        return jsonify({"error": "缺少 league_key 或 team_key"}), 400

    stat_maps = _store.setdefault("stat_maps", {})
    if league_key not in stat_maps:
        stat_maps[league_key] = get_stat_map(token, league_key)
    stat_map = stat_maps[league_key]

    leagues = _store.get("leagues", [])
    league_id = next((str(lg.get("id", "")) for lg in leagues if lg["key"] == league_key), "")
    league_cfg = next((cfg for cfg in LEAGUES.values() if cfg["id"] == league_id), LEAGUES["1"])
    all_cats = league_cfg["batter_cats"] + league_cfg["pitcher_cats"]

    roster = get_team_roster(token, team_key)
    stat_types = {"本季": "season", "近14天": "last_week", "近30天": "last_month"}

    players_out = []
    for player in roster:
        player_stats = {}
        for period, stype in stat_types.items():
            player_stats[period] = fetch_player_stats(
                token, league_key, player["key"], stype, stat_map)
            time.sleep(0.2)
        players_out.append({
            "name": player["name"], "position": player["position"],
            "team": player["team"], "status": player["status"],
            "stats": player_stats,
        })

    avg_cats = [c for c in all_cats if c in AVG_CATS]
    return jsonify({"players": players_out, "cats": all_cats, "avg_cats": avg_cats})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not CLIENT_ID:
        print("⚠️  請先設定 YAHOO_CLIENT_ID 和 YAHOO_CLIENT_SECRET 環境變數")
        sys.exit(1)

    print("=" * 55)
    print("Yahoo Fantasy Baseball 交易分析器 - Web 介面")
    print("=" * 55)
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(host="localhost", port=5000, debug=False)
