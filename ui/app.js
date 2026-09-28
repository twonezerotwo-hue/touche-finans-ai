const $ = (id) => document.getElementById(id);
const money = new Intl.NumberFormat("tr-TR", {style:"currency", currency:"TRY", maximumFractionDigits:2});
const state = {data:null, session:null, projectData:null};
const svgNS = "http://www.w3.org/2000/svg";

function profile(){ return {risk:$("risk").value, rezerv_gun:Number($("rezerv").value), yatirim_vadesi_ay:Number($("vade").value)}; }
function savePrefs(){ localStorage.setItem("toucheFinansPrefs", JSON.stringify({faaliyet:$("faaliyet").value,...profile()})); }
function setMoney(id,value,polarity=false){ const el=$(id); el.textContent=money.format(Number(value||0)); el.classList.remove("negative","positive"); if(polarity) el.classList.add(value<0?"negative":"positive"); }
function escStatus(status){ return status==="Gecikmiş"?"danger":((status.includes("gün")||status.includes("gun")||status==="Vade eksik")?"warn":""); }
function button(label,className,action){ const b=document.createElement("button"); b.type="button"; b.textContent=label; if(className)b.className=className; b.addEventListener("click",action); return b; }
function percent(value){ return `%${Number(value||0).toLocaleString("tr-TR",{maximumFractionDigits:1})}`; }
function setAIStatus(text,busy=false){ const el=$("aiStatus"); el.replaceChildren(); const dot=document.createElement("i"); if(busy)dot.style.background="#d8a94b"; el.append(dot,document.createTextNode(` ${text}`)); }
function svgEl(name,attrs={}){ const el=document.createElementNS(svgNS,name); Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v))); return el; }

async function jsonRequest(url,options={}){
  const res=await fetch(url,options); let data={}; try{data=await res.json();}catch(_){ }
  if(!res.ok) throw new Error(data.hata||`İstek başarısız (${res.status})`); return data;
}

async function checkSession(){
  state.session=await jsonRequest("/api/session");
  if(!state.session.yetkili){ $("pairOverlay").hidden=false; return false; }
  $("pairOverlay").hidden=true;
  if(state.session.yerel) $("remoteButton").hidden=false;
  return true;
}

async function loadActivities(){
  const data=await jsonRequest("/api/faaliyetler");
  data.faaliyetler.forEach(x=>{
    const o=document.createElement("option"); o.value=x.key; o.textContent=x.ad; $("faaliyet").appendChild(o);
    const u=o.cloneNode(true); $("uploadFirma").appendChild(u);
  });
  try{ const p=JSON.parse(localStorage.getItem("toucheFinansPrefs")||"{}"); if(p.faaliyet) $("faaliyet").value=p.faaliyet; if(p.risk) $("risk").value=p.risk; if(p.rezerv_gun) $("rezerv").value=String(p.rezerv_gun); if(p.yatirim_vadesi_ay) $("vade").value=String(p.yatirim_vadesi_ay); }catch(_){ }
}

