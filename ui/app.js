const $ = (id) => document.getElementById(id);
const money = new Intl.NumberFormat("tr-TR", {style:"currency", currency:"TRY", maximumFractionDigits:2});
const state = {data:null, session:null, projectData:null, lastIntent:null, lastContextQuestion:null, activeDraftToken:null};

function formatAmount(value,currency="TL"){
  const amount=Number(value||0);const code=String(currency||"TL").trim().toUpperCase();
  if(code==="TL"||code==="TRY")return money.format(amount);
  if(["USD","EUR","GBP"].includes(code))return new Intl.NumberFormat("tr-TR",{style:"currency",currency:code,maximumFractionDigits:2}).format(amount);
  return `${amount.toLocaleString("tr-TR",{maximumFractionDigits:2})} ${code}`;
}

function profile(){ return {risk:"orta", rezerv_gun:30, yatirim_vadesi_ay:12}; }
function savePrefs(){ }
function setMoney(id,value,polarity=false){ const el=$(id); el.textContent=money.format(Number(value||0)); el.classList.remove("negative","positive"); if(polarity) el.classList.add(value<0?"negative":"positive"); }
function escStatus(status){ return status==="Gecikmiş"?"danger":((status.includes("gün")||status.includes("gun")||status==="Vade eksik")?"warn":""); }
function button(label,className,action){ const b=document.createElement("button"); b.type="button"; b.textContent=label; if(className)b.className=className; b.addEventListener("click",action); return b; }
function setAIStatus(text,busy=false){ const el=$("aiStatus"); el.replaceChildren(); const dot=document.createElement("i"); if(busy)dot.style.background="#d8a94b"; el.append(dot,document.createTextNode(` ${text}`)); }

async function jsonRequest(url,options={}){
  const res=await fetch(url,options); let data={}; try{data=await res.json();}catch(_){ }
  if(!res.ok) throw new Error(data.hata||`İstek başarısız (${res.status})`); return data;
}

function requestApprovalPassword(){
  const dialog=$("approvalPasswordDialog"),form=$("approvalPasswordForm"),input=$("approvalPassword"),error=$("approvalPasswordError"),cancel=$("approvalPasswordCancel");
  input.value="";error.textContent="";
  return new Promise(resolve=>{
    let done=false;
    const finish=value=>{if(done)return;done=true;form.onsubmit=null;cancel.onclick=null;dialog.oncancel=null;input.value="";if(dialog.open)dialog.close();resolve(value);};
    form.onsubmit=e=>{e.preventDefault();const value=input.value;if(!value){error.textContent="Onay şifresini girin.";return;}finish(value);};
    cancel.onclick=()=>finish(null);
    dialog.oncancel=e=>{e.preventDefault();finish(null);};
    dialog.showModal();setTimeout(()=>input.focus(),0);
  });
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
    $("transferSource").appendChild(o.cloneNode(true)); $("transferTarget").appendChild(o.cloneNode(true));
    $("approvalActivity").appendChild(o.cloneNode(true)); $("mappingActivity").appendChild(o.cloneNode(true));
  });
  $("faaliyet").value="tum";
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
  const risk=d.risk||{}; $("riskScore").textContent=String(risk.skor??"—"); $("riskGauge").style.setProperty("--score",Math.max(0,Math.min(100,Number(risk.skor||0))));
  $("odemeSayisi").textContent=`${d.odemeler.length} kayıt`;
  const list=$("odemeler"); list.replaceChildren();
  if(!d.odemeler.length){ const p=document.createElement("p"); p.className="empty"; p.textContent="Açık ödeme kaydı bulunamadı."; list.appendChild(p); }
  d.odemeler.slice(0,5).forEach(x=>{ const row=document.createElement("div"); row.className="payment scheduled-out"; const name=document.createElement("div"); name.className="name"; const strong=document.createElement("strong"); strong.textContent=x.cari; const small=document.createElement("small"); small.textContent=`${x.proje||x.faaliyet} · ${x.vade||"vade girilmedi"}`; name.append(strong,small); const amount=document.createElement("div"); amount.className="amount"; amount.textContent=money.format(x.tutar); const status=document.createElement("div"); status.className=`status due-out ${escStatus(x.durum)}`; status.textContent=x.durum; row.append(name,amount,status); list.appendChild(row); });
  const hareketler=d.son_hareketler||[]; $("hareketSayisi").textContent=`${hareketler.length} kayıt`;
  const hareketList=$("sonHareketler"); hareketList.replaceChildren();
  if(!hareketler.length){ const p=document.createElement("p"); p.className="empty"; p.textContent="Gerçekleşen nakit hareketi bulunamadı."; hareketList.appendChild(p); }
  hareketler.slice(0,5).forEach(x=>{ const cikis=x.yon==="Ödeme"; const row=document.createElement("div"); row.className=`payment ${cikis?"flow-out":"flow-in"}`; const name=document.createElement("div"); name.className="name"; const strong=document.createElement("strong"); strong.textContent=x.cari; const small=document.createElement("small"); small.textContent=`${x.tarih} · ${x.proje||x.faaliyet}${x.aciklama?` · ${x.aciklama}`:""}`; name.append(strong,small); const amount=document.createElement("div"); amount.className="amount"; amount.textContent=money.format(x.tutar); const status=document.createElement("div"); status.className=`status ${cikis?"paid-out":"cash-in"}`; status.textContent=x.yon; row.append(name,amount,status); hareketList.appendChild(row); });
  $("assistantContext").textContent=$("faaliyet").selectedOptions[0]?.textContent||"Tüm holding";
}

