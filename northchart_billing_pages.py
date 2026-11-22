"""Pricing, billing and receipt pages. They share the account pages' design (northchart_auth_pages)."""

from __future__ import annotations

import html
import json
import os
import time

import northchart_auth_pages as ap

CSS = """
<style>
.wrap3{width:100%;max-width:1060px;margin:0 auto;padding:26px 16px 70px}
.hero{text-align:center;margin:10px auto 22px;max-width:640px}
.hero h1{font:700 30px var(--nc-font);letter-spacing:-.5px;margin-bottom:8px}
.hero p{color:var(--dim);font-size:14.5px;line-height:1.6}
.seg{display:inline-flex;gap:2px;padding:3px;border:1px solid var(--bd2);border-radius:10px;background:var(--p2);margin-top:14px}
.seg button{font:600 13px var(--nc-font);padding:7px 16px;border:0;border-radius:8px;background:transparent;color:var(--dim);cursor:pointer}
.seg button.on{background:var(--p1);color:var(--tx);box-shadow:0 1px 3px rgba(16,24,40,.12)}
.seg .save{font:700 10px var(--nc-font);color:var(--g);background:var(--gt);padding:2px 6px;border-radius:999px;margin-left:6px}
.plans{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;align-items:stretch}
@media(max-width:860px){.plans{grid-template-columns:1fr}}
.plan{position:relative;display:flex;flex-direction:column;background:var(--p1);border:1px solid var(--bd);border-radius:14px;box-shadow:var(--shadow);padding:22px 20px}
.plan.hi{border:2px solid var(--tx)}
.plan .flag{position:absolute;top:-11px;left:20px;font:700 10px var(--nc-font);letter-spacing:.6px;text-transform:uppercase;background:var(--tx);color:var(--bg);padding:3px 9px;border-radius:999px}
.plan h3{font:700 17px var(--nc-font);margin-bottom:2px}.plan .tag{color:var(--dim);font-size:12.5px;min-height:18px}
.price{margin:16px 0 4px;display:flex;align-items:baseline;gap:6px}.price b{font:700 34px var(--nc-font);letter-spacing:-1px}
.price span{color:var(--dim);font-size:13px}.kes{color:var(--dim);font-size:12px;min-height:16px}
.plan ul{list-style:none;padding:0;margin:16px 0 18px;flex:1}
.plan li{font-size:13px;line-height:1.45;padding:5px 0 5px 22px;position:relative}
.plan li:before{content:"";position:absolute;left:2px;top:9px;width:10px;height:6px;border-left:2px solid var(--g);border-bottom:2px solid var(--g);transform:rotate(-45deg)}
.plan .go{display:block;width:100%;text-align:center;font:600 14px var(--nc-font);padding:11px;border-radius:9px;border:1px solid var(--bd2);background:var(--p2);color:var(--tx);cursor:pointer;text-decoration:none}
.plan.hi .go{background:var(--tx);color:var(--bg);border-color:var(--tx)}
.plan .go[disabled]{opacity:.55;cursor:default}
.pay{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin:18px 0 0;color:var(--dim);font-size:12.5px}
.pay span{display:inline-flex;align-items:center;gap:6px}.dot{width:7px;height:7px;border-radius:50%;background:var(--g)}
.faq{max-width:760px;margin:34px auto 0}.faq details{border-bottom:1px solid var(--bd);padding:12px 2px}
.faq summary{cursor:pointer;font:600 14px var(--nc-font)}.faq p{color:var(--dim);font-size:13px;line-height:1.6;margin-top:8px}
.cur{display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.cur .big{font:700 22px var(--nc-font)}.cur .meta{color:var(--dim);font-size:12.5px;line-height:1.6}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:6px 0 14px}
@media(max-width:700px){.kpis{grid-template-columns:1fr 1fr}}
.kpi{border:1px solid var(--bd);border-radius:10px;padding:12px;background:var(--p2)}
.kpi b{display:block;font:700 19px var(--nc-font)}.kpi span{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.7px}
.co{border:1px solid var(--bd);border-radius:12px;padding:18px;background:var(--p2)}
.co .sum{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:14px;margin-bottom:14px}
.co .sum b{font-size:18px}
.tabs{display:flex;gap:8px;margin-bottom:14px}
.tabs button{flex:1;font:600 13.5px var(--nc-font);padding:12px;border:1px solid var(--bd2);border-radius:10px;background:var(--p1);color:var(--tx);cursor:pointer;text-align:left}
.tabs button small{display:block;font-weight:500;color:var(--dim);font-size:11.5px;margin-top:2px}
.tabs button.on{border:2px solid var(--tx);padding:11px}
.wait{display:flex;gap:14px;align-items:center;padding:14px;border-radius:10px;background:var(--p1);border:1px solid var(--bd)}
.spin{width:26px;height:26px;border-radius:50%;border:3px solid var(--bd2);border-top-color:var(--g);animation:sp .9s linear infinite;flex:none}
@keyframes sp{to{transform:rotate(360deg)}}
.testbar{background:var(--at);color:var(--a);font:600 12px var(--nc-font);text-align:center;padding:7px;border-radius:8px;margin-bottom:14px}
.rcpt{max-width:620px;margin:30px auto;background:var(--p1);border:1px solid var(--bd);border-radius:12px;padding:30px}
.rcpt h1{font:700 22px var(--nc-font)}.rcpt table{width:100%;border-collapse:collapse;margin-top:18px;font-size:13.5px}
.rcpt td{padding:8px 0;border-bottom:1px solid var(--bd)}.rcpt td:last-child{text-align:right}
.paid{font:700 11px var(--nc-font);letter-spacing:.8px;color:var(--g);background:var(--gt);padding:3px 9px;border-radius:999px}
@media print{.nc-head,.noprint{display:none!important}body{background:#fff}.rcpt{border:0;margin:0}}
</style>"""