async function loadDashboard(){
  savePrefs(); const p=profile();
  const q=new URLSearchParams({faaliyet:$("faaliyet").value,risk:p.risk,rezerv_gun:p.rezerv_gun,yatirim_vadesi_ay:p.yatirim_vadesi_ay});
  const d=await jsonRequest(`/api/dashboard?${q}`); state.data=d;
  $("anaKarar").textContent=d.ana_karar; $("veriNotu").textContent=d.veri_notu;
  setMoney("yatirimKapasitesi",d.nakit_plani.yatirim_kapasitesi); setMoney("nakit",d.ozet.nakit); setMoney("alacak",d.ozet.alacak); setMoney("borc",d.ozet.borc); setMoney("net",d.ozet.net,true);
  $("rezervNotu").textContent=`${d.profil.rezerv_gun} günlük hedef rezerv`;
  $("tahsilatNotu").textContent=`30 günde ${money.format(d.nakit_plani["30_gun_beklenen_tahsilat"]||0)} bekleniyor`;
  $("borcNotu").textContent=d.veri_kalitesi?.vadesiz_borc?`${money.format(d.veri_kalitesi.vadesiz_borc)} vadesiz`:`Vadeler kontrol altında`;
  setMoney("rezervTutar",d.nakit_plani.rezerv); setMoney("tahsilat",d.nakit_plani["30_gun_beklenen_tahsilat"]); setMoney("vadeliOdeme",d.nakit_plani["30_gun_vadeli_odeme"]); setMoney("acik",d.nakit_plani.finansman_acigi);
  $("acik").className=d.nakit_plani.finansman_acigi>0?"negative":"positive";
  $("odemeSayisi").textContent=`${d.odemeler.length} kayıt`;
  const list=$("odemeler"); list.replaceChildren();
  if(!d.odemeler.length){ const p=document.createElement("p"); p.className="empty"; p.textContent="Açık ödeme kaydı bulunamadı."; list.appendChild(p); }
  d.odemeler.forEach(x=>{ const row=document.createElement("div"); row.className="payment"; const name=document.createElement("div"); name.className="name"; const strong=document.createElement("strong"); strong.textContent=x.cari; const small=document.createElement("small"); small.textContent=`${x.proje||x.faaliyet} · ${x.vade||"vade girilmedi"}`; name.append(strong,small); const amount=document.createElement("div"); amount.className="amount"; amount.textContent=money.format(x.tutar); const status=document.createElement("div"); status.className=`status ${escStatus(x.durum)}`; status.textContent=x.durum; row.append(name,amount,status); list.appendChild(row); });
  const hareketler=d.son_hareketler||[]; $("hareketSayisi").textContent=`${hareketler.length} kayıt`;
  const hareketList=$("sonHareketler"); hareketList.replaceChildren();
  if(!hareketler.length){ const p=document.createElement("p"); p.className="empty"; p.textContent="Gerçekleşen nakit hareketi bulunamadı."; hareketList.appendChild(p); }
  hareketler.forEach(x=>{ const row=document.createElement("div"); row.className="payment"; const name=document.createElement("div"); name.className="name"; const strong=document.createElement("strong"); strong.textContent=x.cari; const small=document.createElement("small"); small.textContent=`${x.tarih} · ${x.proje||x.faaliyet}${x.aciklama?` · ${x.aciklama}`:""}`; name.append(strong,small); const amount=document.createElement("div"); amount.className="amount"; amount.textContent=money.format(x.tutar); const status=document.createElement("div"); status.className=`status ${x.yon==="Ödeme"?"danger":""}`; status.textContent=x.yon; row.append(name,amount,status); hareketList.appendChild(row); });
  const warnings=$("uyarilar"); warnings.replaceChildren(); d.uyarilar.forEach(t=>{ const w=document.createElement("div"); w.className="warning"; w.textContent=t; warnings.appendChild(w); });
  renderCashChart(d.nakit_projeksiyonu||[]);
  renderHealth(d);
  renderActions(d.aksiyonlar||[]);
  renderPerformance(d.faaliyet_performansi||[]);
  $("assistantContext").textContent=$("faaliyet").selectedOptions[0]?.textContent||"Tüm holding";
}

