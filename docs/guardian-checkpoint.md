# Travel Guardian 中間版本（2026-09-11）

使用者要求：先在額度用完前封存可運作的中間版本，額度刷新後繼續；**原定 15 技能最終範圍不變**。

## 如何啟動與驗收

```powershell
conda activate travelagent
cd E:\multiagent-travel-planner
python orchestrator/server.py
```

瀏覽 http://127.0.0.1:5000/ 。若已有舊伺服器，先在原終端 Ctrl+C，再啟動；Flask debug 關閉不會自動載入修改。

1. 上方「我的偏好」填自己的飲食限制、整段行程預算/幣別、步行上限和興趣。
2. 選澳門或香港，按「填入此城市的演示情境」，再「開始規劃」。演示無需地圖或模型 Key。
3. 在提案比較前後行程。只有團主接受後，正式行程版本才更新；拒絕不變更。
4. 產生邀請連結，以另一個瀏覽器／無痕視窗加入，驗證成員只能改自己的偏好。
5. 「檢查真實事件」只查真實來源。演示事件可用 POST /events/check {"demo":true} 測試；本版 UI 尚未提供演示事件播放控制。

局域網測試（兩台裝置須同一網路，防火牆需允許此開發服務）：

```powershell
$env:TRAVEL_HOST='0.0.0.0'
$env:PUBLIC_BASE_URL='http://你的電腦局域網IP:5000'
python orchestrator/server.py
```

邀請網址來自 PUBLIC_BASE_URL 或請求網址，不寫死 localhost。普通 HTTP 局域網的剪貼簿 API 可能不可用；邀請連結目前可從 /team/invite 回覆取得，後續補手動複製欄位。

## 已實作並驗證

- SQLite 團隊、成員、邀請雜湊、行程版本、事件、提案；正式提交使用 BEGIN IMMEDIATE 及版本/團隊 revision 雙重檢查。
- 團隊加入、自己偏好更新、移除成員、撤銷邀請、提案接受/拒絕 HTTP；跨站來源檢查。
- 舊會話遷移，舊 WGS84 記錄保留；nearby 與 legacy trip 在同一版本中投影。
- /chat、/nearby-plan、/widget-response 改走提案；舊餐廳工具及酒店卡片回歸保留。
- 15 技能可執行基礎流程、5 個專業角色、硬限制篩選、平均/最低滿意度排序、完整費用及步行檢查。
- Pro 選技能、Flash 抽取附近遊需求。演示及單元測試不需模型呼叫。
- 高德公交/步行/駕車、城市天氣適配器；Open-Meteo 逐時預報仍保留。來源失敗不偽造資料。
- 結構化內容匯入與 ID 關聯；真實／整理／演示來源分離。
- 團隊、偏好、技能結果、來源、提案比較、頁面可見時 10 分鐘事件檢查 UI。
- 澳門／香港虛構演示資料；不把虛構地點畫成真實導航路線。

驗證：`python -m unittest discover -s tests -q`，**87 tests passed**（此 checkpoint）。三個前端腳本另以 Node --check 驗證。測試主要使用模擬供應商；未代表已完成真實高德/Paratera/跨裝置端到端驗收。

## 後續必須完成（不要把中間版當最終交付）

1. 完整瀏覽器驗收：兩個獨立成員、填偏好、接受/拒絕、刷新、重啟，確保 UI 同步與錯誤訊息。
2. 真實服務驗收與來源時效：高德 Web Service Key、JS Key、安全碼；港澳公交覆蓋逐項確認；匯入內容的有效期限完整傳遞。不要提交 .env。
3. 演示腳本／事件播放器：餐廳排隊變化、天氣替代、接續文化拍照；加入實際可驗證的港澳公開內容與真實地圖示範。
4. 技能深度：社交話術目前通用模板；伴手禮收禮人個性化、機位角度/時段、博物館故事、DIY材料、Citywalk故事仍需優質來源及更細的輸入/輸出。未知資料保持未知。
5. 動態重排後，目前時間及路線重設待確認；補完整重新查路線及時段可行性，不只調整順序。
6. 真實聊天上下文與任務混合回歸：多輪偏好、酒店/機票與附近遊協作、Pro故障降級、Flash資訊表達。
7. 公共UI打磨：邀請手動複製、清楚的400/409錯誤、即時成員同步、來源展示、跨日期行程時間檢查。
8. 擴充最終驗收矩陣、獨立程式碼審查與安全/金鑰檢查。舊編排 fallback 尚保留 nearby 早返，新HTTP路徑已走 Guardian；後續統一CLI路徑。

XHS、FlyAI、酒店與機票的真實供應商仍未接通。既有卡片是演示候選，不會下單，也不宣稱有真實庫存。

## 接續入口

核心：guardian_store.py → guardian_http.py → guardian_service.py → guardian_skills.py / guardian_sources.py。
介面：web/guardian.js、web/guardian.css，既有 index.html/nearby.js 仍保留高德架構。
先讀本文件，再跑完整測試，沿 codex/integrate-travel-guardian 繼續，不修改隊友分支。
