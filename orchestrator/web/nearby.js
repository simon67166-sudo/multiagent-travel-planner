/* Nearby UI: render external content as text; map rendering only uses 高德 AMap (GCJ-02). */
let currentNearbyPlan = null;
let nearbyMap = null;
let nearbyBusy = false;
const nearbyById = id => document.getElementById(id);
function nearbyText(parent, tag, text) {
  const el = document.createElement(tag); el.textContent = text == null ? "待確認" : typeof text === "object" ? "詳細資料待確認" : String(text); parent.appendChild(el); return el;
}
function nearbyLink(parent, text, url) {
  if (!url) return;
  try { if (!["https:", "http:"].includes(new URL(url).protocol)) return; } catch (_) { return; }
  const a = nearbyText(parent, "a", text); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
}
nearbyById("nearby-city").onchange = () => {
  nearbyById("nearby-location").value = nearbyById("nearby-city").value === "香港" ? "尖沙咀" : "大三巴";
};
const nearbyTomorrow = new Date(); nearbyTomorrow.setDate(nearbyTomorrow.getDate() + 1);
nearbyById("nearby-date").value = `${nearbyTomorrow.getFullYear()}-${String(nearbyTomorrow.getMonth()+1).padStart(2,"0")}-${String(nearbyTomorrow.getDate()).padStart(2,"0")}`;


function drawNearbyMap(plan) {
  const container = nearbyById("nearby-map"); container.style.display = "block";
  nearbyById("map-container").style.display = "none";
  nearbyById("map-legend").style.display = "none";
  if (plan.demo || plan.mode === "demo" || plan.data_kind === "demo" || !plan.origin) {
    if (nearbyMap) { nearbyMap.destroy(); nearbyMap=null; }
    container.textContent=plan.demo || plan.mode === "demo" || plan.data_kind === "demo" ? "演示地點為虛構，不提供真實導航路線。" : "起點座標未提供，暫時無法顯示路線。";
    return;
  }
  if (plan.crs !== "GCJ02") {
    if (nearbyMap) { nearbyMap.destroy(); nearbyMap = null; }
    container.textContent = "這份行程使用舊版地圖座標，請按『生成附近遊攻略』重新查詢高德路線。原對話與行程記錄仍保留。";
    return;
  }
  const validPoint = p => p && Number.isFinite(p.lng) && Number.isFinite(p.lat) && Math.abs(p.lng) <= 180 && Math.abs(p.lat) <= 90;
  if (!validPoint(plan.origin)) { if (nearbyMap) { nearbyMap.destroy(); nearbyMap = null; } container.textContent = "起點座標無效，請重新查詢。"; return; }
  const points = [plan.origin, ...plan.stops].filter(validPoint);
  if (typeof AMap === "undefined") {
    container.textContent = "地圖元件未載入（AMAP_JS_KEY/安全密鑰可能未設置）；請使用下面的分段導航連結。";
    return;
  }
  if (!nearbyMap) nearbyMap = new AMap.Map(container,{zoom:15,center:[plan.origin.lng,plan.origin.lat]});
  nearbyMap.clearMap();
  points.forEach((p,i) => new AMap.Marker({position:[p.lng,p.lat],title:`${i}. ${p.name}`,map:nearbyMap}));
  (plan.legs || []).forEach(leg => { if (leg.available && !leg.demo && leg.data_kind !== "demo" && (!leg.crs || leg.crs === "GCJ02") && Array.isArray(leg.coordinates) && leg.coordinates.length >= 2 && leg.coordinates.every(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && Math.abs(p[0]) <= 180 && Math.abs(p[1]) <= 90)) new AMap.Polyline({path:leg.coordinates,showDir:true,strokeColor:"#0b7054",strokeWeight:5,map:nearbyMap}); });
  nearbyMap.setFitView();
}

function renderNearbyWeather(weather = {}) {
  const box = nearbyById("nearby-weather"); if (!box) return; box.replaceChildren();
  nearbyText(box,"h3","出遊時段天氣與提醒");
  if (weather.available) nearbyText(box,"p",`氣溫 ${weather.temperature_min ?? "未知"}–${weather.temperature_max ?? "未知"} °C；最高降雨機率 ${weather.max_rain_probability ?? "未知"}%`);
  else nearbyText(box,"p","目前沒有可用的該時段預報。");
  (weather.reminders || []).forEach(t => nearbyText(box,"p",t));
  nearbyText(box,"small",`${weather.source || ""} · 查詢時間 ${weather.fetched_at || "未取得"}`);
  nearbyText(box,"br",""); nearbyLink(box,"預報來源",weather.source_url); nearbyLink(box,"官方天氣及警告",weather.official_url);
  const b=nearbyText(box,"button","更新天氣"); b.type="button"; b.onclick=refreshNearbyWeather;
}