function renderCashChart(items){
  const host=$("cashChart"); host.replaceChildren();
  if(!items.length){const p=document.createElement("p");p.className="empty";p.textContent="Projeksiyon için vadeli kayıt bulunamadı.";host.appendChild(p);return;}
  const width=760,height=230,pad={l:54,r:18,t:18,b:35}; const values=items.map(x=>Number(x.kapanis||0));
  let min=Math.min(0,...values),max=Math.max(0,...values); if(max===min){max=min+1;} const range=max-min;
  const x=i=>pad.l+i*(width-pad.l-pad.r)/Math.max(1,items.length-1); const y=v=>pad.t+(max-v)*(height-pad.t-pad.b)/range;
  const svg=svgEl("svg",{viewBox:`0 0 ${width} ${height}`,preserveAspectRatio:"none"});
  const defs=svgEl("defs"),grad=svgEl("linearGradient",{id:"areaGradient",x1:"0",y1:"0",x2:"0",y2:"1"}); grad.append(svgEl("stop",{offset:"0%","stop-color":"#1c9a6b","stop-opacity":".25"}),svgEl("stop",{offset:"100%","stop-color":"#1c9a6b","stop-opacity":".01"})); defs.appendChild(grad); svg.appendChild(defs);
  for(let i=0;i<4;i++){const gy=pad.t+i*(height-pad.t-pad.b)/3;svg.appendChild(svgEl("line",{x1:pad.l,y1:gy,x2:width-pad.r,y2:gy,class:"chart-grid"}));const label=svgEl("text",{x:pad.l-8,y:gy+4,"text-anchor":"end",class:"chart-label"});label.textContent=compactMoney(max-i*range/3);svg.appendChild(label);}
  if(min<0&&max>0)svg.appendChild(svgEl("line",{x1:pad.l,y1:y(0),x2:width-pad.r,y2:y(0),class:"chart-zero"}));
  const points=items.map((item,i)=>`${x(i)},${y(item.kapanis)}`).join(" "); const area=`M ${x(0)} ${height-pad.b} L ${points.replaceAll(" "," L ")} L ${x(items.length-1)} ${height-pad.b} Z`;
  svg.appendChild(svgEl("path",{d:area,class:"chart-area"})); svg.appendChild(svgEl("polyline",{points,class:"chart-line"}));
  items.forEach((item,i)=>{svg.appendChild(svgEl("circle",{cx:x(i),cy:y(item.kapanis),r:3.3,class:"chart-point"}));if(i%2===0||i===items.length-1){const label=svgEl("text",{x:x(i),y:height-13,"text-anchor":"middle",class:"chart-label"});label.textContent=`H${item.hafta}`;svg.appendChild(label);}});
  host.appendChild(svg); const low=Math.min(...values),end=values.at(-1); const legend=$("cashChartLegend"); legend.replaceChildren(); [["Başlangıç",state.data?.ozet?.nakit||values[0]],["13. hafta",end],["En düşük",low]].forEach(([label,value])=>{const span=document.createElement("span");span.append(document.createTextNode(`${label} `));const strong=document.createElement("strong");strong.textContent=money.format(value);span.appendChild(strong);legend.appendChild(span);});
}

function compactMoney(value){ const n=Number(value||0),abs=Math.abs(n); if(abs>=1e6)return `${(n/1e6).toLocaleString("tr-TR",{maximumFractionDigits:1})} Mn`; if(abs>=1e3)return `${(n/1e3).toLocaleString("tr-TR",{maximumFractionDigits:0})} B`; return n.toLocaleString("tr-TR",{maximumFractionDigits:0}); }

function renderHealth(d){
  const risk=d.risk||{},quality=d.veri_kalitesi||{}; $("riskScore").textContent=String(risk.skor??"—"); $("riskGauge").style.setProperty("--score",Math.max(0,Math.min(100,Number(risk.skor||0))));
  $("riskLevel").textContent=`${risk.seviye||"—"} risk`; $("riskLevel").className=`status ${risk.seviye==="Yüksek"?"danger":risk.seviye==="Orta"?"warn":""}`;
  $("liquidityCoverage").textContent=percent(risk.likidite_karsilama); $("dataQuality").textContent=`${quality.skor??"—"}/100`; $("qualityBar").style.width=`${Math.max(0,Math.min(100,Number(quality.skor||0)))}%`;
  $("lastRecord").textContent=quality.son_kayit_tarihi?`Son finans kaydı: ${quality.son_kayit_tarihi} · ${quality.kayit_sayisi} fiş`:`Finans kaydı tarihi bulunamadı.`;
  const issues=$("qualityIssues");issues.replaceChildren();(quality.sorunlar||[]).slice(0,3).forEach(x=>{const el=document.createElement("div");el.className="mini-alert";el.textContent=x;issues.appendChild(el);});
}

function renderActions(actions){
  $("actionCount").textContent=`${actions.length} aksiyon`; const list=$("actions");list.replaceChildren(); if(!actions.length){const p=document.createElement("p");p.className="empty";p.textContent="Kritik aksiyon bulunamadı.";list.appendChild(p);return;}
  actions.forEach(x=>{const card=document.createElement("article");card.className="action-card";const top=document.createElement("div");top.className="action-card-top";const priority=document.createElement("span");priority.className=`priority ${x.oncelik==="Kritik"?"critical":x.oncelik==="Yüksek"?"high":""}`;priority.textContent=x.oncelik;const time=document.createElement("time");time.textContent=x.ufuk;top.append(priority,time);const title=document.createElement("strong");title.textContent=x.baslik;const why=document.createElement("p");why.textContent=x.neden;card.append(top,title,why);if(Number(x.tutar)>0){const amount=document.createElement("span");amount.className="action-amount";amount.textContent=money.format(x.tutar);card.appendChild(amount);}list.appendChild(card);});
}

