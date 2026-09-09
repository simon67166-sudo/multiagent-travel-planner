# API 與團隊設定指南

## 目前本機模型設定（2026-09-09 核對）
- 平台：Paratera 並行智算雲。
- Base URL：`https://llmapi.paratera.com/v1/`。
- SDK 呼叫：`client.chat.completions.create(...)`，對應 `POST /v1/chat/completions`。
- Pro：`DeepSeek-V4-Pro-0813`；Flash：`DeepSeek-V4-Flash-0731`。
- 本機根目錄 `.env` 的 LLM_API_KEY 與原 E:/TravelAgent/.env 相同；未發現 orchestrator/.env。
- 以上只能確認此電腦的設定，不能推斷隊友電腦是否有自己的金鑰。
- `openai` 是相容協議的 Python SDK 名稱；目前模型請求送往 Paratera，並非 OpenAI 模型服務。
- 每個呼叫 llm_tool 的 Agent 共用同一個 client；model 參數決定模型，沒有為每個 Agent 配一把 Key。

模型文件（使用者提供的官方入口）：https://ai.paratera.com/document/llm/quickStart
本次工具讀取該頁逾時；API 地址、模型名稱以本機設定和成功呼叫確認。

## 外部資料接口
| 用途 | 目前服務及接口 | 文件 |
|---|---|---|
| 地點定位 | 高德 GET /v3/place/text | https://lbs.amap.com/api/webservice/guide/api/search/ |
| 周邊地點 | 高德 GET /v3/place/around | https://lbs.amap.com/api/webservice/guide/api/search/ |
| 步行/駕車路線 | 高德 GET /v3/direction/walking、/v3/direction/driving | https://lbs.amap.com/api/webservice/guide/api/direction |
| 地圖畫面 | 高德 JS API 2.0（AMap.Map/Marker/Polyline） | https://lbs.amap.com/api/jsapi-v2/summary |
| 天氣數值預報 | GET https://api.open-meteo.com/v1/forecast | https://open-meteo.com/en/docs |

港澳附近遊已改成只用高德（地點搜尋/周邊地點/路線都是），不再有 OSM/Photon/Overpass/OSRM/Leaflet 備援；
天氣仍用 Open-Meteo——高德天氣 API 只有城市級逐日預報，沒有這裡需要的按出遊時段小時級預報。
公共接口目前不使用你的 LLM 金鑰，有服務各自的使用限制，並非無限量服務。程式內有快取和限速。
天氣標示為 Open-Meteo 數值預報；香港天文台、澳門氣象局是官方查核連結，尚未直接抓取官方警告 API。
Google Maps 公交連結是交由地圖服務查詢的入口，沒有抓取公交即時班次、票價或站台。
小紅書尚未接入；目前不能宣稱查詢了小紅書。後續須另行確定授權資料來源或匯入使用者提供的內容及來源連結。

## 高德設定（必填，非可選）
`AMAP_KEY`（Web服務，查資料）、`AMAP_JS_KEY`（Web端 JS API，畫地圖）、`AMAP_JS_SECURITY_CODE`（安全密鑰）
三個都要配進 `.env`，否則行程地圖、港澳附近遊都無法正常運作（會優雅降級成文字/錯誤提示，不會整體崩潰）。
所有座標統一是高德 GCJ-02，天氣查詢用城市中心點的固定 WGS84 坐標（跟地圖坐標分開，不混用坐標系）。

## 本機 HTTP 接口
瀏覽器 cookie `travel_session` 保持同一會話。JSON 回傳，輸入錯誤 HTTP400，模型/資料服務失敗通常 HTTP502。

| 方法與路径 | 作用 |
|---|---|
| GET / | 網頁 |
| GET /session | 聊天、待選卡片、已保存 nearby_plan |
| POST /chat | 輸入 `{ "message": "請安排香港尖沙咀附近3小時遊覽" }`，由編排器選能力 |
| GET /trip | 原有行程面板資料，另附 nearby_plan（如有） |
| POST /widget-response | `{ "widget": "hotel_picker", "selected": [...] }`，必須是最新候選 |
| POST /nearby-plan | 按結構化需求建立附近遊建議 |
| POST /nearby-weather | `{}`，更新目前附近遊時段天氣 |

POST /nearby-plan 範例（日期自行改成出遊日）：
```json
{
  "city": "澳門",
  "location": "大三巴",
  "date": "2026-09-10",
  "start_time": "10:00",
  "hours": 3,
  "mode": "walking",
  "members": [
    {"name": "A", "preferences": "喜歡文化和拍照"},
    {"name": "B", "preferences": "希望少走路"}
  ]
}
```
回傳 `{ "chat_reply": "...", "nearby_plan": {...} }`。nearby_plan 包含 request、origin、stops、legs、weather、reminders、currency、crs。
每筆 POI 和成功路線有來源連結與查詢時間；缺少價格/營業時間則保留未知。
沒有固定的成員數量限制；仍受 HTTP 請求大小、模型上下文及效能限制。
目前成員偏好用於 LLM 選點參考，尚未完成多人硬限制最佳化。
時間表為建議：路程用查詢估算、停留時間用規則；並未完成營業時間與訂位可行性驗證。
附近遊保存為獨立 nearby_plan，不覆寫隊友舊 trip_plan，舊行程工具不會直接修改這份建議。

## 可以提交 GitHub 的內容
可以提交：程式、tests、requirements.txt、README、這份文件、`.env.example`。
不要提交：`.env`、真實 API Key、聊天 SQLite、個人行程/位置資料和原始供應商回應快照。
`.gitignore` 已忽略 .env 和 orchestrator/data/。忽略規則不會自動移除過去已追蹤的檔案。
隊友 clone 後自行建 .env，填自己的金鑰。不要把含金鑰的 curl、截圖或除錯紀錄貼進 issue。
本輪尚未自動 commit 或 push；先完成本機驗收，再選擇性提交。