async function saveTransfer(event){
  event.preventDefault();$("transferError").textContent="";
  try{const result=await jsonRequest("/api/sirketler-arasi/transfer",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kaynak:$("transferSource").value,hedef:$("transferTarget").value,tarih:$("transferDate").value,tutar:$("transferAmount").value,aciklama:$("transferDescription").value})});$("transferDialog").close();await loadDashboard();addMessage(`Grup içi transfer kaydedildi: ${result.islem_id}. İki faaliyet fişi birlikte oluşturuldu.`,"assistant");}
  catch(e){$("transferError").textContent=e.message;}
}

async function saveFx(event){
  event.preventDefault();$("fxError").textContent="";
  try{const d=await jsonRequest("/api/kur",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tarih:$("fxDate").value,para_birimi:$("fxCurrency").value,kur:$("fxRate").value,kaynak:$("fxSource").value})});$("fxResult").textContent=`${d.tarih} ${d.para_birimi} kuru ${Number(d.kur).toLocaleString("tr-TR",{maximumFractionDigits:6})} TL olarak kaydedildi.`;}
  catch(e){$("fxError").textContent=e.message;}
}

async function runFxRevaluation(){
  $("fxError").textContent="";
  try{const d=await jsonRequest("/api/kur-degerleme",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tarih:$("fxDate").value})});$("fxResult").textContent=`${d.olusan.length} değerleme fişi oluşturuldu, ${d.atlanan.length} kalem atlandı.`;await loadDashboard();}
  catch(e){$("fxError").textContent=e.message;}
}

async function checkPeriod(event){
  event.preventDefault();
  try{const q=new URLSearchParams({yil:$("periodYear").value,ay:$("periodMonth").value});const d=await jsonRequest(`/api/donem-kontrol?${q}`);const lines=[`${d.donem}: ${d.kapanabilir?"Kapanışa hazır":"Kapanış engeli var"}`,...(d.engeller||[]).map(x=>`Engel: ${x}`),...(d.uyarilar||[]).map(x=>`Uyarı: ${x}`)];$("periodResult").textContent=lines.join("\n");}
  catch(e){$("periodResult").textContent=e.message;}
}

function settingsRow(primary,secondary,action){const row=document.createElement("div");row.className="settings-row";const a=document.createElement("strong");a.textContent=primary;const b=document.createElement("span");b.textContent=secondary;row.append(a,b);if(action)row.append(action);return row;}

async function loadGovernance(){
  const [rules,mappings,bank]=await Promise.all([jsonRequest("/api/onay-kurallari"),jsonRequest(`/api/hesap-eslemeleri?faaliyet=${encodeURIComponent($("faaliyet").value==="tum"?"":$("faaliyet").value)}`),jsonRequest("/api/banka-mutabakat?durum=ESLESMEDI")]);
  const rh=$("approvalRules");rh.replaceChildren();rules.kurallar.forEach(x=>rh.appendChild(settingsRow(`${x.kural_id} · ${x.gerekli_onay} onay`,`${x.faaliyet_key} · ${money.format(x.min_tutar)}+ · ${x.gerekli_roller.join(", ")}`)));
  const mh=$("accountMappings");mh.replaceChildren();mappings.eslemeler.slice(0,30).forEach(x=>mh.appendChild(settingsRow(`${x.faaliyet_key} · ${x.yerel_hesap_kodu}`,`${x.grup_hesap_kodu} ${x.ad}`)));
  const bh=$("bankReconciliation");bh.replaceChildren();if(!bank.hareketler.length){const p=document.createElement("p");p.className="empty";p.textContent="Bekleyen banka hareketi yok.";bh.appendChild(p);}bank.hareketler.forEach(x=>{const inspect=button("Adayları bul","",async()=>showBankCandidates(x,inspect.parentElement));bh.appendChild(settingsRow(`${x.tarih} · ${x.aciklama||"Açıklama yok"}`,money.format(x.tutar),inspect));});
}

