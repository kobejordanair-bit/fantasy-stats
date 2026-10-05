# Yahoo NBA 年度實際資料

擷取指定 Yahoo NBA 聯盟／球隊的歷史球季，產生網站可匯入的 `season.json`、10 份 CSV、10 工作表 Excel，以及不需網路的 HTML 戰情室。沒有登入或沒有真實來源時，不建立冠軍或範例成績。

此版本承接 `claude/review-document-analysis-MjABR` 的 NBA 功能，已核對遠端最新提交 `946cc1df1523aa72159ac9aea01fbd437c5e4cde`。保留原有 10 份 CSV、10 分頁 Excel、交易紀錄與比較、補人各次在隊期間、球員累積／週均、所有受支援聯盟類別、類別領先者與 MVP。Excel 改由年度結構化資料直接產生，百分比保留為可計算數值；工作簿放在每次獨立的年度目錄。其他 baseball 分支不屬於這次 NBA 重寫。

## 安裝

需要 Python 3.11 或更新版本。在這個目錄執行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在本機 `.env` 設定 `YAHOO_CLIENT_ID`、`YAHOO_CLIENT_SECRET`，並讓 `YAHOO_REDIRECT_URI` 與 Yahoo Developer App 設定完全一致。預設 callback 是 `https://localhost:8080`，授權後頁面可能無法連線；在終端貼回完整 callback URL 即可，不需要在本機架 HTTPS 伺服器。

```powershell
.\.venv\Scripts\python.exe fetch_stats.py --check-config
.\.venv\Scripts\python.exe fetch_stats.py login
.\.venv\Scripts\python.exe fetch_stats.py --list-leagues
.\.venv\Scripts\python.exe fetch_stats.py teams --league 000.l.000
.\.venv\Scripts\python.exe fetch_stats.py collect --league 000.l.000 --team 000.l.000.t.1
```

上方 key 是格式示例，請替換成自己的真實 key。`--check-config` 不連線，也不顯示密鑰。只有明確執行 `login` 才會啟動新授權；其他命令只使用既有 token／refresh token。若帳號沒有歷史聯盟，請使用冠軍隊所屬帳號，勿把空白資料當作該球季。

Token 預設存於家目錄 `.fantasy-stats/yahoo-token.json`，不放進此 repository。可在命令前使用 `--token-file` 指定其他本機路徑。不要提交 `.env`、token、`reports/` 或私有原始回應；Git 已排除預設位置。OAuth 使用隨機 state、完整 callback 核對、timeout、有限重試及 401 更新，不打印 API 回應內容或 token。

由本機操作工具接收授權回傳時，可省去人工貼網址：

```powershell
.\.venv\Scripts\python.exe fetch_stats.py --env-file C:\private\yahoo.env login --no-browser --callback-file C:\private\new-random.callback --callback-timeout 1800
```

請使用 repository 外的私人目錄與全新的隨機檔名，啟動前該 callback 檔不得存在。操作工具在 Yahoo 重導後，把完整 callback URL 以 UTF-8 寫入同目錄暫存檔，再原子改名為指定檔；程式只接收一次，先移除檔案，再檢查 state、redirect、單一授權碼並交換 token。等待上限為 1800 秒，不需啟動 HTTPS 伺服器或更改憑證信任。網址與授權碼屬私有資料，不應寫入一般紀錄或提交 Git；逾時後應停止交付該檔並重新登入。

## 年度資料與報表

每次執行都建立獨立目錄：

```text
reports/YYYY-YY/league-key/team-key/UTC時間-隨機值/
  season.json              網站 schemaVersion 1 年度資料
  fantasy_season.xlsx      10 個工作表
  fantasy_dashboard.html   離線戰情室、排序表格、球隊／球員走勢、MVP、類別領先者
  *.csv                    10 份可分析的報表
  collection.json          每日名單與 selected_position、缺漏日期
  source-manifest.json     API path、原始檔與 SHA-256
  raw/                     僅 Yahoo Fantasy 回應；不含 OAuth 回應或密鑰
  run-status.json          complete 或 incomplete
```

網站「歷年戰績」請匯入 `season.json`。新 CSV 保留代碼、狀態、分母等稽核欄位，供 Excel／資料分析使用；不要把它們當作旧版 CSV 再匯入。JSON 是完整、不丟失欄位的交換格式。

10 個 Excel 工作表為：球員季總、球員週均、球隊週次、球員逐週、對戰結果、類別戰績、交易紀錄、補人紀錄、交易比較、補人貢獻。數據是數值，命中率以 0–1 儲存並套用 Excel 百分比格式；未知值為空白。日期、篩選、凍結窗格均保留。純文字即使以 `=` 開頭仍為文字，CSV 同時處理公式注入字首。匯入舊資料重建報表時，來源與口徑照原 JSON 標示，不升格為官方或已驗證先發資料。