function renderNearby(plan) {
  if (!plan || typeof plan !== "object") return;
  plan = {...plan, request: plan.request || {}, stops: Array.isArray(plan.stops) ? plan.stops : [], legs: Array.isArray(plan.legs) ? plan.legs : [], reminders: Array.isArray(plan.reminders) ? plan.reminders : []};
  currentNearbyPlan = plan;
  const output=nearbyById("nearby-output"); output.replaceChildren();
  const intro=nearbyText(output,"section",""); intro.className="nearby-card";
  nearbyText(intro,"h3",`${plan.request.city || plan.city || "城市待確認"} · ${plan.request.date || "日期待確認"} 附近遊建議`);
  nearbyText(intro,"p",`定位：${plan.origin?.name || "演示情境，未提供真實座標"}。請確認這是你要出發的位置。`);
  nearbyText(intro,"p",`採用團隊成員已儲存的偏好 · ${plan.currency || "幣別待確認"} · ${plan.request.hours == null ? "時長待確認" : plan.request.hours + " 小時"}`);
  nearbyLink(intro,"起點資料",plan.origin?.source_url);
  plan.reminders.forEach(t => nearbyText(intro,"p",t));
  const weather=nearbyText(output,"section",""); weather.id="nearby-weather"; weather.className="nearby-card";
  renderNearbyWeather(plan.weather || {});
  plan.stops.forEach((stop,i) => {
    const card=nearbyText(output,"section",""); card.className="nearby-card";
    nearbyText(card,"h3",`${i+1}. ${stop.arrival_time || "時間待確認"}–${stop.end_time || "待確認"} ${stop.name}`);
    nearbyText(card,"p",stop.visit_note);
    nearbyText(card,"p",`地址：${stop.address || "來源未提供"}；價格：${typeof stop.price === "object" && stop.price ? `${stop.price.currency || "幣別待確認"} ${stop.price.amount ?? "金額待確認"}` : stop.price ?? "未知"}；來源營業時間：${stop.opening_hours || "未知"}`);
    nearbyLink(card,"地點資料",stop.source_url);
    nearbyText(card,"small",` ${stop.source || ""} · ${stop.fetched_at || ""}`);
    const leg=plan.legs[i]; if (!leg) return;
    nearbyText(card,"p",`${leg.from_name} → ${leg.to_name}：${leg.available ? `約 ${leg.duration_min} 分鐘／${leg.distance_m} 米（路網估算）` : "交通時間待查"}`);
    const detail=nearbyText(card,"details",""); nearbyText(detail,"summary","查看分段方向");
    const list=nearbyText(detail,"ol",""); (leg.steps || []).forEach(step => nearbyText(list,"li",step));
    nearbyLink(card,"開啟導航",leg.navigation_url); nearbyLink(card,"查公交／轉乘",leg.transit_url); nearbyLink(card,"路線資料來源",leg.source_url);
  });
  const help=nearbyText(output,"section",""); help.className="nearby-card"; nearbyText(help,"h3","交通使用方式");
  (plan.transport_help || ["演示未提供真實交通資料。"]).forEach(t => nearbyText(help,"p",t));
  drawNearbyMap(plan);
}
function restoreNearby(plan) {
  if (!plan || typeof plan !== "object") return;
  const r=plan.request || {};
  for (const [id,key] of [["city","city"],["location","location"],["date","date"],["time","start_time"],["hours","hours"],["mode","mode"]]) if (r[key] != null) nearbyById("nearby-"+id).value=r[key];
  renderNearby(plan);
}
async function refreshNearbyWeather() {
  if (!currentNearbyPlan || nearbyBusy) return;
  nearbyBusy=true;
  try {
    const response=await fetch("/nearby-weather",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    const data=await response.json(); if (!response.ok) throw new Error(window.TravelGuardian?.errorMessage(response.status, data) || "天氣查詢暫時失敗，請稍後重試。");
    currentNearbyPlan.weather=data.weather; renderNearbyWeather(data.weather || {});
  } catch (e) { nearbyById("nearby-status").textContent="天氣更新失敗："+e.message; }
  finally { nearbyBusy=false; }
}
nearbyById("nearby-form").onsubmit=async event => {
  event.preventDefault(); if (nearbyBusy) return;
  const data={city:nearbyById("nearby-city").value,location:nearbyById("nearby-location").value,date:nearbyById("nearby-date").value,start_time:nearbyById("nearby-time").value,hours:Number(nearbyById("nearby-hours").value),mode:nearbyById("nearby-mode").value};
  nearbyBusy=true; nearbyById("build-nearby").disabled=true;
  nearbyById("nearby-status").textContent="正在查詢地點、路線與天氣，通常需要數十秒…";
  try {
    const response=await fetch("/nearby-plan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    const result=await response.json(); if (!response.ok) throw new Error(window.TravelGuardian?.errorMessage(response.status, result) || "規劃失敗，請核對輸入後重試。");
    if (window.TravelGuardian) await window.TravelGuardian.showResult(result);
    addMessage("user",`規劃 ${data.city} ${data.location} 附近遊`); addMessage("assistant",result.chat_reply);
    nearbyById("nearby-status").textContent="提案已建立，請在團隊面板比較並接受。正式行程尚未修改。";
  } catch (e) { nearbyById("nearby-status").textContent=e.message; }
  finally { nearbyBusy=false; nearbyById("build-nearby").disabled=false; }
};
// Guardian owns the single visible-page event polling loop.