async function saveApprovalRule(event){
  event.preventDefault();const roles=$("approvalRoles").value.split(",").map(x=>x.trim().toUpperCase()).filter(Boolean);
  await jsonRequest("/api/onay-kurali",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kural_id:$("approvalRuleId").value,faaliyet:$("approvalActivity").value,min_tutar:$("approvalMin").value,max_tutar:$("approvalMax").value||null,gerekli_onay:$("approvalCount").value,roller:roles,ayri_hazirlayan:$("approvalSeparation").checked})});
  $("approvalRuleId").value="";$("approvalMin").value="";$("approvalMax").value="";await loadGovernance();
}

async function saveMapping(event){
  event.preventDefault();await jsonRequest("/api/hesap-esleme",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({faaliyet:$("mappingActivity").value,yerel_hesap:$("mappingLocal").value,grup_hesap:$("mappingGroup").value,aciklama:"Dashboard eşlemesi"})});$("mappingLocal").value="";$("mappingGroup").value="";await loadGovernance();
}

async function showBankCandidates(movement,row){
  row.querySelectorAll(".bank-candidate").forEach(x=>x.remove());const data=await jsonRequest(`/api/banka-adaylari?hareket_id=${encodeURIComponent(movement.hareket_id)}`);
  data.adaylar.forEach(x=>{const candidate=document.createElement("div");candidate.className="bank-candidate";candidate.textContent=`${x.skor}/100 · ${x.tarih} · ${x.cari||x.aciklama} · ${money.format(x.tutar)}`;const match=button("Eşleştir","",async()=>{await jsonRequest("/api/banka-eslestir",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({hareket_id:movement.hareket_id,fis_id:x.fis_id})});await loadGovernance();await loadDashboard();});candidate.appendChild(match);row.after(candidate);});
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
    const parola=await requestApprovalPassword();if(parola===null)return;
    await jsonRequest(`/api/proje-kalemi/${encodeURIComponent(id)}/guncelle`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({degisiklikler:changes,parola})});
    $("projectDialog").close(); await Promise.all([loadProjectItems(),loadDashboard()]); addMessage(`${id} iş kalemi güncellendi. Ana Excel ve finans defteri eşitlendi.`,"assistant");
  }catch(e){$("projectError").textContent=e.message;}finally{$("projectSave").disabled=false;}
}