PLANS_JS = r"""<script>
const escH=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtUsd=v=>"$"+(+v).toLocaleString("en-US",{maximumFractionDigits:2});
const fmtKes=v=>"KES "+(+v).toLocaleString("en-GB");
const day=t=>t?new Date(t*1000).toLocaleDateString("en-GB",{day:"numeric",month:"short",year:"numeric"}):"";
let CAT=null; const IV="month";
function drawPlans(current, onPick){
  const el=$("plans"); if(!el||!CAT) return;
  el.innerHTML=CAT.plans.map(p=>{
    const per="/month", usd=p.usd[IV], kes=p.kes[IV];
    const mine=current&&current===p.id;
    const label=p.id==="free"?(mine?"Your plan":current?"Included in every plan":"Start free"):(mine?"Your plan":"Choose "+p.name);
    const note=p.id==="free"?"No card needed":`About ${fmtKes(kes)} by M-Pesa`;
    return `<div class="plan${p.id==="plus"?" hi":""}">${p.id==="plus"?'<span class="flag">Most traders pick this</span>':""}
      <h3>${escH(p.name)}</h3><div class="tag">${escH(p.tagline)}</div>
      <div class="price"><b>${usd?fmtUsd(usd):"$0"}</b><span>${usd?per:"forever"}</span></div><div class="kes">${note}</div>
      <ul>${p.features.map(f=>`<li>${escH(f)}</li>`).join("")}</ul>
      <button class="go" data-plan="${p.id}" ${mine||(p.id==="free"&&current)?"disabled":""}>${label}</button></div>`}).join("");
  el.querySelectorAll("button[data-plan]").forEach(b=>b.onclick=()=>onPick(b.dataset.plan));
}
</script>"""

SEG = '<p class="lead" style="margin-top:12px">Monthly plans. Cancel any time.</p>'