function renderPerformance(items){
  const host=$("activityPerformance");host.replaceChildren(); if(!items.length){const p=document.createElement("p");p.className="empty";p.textContent="Seçili görünümde karşılaştırılabilir faaliyet bulunamadı.";host.appendChild(p);return;}
  const head=document.createElement("div");head.className="performance-head";["Faaliyet","Gelir","Gider","Net","Nakit","Marj"].forEach(x=>{const s=document.createElement("span");s.textContent=x;head.appendChild(s);});host.appendChild(head);
  items.forEach(x=>{const row=document.createElement("div");row.className="performance-row";const name=document.createElement("div");name.className="activity-name";const strong=document.createElement("strong");strong.textContent=x.ad;const sub=document.createElement("span");sub.textContent=`Alacak ${money.format(x.alacak)} · Borç ${money.format(x.borc)}`;name.append(strong,sub);row.appendChild(name);[x.gelir,x.gider,x.net,x.nakit].forEach(v=>{const s=document.createElement("span");s.className=`performance-value ${v<0?"negative":""}`;s.textContent=money.format(v);row.appendChild(s);});const margin=document.createElement("span");margin.className=`margin-pill ${x.marj<0?"negative":""}`;margin.textContent=percent(x.marj);row.appendChild(margin);host.appendChild(row);});
}

function summaryBox(label,value,isPercent=false){
  const box=document.createElement("div"); const span=document.createElement("span"); const strong=document.createElement("strong");
  span.textContent=label; strong.textContent=isPercent?`%${Number(value||0).toLocaleString("tr-TR",{maximumFractionDigits:2})}`:money.format(Number(value||0)); box.append(span,strong); return box;
}

function openProjectItem(item){
  $("projectId").value=item.kalem_id; $("projectDialogTitle").textContent=item.is_kalemi;
  $("projectDialogMeta").textContent=`${item.faaliyet} · ${item.proje} · ${item.maliyet_grubu} · ${item.birim}`;
  $("projectQuantity").value=item.metraj; $("projectSalesPrice").value=item.satis_birim_fiyati;
  $("projectCostType").value=item.maliyet_turu; $("projectCostPrice").value=item.maliyet_birim_fiyati;
  $("projectStatus").value=item.durum; $("projectNote").value=item.not; $("projectError").textContent="";
  $("projectDialog").showModal();
}

function renderProjectItems(data){
  state.projectData=data; $("kalemSayisi").textContent=`${data.ozet.kalem_sayisi} kayıt`;
  const summary=$("projeOzeti"); summary.replaceChildren(
    summaryBox("Onaylı ciro",data.ozet.ciro), summaryBox("Usta maliyeti",data.ozet.maliyet),
    summaryBox("Brüt fark",data.ozet.brut_fark), summaryBox("Brüt marj",data.ozet.brut_marj,true)
  );
  const list=$("projeKalemleri"); list.replaceChildren();
  if(!data.kalemler.length){const p=document.createElement("p");p.className="empty";p.textContent="Bu faaliyet için proje iş kalemi bulunamadı.";list.appendChild(p);return;}
  data.kalemler.forEach(item=>{
    const row=document.createElement("div"); row.className="project-item";
    const info=document.createElement("div"); info.className="project-info"; const title=document.createElement("strong"); title.textContent=item.is_kalemi;
    const meta=document.createElement("small"); meta.textContent=`${item.proje} · ${item.maliyet_grubu} · ${item.metraj.toLocaleString("tr-TR")} ${item.birim}`; info.append(title,meta);
    const figures=document.createElement("div"); figures.className="project-figures";
    const revenue=document.createElement("span"); revenue.textContent=`Ciro ${money.format(item.ciro)}`;
    const cost=document.createElement("span"); cost.textContent=`Maliyet ${money.format(item.maliyet)}`;
    const diff=document.createElement("strong"); diff.className=item.brut_fark<0?"negative":"positive"; diff.textContent=`Fark ${money.format(item.brut_fark)}`; figures.append(revenue,cost,diff);
    const actions=document.createElement("div"); actions.className="project-actions"; const status=document.createElement("span"); status.className=`status ${item.durum==="İptal"?"danger":item.durum==="Taslak"?"warn":""}`; status.textContent=item.durum;
    actions.append(status,button("Düzenle","project-edit",()=>openProjectItem(item))); row.append(info,figures,actions); list.appendChild(row);
  });
}