HTML 不載入 CDN、圖片、外部字型或第三方 JavaScript，不需登入即可離線開啟，因此仍應保存在私人位置。資料只透過 `textContent` 建立畫面；嵌入 JSON 的 `<`、`>`、`&`、Unicode 行分隔符先轉義。

已匯出的 JSON 可完全離線重建報表，仍會建立新的目錄：

```powershell
.\.venv\Scripts\python.exe fetch_stats.py export path\to\season.json
```

長球季擷取若中斷，可由前次輸出目錄續跑，仍會產生新的年度快照：

```powershell
.\.venv\Scripts\python.exe fetch_stats.py collect --league 000.l.000 --team 000.l.000.t.1 --resume path\to\previous-run
```

只有經 SHA-256 驗證的成功 API 回應可重用；未取得的回應會重新擷取，損壞的快取或來源清單會停止，避免採信不一致的證據。續跑先驗證目前授權帳號與聯盟／球隊／球季，帳號不同或前次缺少身份綁定時會停止。原始快照保留，失效授權會立即中止，不會繼續用空資料完成球季。每次成功回應即寫入 `source-manifest.jsonl`，即使尚未產生 Excel 也可保留已完成的擷取證據。

## 統計口徑

- **球隊成績**直接取 Yahoo scoreboard 的 `team_stats`，不把全名單 NBA 統計相加冒充實際計分。只有官方 `postevent` 才列為已結算，勝負／類別勝者使用官方紀錄，缺漏保持未知。最終名次僅在 Yahoo `is_finished=1` 時採官方 standings；名次 1 才標冠軍。
- **球員貢獻**逐日查名單與當日球員 stats，要求 roster 和 selected_position 的 date 一致，排除 BN／IL／IL+／IR／NA。只計完整結束週次；未驗證日期或讀取失敗時，受影響整週的球員總數不宣稱完整。`collection.json` 保留板凳及每日證據。這與官方 scoreboard 可能因 Yahoo 統計修正、出賽限制而不同，兩者分開保存。
- **百分比**由命中／出手加權；零出手不污染其他有效樣本。缺分母不平均每週百分比，命中大於出手視為不合法。
- **聯盟類別**不把 Yahoo `is_only_display_stat` 輔助欄位算成得分類別。平台目前支援 FG%、FT%、3PT% 的加權計算；A/T 等其他比例類別會明確停止，避免錯誤相加。
- **交易比較**雙方都查相同日期窗內的完整 NBA 表現，不限持有球隊；從交易次日到擷取區間最後完整週。任何來源缺漏都保持未知，不把實際先發與全聯盟 NBA 表現混比。`--skip-trade-stats` 可先保存交易紀錄，略過較耗時的逐日比較。
- **補人貢獻**僅計已驗證每日先發，從取得次日到下一次送出／釋出／重新取得之前。交易日與離隊當日不歸因，避免當日生效時間不明。重複撿入分開計算，不重複加上命中數。
- **日期**使用 Yahoo game_weeks 日曆；交易 timestamp 轉為 `America/Los_Angeles` 的日期。必要時透過 `--timezone` 指定明確的資料口徑，不使用電腦所在時區猜測。
- **MVP**為隊內計數類別的累積 Z-score，負向類別反轉；不納入百分比。任一類別缺漏不提供綜合分。類別領先者保留原功能：負向類別列「最多（負向類別）」而非價值最佳；比例未加最低出手門檻，須一併檢查出手數。

平台 schema 1 的每季 1.2 MB、200 球員、3,000 球員週次等上限會在匯出前驗證。超出時停止、保留原始資料，不截斷球季。部分 API 失敗會在年度 warnings、missingWeeks、collection.json 中標示；`complete` 代表本次流程已結束，不代表 Yahoo 缺漏已補齊。

## 測試

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

所有測試資料都明確標為 SYNTHETIC，與真實冠軍隊無關。涵蓋資料口徑分離、日期覆蓋、零出手、缺漏、重複補人、交易公平期間、OAuth state／更新、公式／HTML 注入、Excel 儲存型別、10 工作表與不覆寫。

API 參考：[Yahoo Fantasy API](https://sports.yahoo.com/developer/docs/)、[Yahoo OAuth Authorization Code Flow](https://developer.yahoo.com/oauth2/guide/flows_authcode/)。尚未取得使用者帳號下的實際冠軍年度資料；真實 API 的帳號權限、歷史日期可用性及聯盟實際格式需完成授權後驗證。
