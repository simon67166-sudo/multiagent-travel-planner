# 2026-09-10 隊友版本整合

## 基礎
- 採用 origin/amap-only-integration 的 5ba5bc1：地點、路線和地圖只使用高德，移除 OSM/Leaflet 備援；天氣保留 Open-Meteo。
- 合入 origin/master 的 b74cf67：機場地理編碼不再被行程城市限定。
- 所有修改及推送只在 codex/integrate-travel-guardian；不更新隊友分支。

## 保留的相容能力
- 共用 Paratera Pro/Flash 設定與完整 tool_calls 介面。
- SQLite 會話隔離、重啟恢復及失敗回滾。
- 模擬餐廳硬限制工具與測試。
- 港澳附近遊、可增減同行者、天氣提醒和交通指引。
- 隊友的景點/酒店/航班卡片、地圖與日程時間線。

## 整合修正
- /trip 和卡片選擇使用會話城市，避免香港行程套用預設澳門。
- 舊 WGS84 行程保留資料，但不畫入高德；提示重新生成 GCJ-02 路線。
- 新增機場不限定城市、本地酒店使用城市、高德來源及 HTTP 會話城市的回歸測試。

## 運行
本機 .env 必須配置 AMAP_KEY、AMAP_JS_KEY、AMAP_JS_SECURITY_CODE。金鑰不從 GitHub 分支取得，也不提交。
重啟 python orchestrator/server.py，重新整理網頁；舊 OSM 附近遊需重新生成。
29 項離線測試通過；本次不聲稱完成真實高德港澳或瀏覽器端到端驗收。