async function loadProjectItems(){
  const q=new URLSearchParams({faaliyet:$("faaliyet").value});
  renderProjectItems(await jsonRequest(`/api/proje-kalemleri?${q}`));
}

async function saveProjectItem(event){
  event.preventDefault(); const id=$("projectId").value; $("projectSave").disabled=true; $("projectError").textContent="";
  const changes={metraj:$("projectQuantity").value,satis_birim_fiyati:$("projectSalesPrice").value,maliyet_turu:$("projectCostType").value,maliyet_birim_fiyati:$("projectCostPrice").value,durum:$("projectStatus").value,not:$("projectNote").value};
  try{
    await jsonRequest(`/api/proje-kalemi/${encodeURIComponent(id)}/guncelle`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({degisiklikler:changes})});
    $("projectDialog").close(); await Promise.all([loadProjectItems(),loadDashboard()]); addMessage(`${id} iş kalemi güncellendi. Ana Excel ve finans defteri eşitlendi.`,"assistant");
  }catch(e){$("projectError").textContent=e.message;}finally{$("projectSave").disabled=false;}
}

function addMessage(text,who,extra=""){ const el=document.createElement("div"); el.className=`message ${who} ${extra}`; el.textContent=text; $("chat").appendChild(el); $("chat").scrollTop=$("chat").scrollHeight; return el; }
function renderEvidence(data){
  const box=$("aiEvidence");box.replaceChildren();const items=[...(data.kanitlar||[]),...(data.aksiyonlar||[]).map(x=>`Aksiyon: ${x}`)];box.hidden=!items.length;if(!items.length)return;const h=document.createElement("h4");h.textContent="Kullanılan kanıtlar ve aksiyonlar";box.appendChild(h);items.slice(0,7).forEach(x=>{const row=document.createElement("div");row.className="evidence-item";row.textContent=x;box.appendChild(row);});
}
async function ask(question){
  const q=(question||$("soru").value).trim(); if(!q) return; $("soru").value=""; addMessage(q,"user"); const loading=addMessage("Defter, vadeler ve risk göstergeleri analiz ediliyor…","assistant","loading"); $("gonder").disabled=true;setAIStatus("Yerel model analiz ediyor",true);$("aiEvidence").hidden=true;
  try{ const d=await jsonRequest("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({soru:q,faaliyet:$("faaliyet").value,profil:profile(),llm:true})}); loading.remove(); addMessage(d.cevap,"assistant");renderEvidence(d);$("aiConfidence").textContent=`${d.guven||"—"} güven`;setAIStatus(d.llm==="yerel_ollama"?"Yerel Ollama ile yanıtlandı":"Deterministik motorla yanıtlandı"); }
  catch(e){ loading.textContent=`Yanıt hazırlanamadı: ${e.message}`; loading.classList.remove("loading"); }
  finally{ $("gonder").disabled=false;if($("aiStatus").textContent.includes("analiz"))setAIStatus("Defter kanıtlarıyla hazır"); }
}

async function runScenario(event){
  event.preventDefault();$("scenarioRun").disabled=true;
  try{
    const d=await jsonRequest("/api/senaryo",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({faaliyet:$("faaliyet").value,profil:profile(),degisiklikler:{ek_tahsilat:$("scenarioCollection").value,ek_odeme:$("scenarioPayment").value,yeni_yatirim:$("scenarioInvestment").value}})});
    const result=$("scenarioResult");result.replaceChildren();[["Senaryo nakdi",d.senaryo_nakit],["Korunacak tutar",d.korunmasi_gereken],["Yatırım kapasitesi",d.yatirim_kapasitesi]].forEach(([label,value])=>{const box=document.createElement("div");const span=document.createElement("span");span.textContent=label;const strong=document.createElement("strong");strong.textContent=money.format(value);box.append(span,strong);result.appendChild(box);});const decision=document.createElement("div");decision.className="scenario-decision";const span=document.createElement("span");span.textContent=d.finansman_acigi?"Finansman açığı":"Sonuç";const strong=document.createElement("strong");strong.textContent=d.finansman_acigi?money.format(d.finansman_acigi):d.karar;decision.append(span,strong);result.appendChild(decision);
  }catch(e){const result=$("scenarioResult");result.textContent=`Senaryo hesaplanamadı: ${e.message}`;}finally{$("scenarioRun").disabled=false;}
}

function renderDocument(doc){
  const card=document.createElement("article"); card.className="document-card";
  const head=document.createElement("div"); head.className="doc-head";
  const title=document.createElement("div"); const strong=document.createElement("strong"); strong.textContent=`${doc.firma} · ${doc.tur}`; const meta=document.createElement("div"); meta.className="doc-meta"; meta.textContent=`${doc.dosya} · ${doc.motor}`; title.append(strong,meta);
  const validation=document.createElement("span"); validation.className=`validation ${doc.dogrulama.durum||"FAIL"}`; validation.textContent=`${doc.dogrulama.durum||"HATA"} ${doc.dogrulama.skor}/100`; head.append(title,validation); card.appendChild(head);
  const records=document.createElement("div"); records.className="doc-records";
  doc.kayitlar.forEach((x,i)=>{ const line=document.createElement("div"); line.className="doc-record"; const amount=x.tutar!==null&&x.tutar!==""?` · ${money.format(Number(x.tutar))}`:""; line.textContent=`${i+1}. ${x.tarih||"Tarih yok"} · ${x.cari||"Cari yok"}${amount}`; records.appendChild(line); }); card.appendChild(records);
  [...doc.dogrulama.uyarilar,...doc.dogrulama.hatalar].slice(0,3).forEach(x=>{ const w=document.createElement("div"); w.className="warning"; w.textContent=x; card.appendChild(w); });
  if(doc.durum==="ONAY_BEKLIYOR"){
    const actions=document.createElement("div"); actions.className="doc-actions";
    actions.append(button("Onayla","approve",()=>documentAction(doc.token,"onay")),button("Düzelt","",()=>toggleCorrection(card,doc.token)),button("Reddet","reject",()=>documentAction(doc.token,"reddet"))); card.appendChild(actions);
  }else{ const status=document.createElement("div"); status.className="doc-meta"; status.textContent=`Durum: ${doc.durum}`; card.appendChild(status); }
  return card;
}

function toggleCorrection(card,token){
  const old=card.querySelector(".correction"); if(old){old.remove();return;}
  const box=document.createElement("div"); box.className="correction"; const area=document.createElement("textarea"); area.placeholder="Her satıra Alan=Değer yazın\nÖrnek: Tutar=12500,50\nCari=Örnek Firma";
  box.append(area,button("Kontrol et","",async()=>{ try{ const changes={}; area.value.split(/\r?\n/).filter(Boolean).forEach(line=>{const pos=line.indexOf("=");if(pos<1)throw new Error(`Alan=Değer bekleniyor: ${line}`);changes[line.slice(0,pos).trim()]=line.slice(pos+1).trim();}); await jsonRequest(`/api/belge/${token}/duzelt`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({degisiklikler:changes})}); await loadDocuments(); addMessage("Belge düzeltildi ve yeniden doğrulandı.","assistant"); }catch(e){addMessage(`Düzeltme yapılamadı: ${e.message}`,"assistant");} })); card.appendChild(box); area.focus();
}

