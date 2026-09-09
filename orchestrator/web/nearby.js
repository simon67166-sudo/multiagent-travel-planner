/* Nearby UI: render external content as text; map rendering only uses 高德 AMap (GCJ-02). */
let currentNearbyPlan = null;
let nearbyMap = null;
let nearbyBusy = false;
const nearbyById = id => document.getElementById(id);
function nearbyText(parent, tag, text) {
  const el = document.createElement(tag); el.textContent = text; parent.appendChild(el); return el;
}
function nearbyLink(parent, text, url) {
  if (!url) return;
  try { if (!["https:", "http:"].includes(new URL(url).protocol)) return; } catch (_) { return; }
  const a = nearbyText(parent, "a", text); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
}
function addNearbyMember(name = "", preferences = "") {
  const row = document.createElement("div"); row.className = "member-row";
  const n = document.createElement("input"); n.placeholder = "名字"; n.value = name; n.required = true;
  const pref = document.createElement("input"); pref.placeholder = "偏好／飲食／步行／預算需求"; pref.value = preferences;
  const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "移除"; remove.onclick = () => row.remove();
  row.append(n, pref, remove); nearbyById("nearby-members").appendChild(row);
}
nearbyById("add-member").onclick = () => addNearbyMember();
nearbyById("nearby-city").onchange = () => {
  nearbyById("nearby-location").value = nearbyById("nearby-city").value === "香港" ? "尖沙咀" : "大三巴";
};
const nearbyTomorrow = new Date(); nearbyTomorrow.setDate(nearbyTomorrow.getDate() + 1);
nearbyById("nearby-date").value = `${nearbyTomorrow.getFullYear()}-${String(nearbyTomorrow.getMonth()+1).padStart(2,"0")}-${String(nearbyTomorrow.getDate()).padStart(2,"0")}`;
addNearbyMember("我", "喜歡散步和文化景點");

function drawNearbyMap(plan) {
  const container = nearbyById("nearby-map"); container.style.display = "block";
  nearbyById("map-container").style.display = "none";
  nearbyById("map-legend").style.display = "none";
  const points = [plan.origin, ...plan.stops];
  if (typeof AMap === "undefined") {
    container.textContent = "地圖元件未載入（AMAP_JS_KEY/安全密鑰可能未設置）；請使用下面的分段導航連結。";
    return;
  }
  if (!nearbyMap) nearbyMap = new AMap.Map(container,{zoom:15,center:[plan.origin.lng,plan.origin.lat]});
  nearbyMap.clearMap();
  points.forEach((p,i) => new AMap.Marker({position:[p.lng,p.lat],title:`${i}. ${p.name}`,map:nearbyMap}));
  plan.legs.forEach(leg => { if (leg.available && leg.coordinates.length) new AMap.Polyline({path:leg.coordinates,showDir:true,strokeColor:"#0b7054",strokeWeight:5,map:nearbyMap}); });
  nearbyMap.setFitView();
}