FAQ = """<div class="faq">
<details><summary>How do I pay with M-Pesa?</summary><p>Enter your Safaricom number and press Send payment request.
A prompt opens on your phone; enter your M-Pesa PIN and the plan switches on within seconds. Prices are set in US dollars
and charged in shillings at the day's rate, shown before you pay.</p></details>
<details><summary>Does it renew by itself?</summary><p>Cards renew automatically. With M-Pesa you choose: a payment
prompt on the renewal day, or an M-Pesa Ratiba standing order you approve once. We email you three days before every renewal.</p></details>
<details><summary>Can I cancel?</summary><p>Yes, any time from this page. You keep the plan until the end of the period you
paid for, then the account moves to Free. Your journal and data stay.</p></details>
<details><summary>Can I upgrade or downgrade?</summary><p>Upgrading starts straight away and the unused part of your current
plan is taken off the price. Downgrading takes effect when the current period ends.</p></details>
<details><summary>Do you see my card or M-Pesa PIN?</summary><p>No. Card payments run on Paystack's secure page and M-Pesa
asks for your PIN on your own phone. Northchart only keeps the last four digits of the card and a masked phone number.</p></details>
<details><summary>Is the desk investment advice?</summary><p>No. Northchart is research software: desk calls are model
output for your own analysis, free on every plan, and Northchart never places trades. See the <a href="/legal/risk">risk notice</a>.</p></details>
</div>"""


def pricing(signed_in: bool, catalogue: dict):
    inner = f"""<main class="wrap3">{CSS}
<div class="hero"><h1>Trade with a desk behind you</h1>
<p>Desk calls and the chart are free for everyone. Plus and Pro add the tools that save you hours: a journal that fills
itself from your broker, the agent desk, and Northchart inside ChatGPT and Claude.</p>{SEG}</div>
<div class="plans" id="plans"></div>
<div class="pay"><span><i class="dot"></i>Lipa na M-Pesa</span><span><i class="dot"></i>Visa and Mastercard</span>
<span><i class="dot"></i>Cancel any time</span></div>{FAQ}</main>"""
    script = PLANS_JS + f"""<script>
CAT={json.dumps(catalogue)};
const SIGNED={json.dumps(bool(signed_in))};
function pick(p){{ if(p==="free"){{ location.href=SIGNED?"/":"/signup"; return }}
  const to="/billing?plan="+p; location.href=SIGNED?to:"/signup?next="+encodeURIComponent(to) }}
const redraw=()=>drawPlans(null,pick); redraw();
</script>"""
    return ap.page("Pricing", inner, script, nav="billing" if signed_in else None)