async function loadDocuments(){ const data=await jsonRequest("/api/belgeler"); const list=$("belgeler"); list.replaceChildren(); data.belgeler.slice(0,10).forEach(x=>list.appendChild(renderDocument(x))); }
async function documentAction(token,action){
  try{ const result=await jsonRequest(`/api/belge/${token}/${action}`,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}); if(action==="onay"&&!result.tamam){addMessage(`Onay kaydedildi: ${result.onay.mevcut}/${result.onay.gerekli}. Diğer yetkili bekleniyor.`,"assistant");}else{addMessage(action==="onay"?"Belge Excel ve merkez finans defterine işlendi.":"Belge reddedildi; Excel'e yazılmadı.","assistant");} await Promise.all([loadDocuments(),loadDashboard()]); }
  catch(e){addMessage(`Belge işlemi başarısız: ${e.message}`,"assistant");}
}

async function uploadDocument(event){
  event.preventDefault(); const file=$("dosya").files[0]; if(!file)return; const form=new FormData(); form.append("dosya",file); form.append("firma",$("uploadFirma").value); $("uploadButton").disabled=true; $("uploadStatus").textContent="Belge yapay zekâ ve doğrulama katmanı tarafından inceleniyor…";
  try{ const doc=await jsonRequest("/api/upload",{method:"POST",body:form}); $("uploadStatus").textContent=`${doc.firma} için ${doc.dogrulama.durum} sonucu alındı. Onayınızı bekliyor.`; $("uploadForm").reset(); await loadDocuments(); addMessage(`${doc.dosya} okundu. Doğrulama: ${doc.dogrulama.durum} (${doc.dogrulama.skor}/100). Excel'e yazmadan önce kararınızı bekliyorum.`,"assistant"); }
  catch(e){$("uploadStatus").textContent=`Belge işlenemedi: ${e.message}`;}
  finally{$("uploadButton").disabled=false;}
}

