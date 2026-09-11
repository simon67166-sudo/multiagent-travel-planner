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
  const pref = document.createElement("input"); pref.placeholder = "偏好／饮食／步行／预算需求"; pref.value = preferences;
  const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "移除"; remove.onclick = () => row.remove();
  row.append(n, pref, remove); nearbyById("nearby-members").appendChild(row);
}
nearbyById("add-member").onclick = () => addNearbyMember();
nearbyById("nearby-city").onchange = () => {
  nearbyById("nearby-location").value = nearbyById("nearby-city").value === "香港" ? "尖沙咀" : "大三巴";
};
const nearbyTomorrow = new Date(); nearbyTomorrow.setDate(nearbyTomorrow.getDate() + 1);
nearbyById("nearby-date").value = `${nearbyTomorrow.getFullYear()}-${String(nearbyTomorrow.getMonth()+1).padStart(2,"0")}-${String(nearbyTomorrow.getDate()).padStart(2,"0")}`;
addNearbyMember("我", "喜欢散步和文化景点");

function drawNearbyMap(plan) {
  const container = nearbyById("nearby-map"); container.style.display = "block";
  nearbyById("map-frames").style.display = "none";
  nearbyById("map-legend").style.display = "none";
  nearbyById("map-unclustered").style.display = "none";
  if (plan.crs !== "GCJ02") {
    if (nearbyMap) { nearbyMap.destroy(); nearbyMap = null; }
    container.textContent = "这份行程使用旧版地图座标，请按『生成附近游攻略』重新查询高德路线。原对话与行程记录仍保留。";
    return;
  }
  const points = [plan.origin, ...plan.stops];
  if (typeof AMap === "undefined") {
    container.textContent = "地图元件未载入（AMAP_JS_KEY/安全密钥可能未设置）；请使用下面的分段导航连结。";
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
  nearbyText(box,"h3","出游时段天气与提醒");
  if (weather.available) nearbyText(box,"p",`气温 ${weather.temperature_min ?? "未知"}–${weather.temperature_max ?? "未知"} °C；最高降雨机率 ${weather.max_rain_probability ?? "未知"}%`);
  else nearbyText(box,"p","目前没有可用的该时段预报。");
  (weather.reminders || []).forEach(t => nearbyText(box,"p",t));
  nearbyText(box,"small",`${weather.source || ""} · 查询时间 ${weather.fetched_at || "未取得"}`);
  nearbyText(box,"br",""); nearbyLink(box,"预报来源",weather.source_url); nearbyLink(box,"官方天气及警告",weather.official_url);
  const b=nearbyText(box,"button","更新天气"); b.type="button"; b.onclick=refreshNearbyWeather;
}

function renderNearby(plan) {
  currentNearbyPlan = plan;
  const output=nearbyById("nearby-output"); output.replaceChildren();
  const intro=nearbyText(output,"section",""); intro.className="nearby-card";
  nearbyText(intro,"h3",`${plan.request.city} · ${plan.request.date} 附近游建议`);
  nearbyText(intro,"p",`定位：${plan.origin.name}。请确认这是你要出发的位置。`);
  nearbyText(intro,"p",`${plan.request.members.length} 位已登记成员 · ${plan.currency} · ${plan.request.hours} 小时`);
  nearbyLink(intro,"起点资料",plan.origin.source_url);
  plan.reminders.forEach(t => nearbyText(intro,"p",t));
  const weather=nearbyText(output,"section",""); weather.id="nearby-weather"; weather.className="nearby-card";
  renderNearbyWeather(plan.weather);
  plan.stops.forEach((stop,i) => {
    const card=nearbyText(output,"section",""); card.className="nearby-card";
    nearbyText(card,"h3",`${i+1}. ${stop.arrival_time}–${stop.end_time} ${stop.name}`);
    nearbyText(card,"p",stop.visit_note);
    nearbyText(card,"p",`地址：${stop.address || "来源未提供"}；价格：${stop.price ?? "未知"}；来源营业时间：${stop.opening_hours || "未知"}`);
    nearbyLink(card,"地点资料",stop.source_url);
    nearbyText(card,"small",` ${stop.source || ""} · ${stop.fetched_at || ""}`);
    const leg=plan.legs[i]; if (!leg) return;
    nearbyText(card,"p",`${leg.from_name} → ${leg.to_name}：${leg.available ? `约 ${leg.duration_min} 分钟／${leg.distance_m} 米（路网估算）` : "交通时间待查"}`);
    const detail=nearbyText(card,"details",""); nearbyText(detail,"summary","查看分段方向");
    const list=nearbyText(detail,"ol",""); (leg.steps || []).forEach(step => nearbyText(list,"li",step));
    nearbyLink(card,"开启导航",leg.navigation_url); nearbyLink(card,"查公交／转乘",leg.transit_url); nearbyLink(card,"路线资料来源",leg.source_url);
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
    const data=await response.json(); if (!response.ok) throw new Error(data.error || "查询失败");
    currentNearbyPlan.weather=data.weather; renderNearbyWeather(data.weather);
  } catch (e) { nearbyById("nearby-status").textContent="天气更新失败："+e.message; }
  finally { nearbyBusy=false; }
}
nearbyById("nearby-form").onsubmit=async event => {
  event.preventDefault(); if (nearbyBusy) return;
  const data={city:nearbyById("nearby-city").value,location:nearbyById("nearby-location").value,date:nearbyById("nearby-date").value,start_time:nearbyById("nearby-time").value,hours:Number(nearbyById("nearby-hours").value),mode:nearbyById("nearby-mode").value,
    members:[...nearbyById("nearby-members").children].map(row => ({name:row.children[0].value,preferences:row.children[1].value}))};
  nearbyBusy=true; nearbyById("build-nearby").disabled=true;
  nearbyById("nearby-status").textContent="正在查询地点、路线与天气，通常需要数十秒…";
  try {
    const response=await fetch("/nearby-plan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    const result=await response.json(); if (!response.ok) throw new Error(result.error || "规划失败");
    renderNearby(result.nearby_plan);
    addMessage("user",`规划 ${data.city} ${data.location} 附近游`); addMessage("assistant",result.chat_reply);
    nearbyById("nearby-status").textContent="建议行程已保存。页面开启时每10分钟更新天气，不会自动修改行程。";
  } catch (e) { nearbyById("nearby-status").textContent=e.message; }
  finally { nearbyBusy=false; nearbyById("build-nearby").disabled=false; }
};
setInterval(() => { if (document.visibilityState === "visible") refreshNearbyWeather(); },600000);