def billing():
    inner = f"""<main class="wrap3">{CSS}
<div id="test" class="testbar" hidden>Test mode: use Safaricom sandbox numbers and Paystack test cards. No real money moves.</div>
<div id="welcome" class="msg"></div>
<section class="sec" id="curSec"><h2>Your plan</h2><div class="cur" id="cur"><span class="lead">Loading…</span></div>
  <div class="row" id="curAct" style="margin-top:14px"></div>
  <div id="auto" style="margin-top:14px" hidden></div></section>

<section class="sec" id="pickSec"><div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
  <div><h2>Plans</h2><p class="lead" style="margin:0">Desk calls stay free on every plan. Monthly prices in US dollars; M-Pesa charges the shilling amount shown.</p></div></div>
  <div class="plans" id="plans" style="margin-top:18px"></div></section>

<section class="sec" id="coSec" hidden><h2 id="coTitle">Checkout</h2>
  <div class="co"><div class="sum" id="coSum"></div>
  <div class="tabs" id="coTabs"><button data-m="mpesa" id="tMpesa">Lipa na M-Pesa<small>Prompt on your phone</small></button>
  <button data-m="card" id="tCard">Card<small>Visa or Mastercard, renews by itself</small></button></div>
  <div id="coMpesa"><div class="row"><div class="f"><label for="phone">M-Pesa number</label>
    <input id="phone" inputmode="tel" autocomplete="tel" placeholder="0712 345 678"></div>
    <button class="btn2" id="payM" style="height:41px;background:var(--tx);color:var(--bg);border-color:var(--tx)">Send payment request</button></div></div>
  <div id="coCard" hidden><button class="btn2" id="payC" style="height:41px;background:var(--tx);color:var(--bg);border-color:var(--tx)">Continue to secure card payment</button>
    <p class="lead" style="margin:8px 0 0">You will pay on Paystack's page and come straight back here.</p></div>
  <div id="coWait" hidden style="margin-top:14px"></div></div></section>

<section class="sec" id="histSec"><h2>Payments and receipts</h2><table class="t" id="hist"></table></section>

<section class="sec" id="ownSec" hidden><h2>Desk revenue</h2><p class="lead">Only you see this. Members never see each other.</p>
  <div class="kpis" id="kpis"></div>
  <table class="t" id="subs"></table>
  <h2 style="margin-top:18px">Give a member a plan</h2><p class="lead">For testers, partners or a refund. It ends by itself.</p>
  <div class="row"><div class="f"><label>Member</label><select id="gUid"></select></div>
    <div class="f" style="max-width:150px"><label>Plan</label><select id="gPlan"><option value="plus">Plus</option><option value="pro">Pro</option><option value="free">Free (remove)</option></select></div>
    <div class="f" style="max-width:120px"><label>Days</label><input id="gDays" type="number" value="30" min="1" max="730"></div>
    <button class="btn2" id="gGo">Give plan</button></div>
  <h2 style="margin-top:18px">Payment setup</h2><div id="setup" class="lead"></div>
  <div class="row"><button class="btn2" id="c2b">Register M-Pesa confirmation address</button></div></section>
<div id="msg" class="msg" role="alert" style="position:fixed;bottom:14px;right:14px;max-width:380px;z-index:9"></div>
</main>"""
    script = PLANS_JS + r"""<script>
function flash(t,ok){ show($("msg"),t,ok); clearTimeout(flash.h); flash.h=setTimeout(()=>$("msg").className="msg",4200) }
let ST=null, PICK=null, METHOD="mpesa", POLL=null;
const qs=new URLSearchParams(location.search);
const method=m=>({mpesa:"M-Pesa",card:"Card",comp:"Given by the desk",credit:"Plan credit"}[m]||m||"");
async function load(){
  const r=await fetch("/api/billing/state"); if(r.status===401){ location.href="/signin?next=/billing"; return }
  ST=await r.json(); CAT=ST.catalogue;
  $("test").hidden=!(CAT.test&&CAT.mode!=="off");
  drawCurrent(); drawHist(); redraw();
  if(ST.owner) loadOwner();
  if(!PICK&&!load.once&&qs.get("plan")&&qs.get("plan")!=="free"&&!ST.owner&&qs.get("plan")!==ST.plan) choose(qs.get("plan"));
  load.once=true;
}
function drawCurrent(){
  const s=ST.sub, p=ST.plan;
  const pn={free:"Free",plus:"Plus",pro:"Pro"}[p];
  let meta="";
  if(ST.owner) meta="You own this desk, so every feature is on. Members pay through this page.";
  else if(CAT.mode==="off") meta="Payments are not switched on for this desk yet, so every feature is open.";
  else if(!s||p==="free") meta="Desk calls, the chart and your manual journal. Upgrade for automatic sync and the agent desk.";
  else{
    const ends=day(s.period_end);
    meta=s.comp?`Given by the desk until ${ends}.`:
      s.status==="past_due"?`<span style="color:var(--r)">Renewal due since ${ends}.</span> Pay within ${CAT.grace_days} days to keep ${pn}.`:
      s.cancel_at_period_end?`Ends on ${ends}. It will not renew.`:
      `Monthly · renews on ${ends}`;
    if(s.next_plan) meta+=` · moves to ${({plus:"Plus",pro:"Pro"})[s.next_plan]} on ${ends}`;
    const how=s.card&&s.method==="card"?`${escH(s.card.brand)} ending ${escH(s.card.last4)}`:
      s.ratiba&&s.ratiba.state==="active"?`M-Pesa Ratiba from ${escH(s.phone_masked)}`:
      s.phone_masked?`M-Pesa prompt to ${escH(s.phone_masked)}`:method(s.method);
    if(!s.comp) meta+=`<br>Renewal: ${how}`;
  }
  const used=ST.usage&&ST.usage.agent!=null?` · ${ST.usage.agent} of ${ST.limits.agent_day} agent questions today`:"";
  $("cur").innerHTML=`<div class="big">${pn}</div><span class="badge ${p==="free"?"mut":"ok"}">${p==="free"?"Free":"Active"}</span>
    <div class="meta" style="flex:1;min-width:260px">${meta}${ST.owner?"":used}</div>`;
  const act=[];
  if(s&&!s.comp&&s.status==="active"&&!ST.owner){
    act.push(s.cancel_at_period_end?'<button class="btn2" id="resume">Keep my plan</button>':'<button class="btn2 danger" id="cancel">Cancel renewal</button>');
    act.push('<button class="btn2" id="autoBtn">How renewals are paid</button>');
  }
  if(s&&s.status==="past_due") act.push(`<button class="btn2" id="payNow" style="background:var(--tx);color:var(--bg)">Pay now</button>`);
  $("curAct").innerHTML=act.join("");
  const on=(id,fn)=>{ const b=$(id); if(b) b.onclick=async()=>{ try{ await fn() }catch(e){ flash(e.message) } } };
  on("cancel",async()=>{ if(!confirm("Stop the renewal? You keep "+pn+" until "+day(s.period_end)+".")) return;
    await post("/api/billing/cancel"); flash("Renewal cancelled. You keep the plan until "+day(s.period_end)+".",true); load() });
  on("resume",async()=>{ await post("/api/billing/resume"); flash("Your plan will renew.",true); load() });
  on("payNow",async()=>{ choose(s.plan) });
  on("autoBtn",async()=>{ drawAuto() });
  $("pickSec").hidden=!!ST.owner; $("histSec").hidden=!!ST.owner;
}
function drawAuto(){
  const s=ST.sub, a=$("auto"); a.hidden=false;
  a.innerHTML=`<div class="co"><p class="lead" style="margin:0 0 10px">Pick how the next renewal is paid.</p>
   <div class="tabs"><button data-a="card" ${s.card?"":"disabled"}>Saved card<small>${s.card?escH(s.card.brand)+" ending "+escH(s.card.last4):"Pay once by card to save one"}</small></button>
   <button data-a="mpesa">M-Pesa prompt<small>We send a payment request on the day</small></button>
   <button data-a="ratiba">M-Pesa Ratiba<small>Standing order you approve once</small></button></div>
   <div class="row"><div class="f"><label>M-Pesa number</label><input id="aPhone" inputmode="tel" placeholder="${escH(s.phone_masked||"0712 345 678")}"></div>
   <button class="btn2" id="aSave">Save</button></div><p class="lead" style="margin:8px 0 0" id="aNote"></p></div>`;
  let pickA=s.ratiba&&s.ratiba.state==="active"?"ratiba":(s.method==="card"&&s.card?"card":"mpesa");
  const mark=()=>a.querySelectorAll("button[data-a]").forEach(b=>b.classList.toggle("on",b.dataset.a===pickA));
  a.querySelectorAll("button[data-a]").forEach(b=>b.onclick=()=>{ pickA=b.dataset.a; mark();
    $("aNote").textContent=pickA==="ratiba"?"M-Pesa sends a prompt to approve the standing order. Each renewal then pays itself on the due date (Safaricom charges KES 5 per payment).":"" });
  mark();
  $("aSave").onclick=async()=>{ try{ await post("/api/billing/autopay",{method:pickA,phone:$("aPhone").value});
    flash(pickA==="ratiba"?"Approve the standing order on your phone.":"Saved.",true); a.hidden=true; load() }catch(e){ flash(e.message) } };
}
function drawHist(){
  const h=ST.payments||[];
  $("hist").innerHTML=h.length?"<tr><th>Date</th><th>Plan</th><th>Method</th><th>Amount</th><th>Status</th><th></th></tr>"+h.map(p=>
    `<tr><td>${day(p.created)}</td><td>${escH(({plus:"Plus",pro:"Pro"})[p.plan]||p.plan)} · monthly</td>
    <td>${escH(method(p.method))}</td><td>${escH(p.currency)} ${(+p.amount).toLocaleString("en-GB")}</td>
    <td><span class="badge ${p.status==="paid"?"ok":p.status==="pending"?"warn":"mut"}">${escH(p.status)}</span>${p.note&&p.status!=="paid"?` <span class="lead" style="font-size:11.5px">${escH(p.note)}</span>`:""}</td>
    <td>${p.status==="paid"?`<a class="btn2" style="text-decoration:none" href="/billing/receipt?ref=${encodeURIComponent(p.ref)}" target="_blank">Receipt</a>`:""}</td></tr>`).join("")
    :'<tr><td class="lead">No payments yet.</td></tr>';
}
const redraw=()=>drawPlans(ST&&!ST.owner?ST.plan:null,p=>{ if(p!=="free") choose(p) });
async function choose(plan){
  PICK=plan; $("coSec").hidden=false;
  let q; try{ q=await (await fetch(`/api/billing/quote?plan=${plan}&interval=${IV}`)).json() }catch(e){ return flash("Could not price that plan.") }
  if(q.ok===false) return flash(q.error);
  const pn=({plus:"Plus",pro:"Pro"})[plan];
  $("coTitle").textContent=`Northchart ${pn}, monthly`;
  $("coSum").innerHTML=`<div>${fmtUsd(q.usd)} a month${q.credit?`<br><span class="lead" style="font-size:12px">Less ${fmtKes(q.credit)} for unused time on your current plan</span>`:""}</div>
    <div style="text-align:right">To pay now<br><b>${fmtKes(q.amount)}</b></div>`;
  $("tMpesa").disabled=!CAT.methods.mpesa; $("tCard").disabled=!CAT.methods.card;
  if(!CAT.methods[METHOD]) METHOD=CAT.methods.mpesa?"mpesa":"card";
  setMethod(METHOD); $("coWait").hidden=true;
  $("coSec").scrollIntoView({behavior:"smooth",block:"start"});
  if(!CAT.methods.mpesa&&!CAT.methods.card) $("coWait").hidden=false, $("coWait").innerHTML='<p class="lead">Payments are being set up. Check back soon.</p>';
}
function setMethod(m){ METHOD=m; $("tMpesa").classList.toggle("on",m==="mpesa"); $("tCard").classList.toggle("on",m==="card");
  $("coMpesa").hidden=m!=="mpesa"; $("coCard").hidden=m!=="card" }
$("tMpesa").onclick=()=>setMethod("mpesa"); $("tCard").onclick=()=>setMethod("card");
try{ const ph=localStorage.getItem("nc-mpesa"); if(ph) $("phone").value=ph }catch(e){}
$("payM").onclick=async()=>{ const b=$("payM"); b.disabled=true;
  try{ const j=await post("/api/billing/checkout",{plan:PICK,interval:IV,method:"mpesa",phone:$("phone").value});
    try{ localStorage.setItem("nc-mpesa",$("phone").value) }catch(e){}
    if(j.scheduled){ flash(j.message,true); load(); return }
    if(j.status==="paid"){ done(j.ref); return }
    waitFor(j.ref,j.message) }catch(e){ flash(e.message) } finally{ b.disabled=false } };
$("payC").onclick=async()=>{ const b=$("payC"); b.disabled=true;
  try{ const j=await post("/api/billing/checkout",{plan:PICK,interval:IV,method:"card"});
    if(j.scheduled){ flash(j.message,true); load(); return }
    if(j.status==="paid"){ done(j.ref); return }
    location.href=j.redirect }catch(e){ flash(e.message); b.disabled=false } };
function waitFor(ref,msg){
  const w=$("coWait"); w.hidden=false;
  w.innerHTML=`<div class="wait"><div class="spin"></div><div><b>${escH(msg||"Check your phone")}</b>
    <div class="lead" style="margin:2px 0 0">This page updates by itself. <a href="#" id="again">Didn't get the prompt?</a></div></div></div>`;
  $("again").onclick=e=>{ e.preventDefault(); clearInterval(POLL); w.hidden=true };
  const t0=Date.now(); clearInterval(POLL);
  POLL=setInterval(async()=>{
    let j={}; try{ j=await (await fetch("/api/billing/status?ref="+encodeURIComponent(ref))).json() }catch(e){}
    if(j.status==="paid"){ clearInterval(POLL); done(ref) }
    else if(j.status==="failed"||j.status==="abandoned"){ clearInterval(POLL);
      w.innerHTML=`<div class="wait" style="border-color:var(--r)"><div><b style="color:var(--r)">Payment not completed</b><div class="lead" style="margin:2px 0 0">${escH(j.note||"The request was cancelled or timed out.")} Try again when ready.</div></div></div>` }
    else if(Date.now()-t0>150000){ clearInterval(POLL);
      w.innerHTML='<div class="wait"><div><b>Still waiting for M-Pesa</b><div class="lead" style="margin:2px 0 0">If you paid, it will show here within a few minutes. Refresh to check.</div></div></div>' }
  },3000);
}
function done(ref){ $("coSec").hidden=true; PICK=null; history.replaceState(null,"","/billing"); show($("welcome"),"Payment received. Your plan is on. A receipt is in your email and below.",true); load(); scrollTo({top:0,behavior:"smooth"}) }
if(qs.get("paid")) show($("welcome"),"Payment received. Your plan is on.",true);
if(qs.get("payerr")) show($("welcome"),qs.get("payerr"));
async function loadOwner(){
  $("ownSec").hidden=false;
  const o=await (await fetch("/api/billing/admin")).json(); if(o.ok===false) return;
  const r=o.revenue_month||{};
  $("kpis").innerHTML=`<div class="kpi"><span>Paying members</span><b>${o.active}</b></div>
    <div class="kpi"><span>Monthly recurring</span><b>${fmtUsd(o.mrr_usd)}</b></div>
    <div class="kpi"><span>Collected this month</span><b>${fmtKes(r.KES||0)}${r.USD?" + "+fmtUsd(r.USD):""}</b></div>
    <div class="kpi"><span>Mode</span><b style="text-transform:capitalize">${escH(CAT.mode)}</b></div>`;
  $("subs").innerHTML=o.subs.length?"<tr><th>Member</th><th>Plan</th><th>Status</th><th>Until</th><th>Pays by</th></tr>"+o.subs.map(x=>
    `<tr><td>${escH(x.name||x.email)}<br><span class="lead" style="font-size:11px">${escH(x.email)}</span></td><td>${escH(({plus:"Plus",pro:"Pro"})[x.plan]||x.plan)} monthly</td>
    <td><span class="badge ${x.status==="active"?"ok":x.status==="past_due"?"warn":"mut"}">${escH(x.comp?"given":x.cancelling&&x.status==="active"?"ending":x.status)}</span></td><td>${day(x.period_end)}</td><td>${escH(method(x.method))}</td></tr>`).join("")
    :'<tr><td class="lead">No subscribers yet.</td></tr>';
  $("gUid").innerHTML=o.members.length?o.members.map(m=>`<option value="${escH(m.uid)}">${escH(m.email)}</option>`).join(""):'<option value="">No members yet</option>';
  const s=o.setup;
  const tick=(ok,t)=>`<div>${ok?'<span class="badge ok">Ready</span>':'<span class="badge warn">Missing</span>'} ${t}</div>`;
  $("setup").innerHTML=tick(CAT.mode!=="off",`Billing switched on (NORTHCHART_BILLING=${escH(CAT.mode)})`)+
    tick(s.daraja,`M-Pesa through Daraja (${escH(s.daraja_env)}, ${s.till?"Till "+escH(s.till):"Paybill "+escH(s.shortcode||"not set")})`)+
    tick(s.paystack,`Cards through Paystack${s.paystack_test?" (test keys)":""}, charged in ${escH(CAT.card_currency)}`)+
    tick(s.public_url,"Public address for payment callbacks (NORTHCHART_PUBLIC_URL)")+
    tick(s.signup_open,"Open sign-up so anyone can buy (NORTHCHART_SIGNUP=open)")+
    `<div style="margin-top:6px">Exchange rate used: 1 USD = KES ${CAT.rate}</div>`;
}
$("gGo").onclick=async()=>{ try{ await post("/api/billing/admin/grant",{uid:$("gUid").value,plan:$("gPlan").value,days:+$("gDays").value}); flash("Done.",true); loadOwner() }catch(e){ flash(e.message) } };
$("c2b").onclick=async()=>{ try{ await post("/api/billing/admin/register-c2b"); flash("Safaricom now sends Paybill and Ratiba payments to this desk.",true) }catch(e){ flash(e.message) } };
load();
</script>"""
    return ap.page("Plan and billing", inner, script, nav="billing")