function addMessage(text,who,extra=""){ const el=document.createElement("div"); el.className=`message ${who} ${extra}`; el.textContent=text; $("chat").appendChild(el); $("chat").scrollTop=$("chat").scrollHeight; return el; }
function renderEvidence(data){
  const box=$("aiEvidence");box.replaceChildren();const items=[...(data.kanitlar||[]),...(data.aksiyonlar||[]).map(x=>`Aksiyon: ${x}`)];box.hidden=!items.length;if(!items.length)return;const h=document.createElement("h4");h.textContent="Kullanılan kanıtlar ve aksiyonlar";box.appendChild(h);items.slice(0,7).forEach(x=>{const row=document.createElement("div");row.className="evidence-item";row.textContent=x;box.appendChild(row);});
}
function renderChatDraft(doc){
  if(!doc)return; state.activeDraftToken=doc.token;
  const card=document.createElement("article");card.className="chat-draft";
  const head=document.createElement("div");head.className="chat-draft-head";
  const title=document.createElement("strong");title.textContent="Onay bekleyen kayıt taslağı";
  const validation=document.createElement("span");validation.className=`validation ${doc.dogrulama?.durum||"FAIL"}`;validation.textContent=`${doc.dogrulama?.durum||"FAIL"} ${doc.dogrulama?.skor||0}/100`;head.append(title,validation);card.appendChild(head);
  (doc.kayitlar||[]).forEach(x=>{const row=document.createElement("div");row.className="chat-draft-record";row.textContent=`${x.tarih||"Tarih eksik"} · ${x.proje||"Proje yok"} · ${x.cari||"Cari eksik"} · ${x.islem||"İşlem eksik"} · ${x.tutar!==null&&x.tutar!==""?formatAmount(x.tutar,x.pb):"Tutar eksik"} · ${x.hesap_kasa||"Hesap/kasa eksik"}`;card.appendChild(row);(x.otomatik_eslesmeler||[]).forEach(m=>{const auto=document.createElement("div");auto.className="draft-auto-match";auto.textContent=`Otomatik eşleşme: ${m}`;card.appendChild(auto);});});
  [...(doc.dogrulama?.hatalar||[]),...(doc.dogrulama?.uyarilar||[])].slice(0,4).forEach(x=>{const w=document.createElement("div");w.className="draft-warning";w.textContent=x;card.appendChild(w);});
  if(doc.durum==="ONAY_BEKLIYOR"){
    const actions=document.createElement("div");actions.className="chat-draft-actions";
    const approve=button("Şifreyle Onayla","approve",()=>chatDraftAction(doc.token,"onay",card));approve.disabled=doc.dogrulama?.durum==="FAIL";
    actions.append(approve,button("Düzelt","",()=>{$("soru").value="Düzelt: ";$("soru").focus();}),button("İptal","reject",()=>chatDraftAction(doc.token,"reddet",card)));card.appendChild(actions);
  }
  $("chat").appendChild(card);$("chat").scrollTop=$("chat").scrollHeight;
}
async function loadPendingChatDraft(){
  const data=await jsonRequest("/api/sohbet-taslaklari");
  const pending=(data.taslaklar||[]).find(x=>x.durum==="ONAY_BEKLIYOR");
  if(pending&&!state.activeDraftToken){
    addMessage("Daha önce hazırlanan ve henüz Excel'e yazılmamış taslak aşağıdadır.","assistant");
    renderChatDraft(pending);
  }
}
async function chatDraftAction(token,action,card=null){
  try{
    const parola=action==="onay"?await requestApprovalPassword():null;if(action==="onay"&&parola===null)return;
    const result=await jsonRequest(`/api/belge/${token}/${action}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(action==="onay"?{parola}:{})});
    state.activeDraftToken=null;
    addMessage(action==="onay"?"Kayıt onaylandı; master Excel ve merkez finans defteri güncellendi.":"Taslak iptal edildi; Excel'e yazılmadı.","assistant");
    await Promise.all([loadDashboard(),loadProjectItems(),loadDocuments()]);
  }catch(e){addMessage(`Kayıt işlemi başarısız: ${e.message}`,"assistant");}
}
async function ask(question,parola=null,tekrar=false){
  const q=(question||$("soru").value).trim();if(!q)return;
  if(!tekrar){$("soru").value="";addMessage(q,"user");}
  const loading=addMessage("Defter, vadeler ve risk göstergeleri analiz ediliyor…","assistant","loading");$("gonder").disabled=true;setAIStatus("Yerel model analiz ediyor",true);$("aiEvidence").hidden=true;
  try{
    const d=await jsonRequest("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({soru:q,faaliyet:$("faaliyet").value,profil:profile(),llm:true,onceki_niyet:state.lastIntent,onceki_soru:state.lastContextQuestion,aktif_taslak:state.activeDraftToken,parola})});
    loading.remove();addMessage(d.cevap,"assistant");if(d.kayit_taslagi)renderChatDraft(d.kayit_taslagi);if(d.taslak_kapat)state.activeDraftToken=null;renderEvidence(d);state.lastIntent=d.niyet||null;state.lastContextQuestion=d.baglam_sorusu||q;
    if(d.degisiklik_yapildi)await Promise.all([loadDashboard(),loadProjectItems()]);
    if(d.parola_gerekli&&d.kayit_taslagi_token)await chatDraftAction(d.kayit_taslagi_token,"onay");
    else if(d.parola_gerekli&&d.tekrar_sorusu){const yeniParola=await requestApprovalPassword();if(yeniParola!==null)await ask(d.tekrar_sorusu,yeniParola,true);}
    $("aiConfidence").textContent=`${d.guven||"—"} güven`;const durum=d.llm==="evren_api"?"EVREN API ile yanıtlandı":(d.llm==="yerel_ollama"?"Yerel Ollama ile yanıtlandı":"Deterministik motorla yanıtlandı");setAIStatus(d.degisiklik_yapildi?"Değişiklik doğrulandı":durum);
  }catch(e){loading.textContent=`Yanıt hazırlanamadı: ${e.message}`;loading.classList.remove("loading");}
  finally{$("gonder").disabled=false;if($("aiStatus").textContent.includes("analiz"))setAIStatus("Defter kanıtlarıyla hazır");}
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
  doc.kayitlar.forEach((x,i)=>{ const line=document.createElement("div"); line.className="doc-record"; const amount=x.tutar!==null&&x.tutar!==""?` · ${formatAmount(x.tutar,x.pb)}`:""; line.textContent=`${i+1}. ${x.tarih||"Tarih yok"} · ${x.cari||"Cari yok"}${amount}`; records.appendChild(line); }); card.appendChild(records);
  [...doc.dogrulama.uyarilar,...doc.dogrulama.hatalar].slice(0,3).forEach(x=>{ const w=document.createElement("div"); w.className="warning"; w.textContent=x; card.appendChild(w); });
  if(doc.durum==="ONAY_BEKLIYOR"){
    const actions=document.createElement("div"); actions.className="doc-actions";
    actions.append(button("Şifreyle Onayla","approve",()=>documentAction(doc.token,"onay")),button("Düzelt","",()=>toggleCorrection(card,doc.token)),button("Reddet","reject",()=>documentAction(doc.token,"reddet"))); card.appendChild(actions);
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
  try{ const parola=action==="onay"?await requestApprovalPassword():null;if(action==="onay"&&parola===null)return;await jsonRequest(`/api/belge/${token}/${action}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(action==="onay"?{parola}:{})});addMessage(action==="onay"?"Belge Excel ve merkez finans defterine işlendi.":"Belge reddedildi; Excel'e yazılmadı.","assistant");await Promise.all([loadDocuments(),loadDashboard()]); }
  catch(e){addMessage(`Belge işlemi başarısız: ${e.message}`,"assistant");}
}