function renderNearbyWeather(weather) {
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
  currentNearbyPlan = plan;
  const output=nearbyById("nearby-output"); output.replaceChildren();
  const intro=nearbyText(output,"section",""); intro.className="nearby-card";
  nearbyText(intro,"h3",`${plan.request.city} · ${plan.request.date} 附近遊建議`);
  nearbyText(intro,"p",`定位：${plan.origin.name}。請確認這是你要出發的位置。`);
  nearbyText(intro,"p",`${plan.request.members.length} 位已登記成員 · ${plan.currency} · ${plan.request.hours} 小時`);
  nearbyLink(intro,"起點資料",plan.origin.source_url);
  plan.reminders.forEach(t => nearbyText(intro,"p",t));
  const weather=nearbyText(output,"section",""); weather.id="nearby-weather"; weather.className="nearby-card";
  renderNearbyWeather(plan.weather);
  plan.stops.forEach((stop,i) => {
    const card=nearbyText(output,"section",""); card.className="nearby-card";
    nearbyText(card,"h3",`${i+1}. ${stop.arrival_time}–${stop.end_time} ${stop.name}`);
    nearbyText(card,"p",stop.visit_note);
    nearbyText(card,"p",`地址：${stop.address || "來源未提供"}；價格：${stop.price ?? "未知"}；來源營業時間：${stop.opening_hours || "未知"}`);
    nearbyLink(card,"地點資料",stop.source_url);
    nearbyText(card,"small",` ${stop.source || ""} · ${stop.fetched_at || ""}`);
    const leg=plan.legs[i]; if (!leg) return;
    nearbyText(card,"p",`${leg.from_name} → ${leg.to_name}：${leg.available ? `約 ${leg.duration_min} 分鐘／${leg.distance_m} 米（路網估算）` : "交通時間待查"}`);
    const detail=nearbyText(card,"details",""); nearbyText(detail,"summary","查看分段方向");
    const list=nearbyText(detail,"ol",""); (leg.steps || []).forEach(step => nearbyText(list,"li",step));
    nearbyLink(card,"開啟導航",leg.navigation_url); nearbyLink(card,"查公交／轉乘",leg.transit_url); nearbyLink(card,"路線資料來源",leg.source_url);
  });
  const help=nearbyText(output,"section",""); help.className="nearby-card"; nearbyText(help,"h3","交通使用方式");
  plan.transport_help.forEach(t => nearbyText(help,"p",t));
  drawNearbyMap(plan);
}
function restoreNearby(plan) {
  const r=plan.request;
  for (const [id,key] of [["city","city"],["location","location"],["date","date"],["time","start_time"],["hours","hours"],["mode","mode"]]) nearbyById("nearby-"+id).value=r[key];
  nearbyById("nearby-members").replaceChildren(); r.members.forEach(m => addNearbyMember(m.name,m.preferences || ""));
  renderNearby(plan);
}
async function refreshNearbyWeather() {
  if (!currentNearbyPlan || nearbyBusy) return;
  nearbyBusy=true;
  try {
    const response=await fetch("/nearby-weather",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    const data=await response.json(); if (!response.ok) throw new Error(data.error || "查詢失敗");
    currentNearbyPlan.weather=data.weather; renderNearbyWeather(data.weather);
  } catch (e) { nearbyById("nearby-status").textContent="天氣更新失敗："+e.message; }
  finally { nearbyBusy=false; }
}
nearbyById("nearby-form").onsubmit=async event => {
  event.preventDefault(); if (nearbyBusy) return;
  const data={city:nearbyById("nearby-city").value,location:nearbyById("nearby-location").value,date:nearbyById("nearby-date").value,start_time:nearbyById("nearby-time").value,hours:Number(nearbyById("nearby-hours").value),mode:nearbyById("nearby-mode").value,
    members:[...nearbyById("nearby-members").children].map(row => ({name:row.children[0].value,preferences:row.children[1].value}))};
  nearbyBusy=true; nearbyById("build-nearby").disabled=true;
  nearbyById("nearby-status").textContent="正在查詢地點、路線與天氣，通常需要數十秒…";
  try {
    const response=await fetch("/nearby-plan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    const result=await response.json(); if (!response.ok) throw new Error(result.error || "規劃失敗");
    renderNearby(result.nearby_plan);
    addMessage("user",`規劃 ${data.city} ${data.location} 附近遊`); addMessage("assistant",result.chat_reply);
    nearbyById("nearby-status").textContent="建議行程已保存。頁面開啟時每10分鐘更新天氣，不會自動修改行程。";
  } catch (e) { nearbyById("nearby-status").textContent=e.message; }
  finally { nearbyBusy=false; nearbyById("build-nearby").disabled=false; }
};
setInterval(() => { if (document.visibilityState === "visible") refreshNearbyWeather(); },600000);