def receipt(p: dict, u: dict):
    legal = os.environ.get("NORTHCHART_LEGAL_NAME") or "Northchart"
    pin = os.environ.get("NORTHCHART_KRA_PIN") or ""
    names = {"plus": "Plus", "pro": "Pro"}
    meth = {"mpesa": "M-Pesa", "card": "Card", "credit": "Plan credit", "comp": "Given by the desk"}.get(p["method"], p["method"])
    when = time.strftime("%d %B %Y, %H:%M", time.gmtime((p.get("paid_at") or p["created"]) + 3 * 3600)) + " EAT"
    e = html.escape
    rows = [("Receipt number", p["ref"]), ("Date", when), ("Billed to", (u.get("name") + ", " if u.get("name") else "") + u["email"]),
            ("Item", f"Northchart {names.get(p['plan'], p['plan'])}, one month"),
            ("Paid by", meth)]
    if p.get("receipt") and p["receipt"] != p["ref"]:
        rows.append(("M-Pesa receipt" if p["method"] == "mpesa" else "Payment reference", p["receipt"]))
    if p.get("credit"):
        rows.append(("Credit applied", f"KES {p['credit']:,}"))
    amount = f"{p['currency']} {p['amount']:,.2f}" if isinstance(p["amount"], float) else f"{p['currency']} {p['amount']:,}"
    inner = f"""{CSS}<div class="rcpt"><div style="display:flex;justify-content:space-between;align-items:flex-start">
<div><h1>Receipt</h1><p class="lead" style="margin-top:4px">{e(legal)}{' · PIN ' + e(pin) if pin else ''}</p></div><span class="paid">Paid</span></div>
<table>{''.join(f'<tr><td>{e(k)}</td><td>{e(str(v))}</td></tr>' for k, v in rows)}
<tr><td><b>Total</b></td><td><b>{e(amount)}</b></td></tr></table>
<p class="lead" style="margin-top:18px">Thank you for using Northchart. Questions about this payment: reply to the receipt email.</p>
<div class="noprint" style="margin-top:16px;display:flex;gap:8px"><button class="btn2" onclick="print()">Print or save as PDF</button>
<a class="btn2" href="/billing" style="text-decoration:none">Back to billing</a></div></div>"""
    return ap.page("Receipt " + p["ref"], inner, nav="billing")