async function uploadDocument(event){
  event.preventDefault(); const file=$("dosya").files[0]; if(!file)return; const form=new FormData(); form.append("dosya",file); form.append("firma",$("uploadFirma").value); $("uploadButton").disabled=true; $("uploadStatus").textContent="Belge yapay zekâ ve doğrulama katmanı tarafından inceleniyor…";
  try{ const doc=await jsonRequest("/api/upload",{method:"POST",body:form}); $("uploadStatus").textContent=`${doc.firma} için ${doc.dogrulama.durum} sonucu alındı. Onayınızı bekliyor.`; $("uploadForm").reset(); await loadDocuments(); addMessage(`${doc.dosya} okundu. Doğrulama: ${doc.dogrulama.durum} (${doc.dogrulama.skor}/100). Excel'e yazmadan önce kararınızı bekliyorum.`,"assistant"); }
  catch(e){$("uploadStatus").textContent=`Belge işlenemedi: ${e.message}`;}
  finally{$("uploadButton").disabled=false;}
}

async function loadRemoteUsers(){ const data=await jsonRequest("/api/remote/users"); const list=$("remoteUsers"); list.replaceChildren(); if(!data.kullanicilar.length){const s=document.createElement("span");s.textContent="Henüz kullanıcı yok.";list.appendChild(s);} data.kullanicilar.forEach(x=>{const row=document.createElement("div");row.className="remote-user";const name=document.createElement("span");name.textContent=x;row.append(name,button("Yetkiyi kaldır","",async()=>{await jsonRequest(`/api/remote/users/${encodeURIComponent(x)}`,{method:"DELETE"});await loadRemoteUsers();}));list.appendChild(row);}); }

async function openExcelCenter(){
  const d=await jsonRequest("/api/excel/bilgi");
  $("excelFileName").textContent=d.ad;$("excelFilePath").textContent=d.yol;
  $("excelOpen").hidden=!d.yerel;$("excelStatus").textContent=d.mevcut?`${(Number(d.boyut||0)/1024/1024).toLocaleString("tr-TR",{maximumFractionDigits:1})} MB · erişime hazır`:"Ana Excel bulunamadı";
  $("excelDialog").showModal();
}

async function openExcelLocally(){
  $("excelOpen").disabled=true;
  try{const d=await jsonRequest("/api/excel/ac",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});$("excelStatus").textContent=d.mesaj;}
  catch(e){$("excelStatus").textContent=e.message;}
  finally{$("excelOpen").disabled=false;}
}

