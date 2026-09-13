# Travel Guardian 團隊整合版

第一輪：保留 Flask 聊天、地圖、時間線和選擇卡片，接入餐廳工具與 SQLite 會話記憶。

## 啟動（PowerShell）
```powershell
conda activate travelagent
cd E:\multiagent-travel-planner
python -m pip install -r requirements.txt
python orchestrator/server.py
```
瀏覽器開啟 http://127.0.0.1:5000 。目前為單進程本地演示，不是帳號登入服務。

## 設定
根目錄 `.env` 使用 `.env.example` 的欄位。已有 `.env` 時不要覆蓋。
讀取優先序：程序環境 > 根目錄 `.env` > `orchestrator/.env`（相同名稱）。
API Key 優先採用 `LLM_API_KEY`，沒有時相容 `PARATERA_API_KEY`。
Pro 用於編排，Flash 用於餐廳工具；兩者模型名稱皆可配置。
高德 Key 沿用原有欄位；沒有高德 Key 仍可測試餐廳聊天。
`.env`、Chroma 資料及 SQLite 資料庫均不提交 Git。

## 第一輪驗收
依次在同一個瀏覽器輸入：
1. 「請查詢澳門模擬餐廳，人均80澳門元，有人不能吃辣，排隊不超過20分鐘。」預期只有演示餐廳 A 符合。
2. 「預算改成60澳門元，其他要求不變。」預期沒有符合條件的餐廳，仍保留不辣與20分鐘上限。
3. 重新整理頁面，應恢復聊天。停止並重啟伺服器，再問「我剛才的飲食要求是什麼？」
4. 用另一個瀏覽器或無痕視窗開啟，應得到獨立對話。一般同瀏覽器分頁共享 cookie。
5. 對已有景點詢問天氣，行程不應被刪除。

餐廳資料是 **虛構澳門餐廳／MOP**，不會加入杭州地圖；資料不是即時店家資訊。
多人結構化偏好及自動事件重規劃仍屬第二輪。
目前自然語言需求由模型依完整歷史理解，Python 確定性檢查工具參數及篩選結果；並非已完成多人最佳化。
異常模組只提供未驗證提案，尚無接受重規劃提案的執行接口。
酒店／機票選擇只寫入本地日程，不代表已向供應商下單。

## 會話和接口
- `GET /session`：恢復可顯示的聊天與最新卡片，工具推理訊息不回傳前端。
- `POST /chat`：保持 `{message: ...}`，返回原有 `chat_reply/community_panel/map_panel/widgets`，新增 `restaurant_evidence`。
- `GET /trip`、`POST /widget-response` 沿用原有格式；選項必須來自該會話最新未使用的卡片。
- cookie `travel_session` 是隨機識別碼；SQLite 保存完整工具歷史、persona、trip_plan 和待選卡片。
- 預設資料庫 `orchestrator/data/sessions.db`。同一進程序列化寫入，模型失敗不保存部分狀態。
- 目前不截斷歷史，長對話會增加 token 用量；尚未提供跨裝置帳號同步。
- 舊專案及其舊 SQLite 不變；新網頁從新會話開始，不自動匯入舊 CLI 對話。

## 測試
```powershell
python -m unittest discover -s tests -v
python -m pip check
```
離線測試 mock 模型和外部查詢，不花費 API 額度；真實模型驗收必須另行執行。

## 版本控制
目前在 `codex/integrate-travel-guardian` 開發。先驗收，再提交、推送及開 Pull Request；不要直接覆蓋 master。

## 本輪實際驗證紀錄（2026-09-09）
- 19 項離線測試通過。
- Paratera 真實兩輪 HTTP 聊天驗收通過：80 MOP 選 A；改60 MOP後無符合結果，保留不辣及20分鐘上限。
- 以新 SessionStore 重新讀取同一 SQLite，恢復兩輪對話。
- JavaScript 語法及 pip 依賴一致性檢查通過。
- 已完成本機瀏覽器演示驗收；若要顯示真實高德地圖，仍須在本機配置 Web Service Key、JS Key 與安全密鑰。本專案不執行真實預訂。

## 港澳附近遊（第二輪）
停止舊伺服器並重新 `python orchestrator/server.py`，重新整理網頁。
左側表單選澳門/香港、起點、日期時間、時長及交通；同行者可自由增減。
建議演示起點：澳門「大三巴」、香港「尖沙咀」。也可在對話要求規劃港澳附近遊。
結果有真實候選地點、建議停留時間、路網估算、地圖路線方向、交通指引和天氣預報。
頁面開著且可見時每10分鐘更新天氣；不在背景持續執行，不自動更改行程。
地點搜尋、周邊地點、路線一律使用高德（GCJ-02），需要 AMAP_KEY/AMAP_JS_KEY/AMAP_JS_SECURITY_CODE；
天氣仍用 Open-Meteo（高德天氣 API 只有城市級逐日預報，沒有這裡需要的按出遊時段小時級預報）。
公交僅提供查詢入口，具體站台/班次/票價尚未取得。小紅書尚未接入。
接口和配置詳見 [API 指南](docs/api-guide.md)。
真實接口驗收：澳門大三巴和香港尖沙咀皆曾取得周邊地點、3 段步行路線和天氣。2026-09-13 另完成本機雙瀏覽器演示驗收；真實地圖畫面仍需本機高德 JS 憑證才能重現。