async function loadRemoteUsers(){ const data=await jsonRequest("/api/remote/users"); const list=$("remoteUsers"); list.replaceChildren(); if(!data.kullanicilar.length){const s=document.createElement("span");s.textContent="Henüz kullanıcı yok.";list.appendChild(s);} data.kullanicilar.forEach(x=>{const row=document.createElement("div");row.className="remote-user";const name=document.createElement("span");name.textContent=x;row.append(name,button("Yetkiyi kaldır","",async()=>{await jsonRequest(`/api/remote/users/${encodeURIComponent(x)}`,{method:"DELETE"});await loadRemoteUsers();}));list.appendChild(row);}); }

document.addEventListener("DOMContentLoaded",async()=>{
  $("headerDate").textContent=new Intl.DateTimeFormat("tr-TR",{day:"2-digit",month:"long",year:"numeric"}).format(new Date());
  $("pairForm").addEventListener("submit",async e=>{e.preventDefault();try{await jsonRequest("/api/session/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kod:$("pairCode").value})});location.reload();}catch(err){$("pairError").textContent=err.message;}});
  if(!await checkSession())return;
  try{ await loadActivities(); await Promise.all([loadDashboard(),loadDocuments(),loadProjectItems()]); }catch(e){ $("anaKarar").textContent=`Veri yüklenemedi: ${e.message}`; }
  $("faaliyet").addEventListener("change",()=>Promise.all([loadDashboard(),loadProjectItems()]).catch(e=>addMessage(e.message,"assistant")));
  ["risk","rezerv","vade"].forEach(id=>$(id).addEventListener("change",()=>loadDashboard().catch(e=>addMessage(e.message,"assistant"))));
  $("chatForm").addEventListener("submit",e=>{e.preventDefault();ask();}); document.querySelectorAll("[data-question]").forEach(b=>b.addEventListener("click",()=>ask(b.dataset.question)));
  $("scenarioForm").addEventListener("submit",runScenario);
  $("uploadForm").addEventListener("submit",uploadDocument); $("dosya").addEventListener("change",()=>{const f=$("dosya").files[0];if(f)$("uploadStatus").textContent=`Seçilen dosya: ${f.name}`;});
  $("remoteButton").addEventListener("click",async()=>{await loadRemoteUsers();$("remoteDialog").showModal();}); $("remoteDialog").querySelector(".dialog-close").addEventListener("click",()=>$("remoteDialog").close());
  $("projectDialog").querySelector(".dialog-close").addEventListener("click",()=>$("projectDialog").close()); $("projectForm").addEventListener("submit",saveProjectItem);
  $("generateCode").addEventListener("click",async()=>{try{const d=await jsonRequest("/api/remote/code",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});$("remoteCode").textContent=d.kod;}catch(e){$("remoteCode").textContent=e.message;}});
  ["dragenter","dragover"].forEach(n=>$("dropZone").addEventListener(n,e=>{e.preventDefault();$("dropZone").classList.add("drag");})); ["dragleave","drop"].forEach(n=>$("dropZone").addEventListener(n,e=>{$("dropZone").classList.remove("drag");}));
});