document.addEventListener("DOMContentLoaded",async()=>{
  $("headerDate").textContent=new Intl.DateTimeFormat("tr-TR",{day:"2-digit",month:"long",year:"numeric"}).format(new Date());
  $("pairForm").addEventListener("submit",async e=>{e.preventDefault();try{await jsonRequest("/api/session/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kod:$("pairCode").value})});location.reload();}catch(err){$("pairError").textContent=err.message;}});
  if(!await checkSession())return;
  try{ await loadActivities(); await Promise.all([loadDashboard(),loadDocuments(),loadProjectItems(),loadPendingChatDraft()]); }catch(e){ $("anaKarar").textContent=`Veri yüklenemedi: ${e.message}`; }
  $("faaliyet").addEventListener("change",()=>{state.lastIntent=null;state.lastContextQuestion=null;return Promise.all([loadDashboard(),loadProjectItems()]).catch(e=>addMessage(e.message,"assistant"));});
  ["risk","rezerv","vade"].forEach(id=>$(id).addEventListener("change",()=>loadDashboard().catch(e=>addMessage(e.message,"assistant"))));
  $("chatForm").addEventListener("submit",e=>{e.preventDefault();ask();}); document.querySelectorAll("[data-question]").forEach(b=>b.addEventListener("click",()=>ask(b.dataset.question)));
  $("scenarioForm").addEventListener("submit",runScenario);
  $("uploadForm").addEventListener("submit",uploadDocument); $("dosya").addEventListener("change",()=>{const f=$("dosya").files[0];if(f)$("uploadStatus").textContent=`Seçilen dosya: ${f.name}`;});
  $("remoteButton").addEventListener("click",async()=>{await loadRemoteUsers();$("remoteDialog").showModal();}); $("remoteDialog").querySelector(".dialog-close").addEventListener("click",()=>$("remoteDialog").close());
  $("excelButton").addEventListener("click",()=>openExcelCenter().catch(e=>addMessage(`Excel merkezi açılamadı: ${e.message}`,"assistant")));$("excelDialog").querySelector(".dialog-close").addEventListener("click",()=>$("excelDialog").close());$("excelOpen").addEventListener("click",openExcelLocally);
  $("projectDialog").querySelector(".dialog-close").addEventListener("click",()=>$("projectDialog").close()); $("projectForm").addEventListener("submit",saveProjectItem);
  const today=new Date().toISOString().slice(0,10);$("transferDate").value=today;$("fxDate").value=today;$("periodYear").value=new Date().getFullYear();$("periodMonth").value=new Date().getMonth()+1;
  $("transferButton").addEventListener("click",()=>$("transferDialog").showModal());$("transferForm").addEventListener("submit",saveTransfer);
  $("fxButton").addEventListener("click",()=>$("fxDialog").showModal());$("fxForm").addEventListener("submit",saveFx);$("fxRevalue").addEventListener("click",runFxRevaluation);
  $("periodButton").addEventListener("click",()=>$("periodDialog").showModal());$("periodForm").addEventListener("submit",checkPeriod);
  $("governanceButton").addEventListener("click",async()=>{try{await loadGovernance();$("governanceDialog").showModal();}catch(e){addMessage(`Yönetim ayarları yüklenemedi: ${e.message}`,"assistant");}});
  $("approvalRuleForm").addEventListener("submit",e=>saveApprovalRule(e).catch(err=>addMessage(`Onay kuralı kaydedilemedi: ${err.message}`,"assistant")));
  $("mappingForm").addEventListener("submit",e=>saveMapping(e).catch(err=>addMessage(`Hesap eşlemesi kaydedilemedi: ${err.message}`,"assistant")));
  ["transferDialog","fxDialog","periodDialog","governanceDialog"].forEach(id=>$(id).querySelector(".dialog-close").addEventListener("click",()=>$(id).close()));
  $("generateCode").addEventListener("click",async()=>{try{const d=await jsonRequest("/api/remote/code",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});$("remoteCode").textContent=d.kod;}catch(e){$("remoteCode").textContent=e.message;}});
  ["dragenter","dragover"].forEach(n=>$("dropZone").addEventListener(n,e=>{e.preventDefault();$("dropZone").classList.add("drag");})); ["dragleave","drop"].forEach(n=>$("dropZone").addEventListener(n,e=>{$("dropZone").classList.remove("drag");}));
});
