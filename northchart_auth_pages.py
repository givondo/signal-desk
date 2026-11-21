"""Pages for signing in, signing up, the owner's first-run setup, password reset and the account page.

Every page shares the Northchart design (northchart_ui). Forms post JSON to /api/auth/*
with fetch, so the server can check the Origin header on every write.
"""

from __future__ import annotations

import html

import northchart_ui as ui

CSS = """
*{box-sizing:border-box;margin:0}
body{min-height:100vh;display:flex;flex-direction:column}
.auth{flex:1;display:flex;align-items:flex-start;justify-content:center;padding:7vh 16px 40px}
.box{width:min(420px,100%);background:var(--p1);border:1px solid var(--bd);border-radius:14px;box-shadow:var(--shadow);padding:26px 26px 22px}
.box h1{font:700 21px var(--nc-font);letter-spacing:-.2px;margin-bottom:4px}
.box .lead{color:var(--dim);font-size:13px;line-height:1.55;margin-bottom:18px}
.f{margin-bottom:12px}.f label{display:block;font:600 11px var(--nc-font);color:var(--dim);margin-bottom:5px}
.f input{width:100%;font:14px var(--nc-font);padding:10px 11px;border:1px solid var(--bd2);border-radius:8px;background:var(--p1);color:var(--tx)}
.f input:focus{outline:none;border-color:var(--b);box-shadow:0 0 0 3px var(--bt)}
.f .help{font-size:11.5px;color:var(--dim);margin-top:4px}
.cta{display:block;width:100%;font:600 14px var(--nc-font);padding:11px;border:0;border-radius:8px;background:var(--tx);color:var(--bg);cursor:pointer;margin-top:4px}
.cta[disabled]{opacity:.5;cursor:default}
.btn2{font:600 12.5px var(--nc-font);padding:7px 12px;border:1px solid var(--bd2);border-radius:7px;background:var(--p2);color:var(--tx);cursor:pointer}
.btn2:hover{background:var(--p3)}.btn2.danger{color:var(--r);border-color:var(--r)}
.alt{text-align:center;font-size:12.5px;color:var(--dim);margin-top:16px}
.alt a,.box a{color:var(--b);text-decoration:none}.alt a:hover{text-decoration:underline}
.msg{display:none;font-size:12.5px;padding:9px 11px;border-radius:8px;margin-bottom:12px;line-height:1.5}
.msg.err{display:block;color:var(--r);background:var(--rt)}.msg.ok{display:block;color:var(--g);background:var(--gt)}
.fine{font-size:11px;color:var(--dim2);line-height:1.6;margin-top:16px;text-align:center}
.wrap2{max-width:860px;margin:0 auto;padding:22px 16px 60px}
.sec{background:var(--p1);border:1px solid var(--bd);border-radius:12px;box-shadow:var(--shadow);padding:18px 20px;margin-bottom:14px}
.sec h2{font:700 15px var(--nc-font);margin-bottom:3px}.sec .lead{color:var(--dim);font-size:12.5px;margin-bottom:12px;line-height:1.55}
.row{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}.row .f{flex:1;min-width:200px;margin:0}
table.t{width:100%;border-collapse:collapse;font-size:12.5px}
table.t td,table.t th{padding:7px 6px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:middle}
table.t th{font:600 9.5px var(--nc-font);letter-spacing:.9px;text-transform:uppercase;color:var(--dim)}
.badge{font:700 9px var(--nc-font);letter-spacing:.6px;text-transform:uppercase;padding:2px 7px;border-radius:999px}
.badge.ok{color:var(--g);background:var(--gt)}.badge.warn{color:var(--a);background:var(--at)}.badge.mut{color:var(--dim);background:var(--p3)}
code.k{font:12.5px var(--nc-mono);background:var(--p3);padding:6px 9px;border-radius:6px;display:block;word-break:break-all;margin-top:8px}
.legal{max-width:760px;margin:0 auto;padding:26px 18px 60px;line-height:1.7;font-size:14px}
.legal h1{font:700 24px var(--nc-font);margin-bottom:6px}.legal h2{font:700 16px var(--nc-font);margin:20px 0 6px}
.legal p,.legal li{color:var(--tx)}.legal ul{padding-left:20px}
"""

JS = """
<script>
"use strict";
const $=id=>document.getElementById(id);
async function post(path,body){
  const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",body:JSON.stringify(body||{})});
  let j={}; try{ j=await r.json() }catch(e){}
  if(!r.ok||j.ok===false) throw new Error(j.error||"Something went wrong. Try again.");
  return j;
}
function show(el,text,ok){ el.textContent=text; el.className="msg "+(ok?"ok":"err") }
function nextUrl(){ const n=new URLSearchParams(location.search).get("next")||"/";
  return n.startsWith("/")&&!n.startsWith("//")?n:"/" }
function wire(formId,fn){ const f=$(formId); f.addEventListener("submit",async e=>{ e.preventDefault();
  const b=f.querySelector("button[type=submit]"); b.disabled=true;
  try{ await fn(Object.fromEntries(new FormData(f))) }catch(err){ show($("msg"),err.message) } finally{ b.disabled=false } }) }
</script>
"""

FOOT = ('<p class="fine">Northchart is research software. It does not place trades and nothing on it is '
        'investment advice. <a href="/legal/terms">Terms</a> · <a href="/legal/privacy">Privacy</a> · '
        '<a href="/legal/risk">Risk notice</a></p>')


def _bare_head(title):
    return ('<header class="nc-head"><div class="nc-bar">'
            '<a class="nc-brand" href="/" aria-label="Northchart home"><div class="nc-mark" aria-hidden="true">N</div>'
            f'<div><div class="nc-name">Northchart</div><div class="nc-sub">{html.escape(title)}</div></div></a>'
            '<nav class="nc-actions"><button type="button" class="nc-icon" id="nc-theme" onclick="ncToggleTheme()">☾</button></nav>'
            '</div></header>' + ui.THEME_SWITCH)


def page(title, inner, script="", nav=None):
    head = ui.nav(nav) if nav else _bare_head(title)
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{html.escape(title)} · Northchart</title><meta name='robots' content='noindex'>"
            f"{ui.head()}<style>{CSS}</style></head><body>{head}{inner}{JS}{script}</body></html>").encode("utf-8")


def _field(name, label, typ="text", auto="", help_="", extra=""):
    return (f'<div class="f"><label for="{name}">{label}</label>'
            f'<input id="{name}" name="{name}" type="{typ}" autocomplete="{auto}" {extra}>'
            + (f'<div class="help">{help_}</div>' if help_ else "") + "</div>")


def signin(mode, has_users, smtp):
    if not has_users:
        return setup_page(True)
    signup_link = ('<div class="alt">New here? <a href="/signup">Create an account</a></div>'
                   if mode != "closed" else "")
    inner = f"""<main class="auth"><div class="box">
<h1>Sign in</h1><p class="lead">Your desk, journal and broker connections.</p>
<div id="msg" class="msg" role="alert"></div>
<form id="f" novalidate>
{_field("email", "Email", "email", "email", extra='required autofocus')}
{_field("password", "Password", "password", "current-password", extra='required')}
<button class="cta" type="submit">Sign in</button></form>
<div class="alt"><a href="/forgot">Forgot your password?</a></div>{signup_link}{FOOT}</div></main>"""
    script = """<script>wire("f",async d=>{ await post("/api/auth/signin",d); location.href=nextUrl() })</script>"""
    return page("Sign in", inner, script)


def signup(mode, invite=""):
    if mode == "closed":
        inner = ('<main class="auth"><div class="box"><h1>Sign-up is closed</h1><p class="lead">This desk is not '
                 'taking new accounts. <a href="/signin">Sign in</a> if you already have one.</p></div></main>')
        return page("Sign up", inner)
    inv = (_field("invite", "Invite code", "text", "one-time-code",
                  "Ask the desk owner for one. It looks like 1A2B-3C4D-5E6F.",
                  f'required value="{html.escape(invite)}"') if mode == "invite" else "")
    inner = f"""<main class="auth"><div class="box">
<h1>Create your account</h1><p class="lead">Connect your broker and every trade you take is journalled for you,
matched to the desk's view at entry. Read-only: Northchart never places trades.</p>
<div id="msg" class="msg" role="alert"></div>
<form id="f" novalidate>
{_field("name", "Your name", "text", "name", extra='required autofocus')}
{_field("email", "Email", "email", "email", extra='required')}
{_field("password", "Password", "password", "new-password", "At least 10 characters.", 'required minlength="10"')}
{inv}
<button class="cta" type="submit">Create account</button></form>
<div class="alt">Already have an account? <a href="/signin">Sign in</a></div>
<p class="fine">By creating an account you accept the <a href="/legal/terms">terms</a> and the
<a href="/legal/privacy">privacy notice</a>, and confirm you have read the <a href="/legal/risk">risk notice</a>.</p>
</div></main>"""
    script = """<script>wire("f",async d=>{ await post("/api/auth/signup",d); location.href="/account?welcome=1" })</script>"""
    return page("Create account", inner, script)


def setup_page(need_legacy):
    legacy = (_field("legacy_password", "Current desk password", "password", "current-password",
                     "The password this desk uses today (SIGNALDESK_PASS). It proves you run the desk.",
                     'required') if need_legacy else
              '<p class="help" style="font-size:12px;color:var(--dim);margin-bottom:12px">No desk password is set, '
              'so the owner account can only be created from the computer running the desk.</p>')
    inner = f"""<main class="auth"><div class="box">
<h1>Set up the owner account</h1><p class="lead">This desk has no accounts yet. The first account is the owner:
it keeps the existing journal and can invite other traders.</p>
<div id="msg" class="msg" role="alert"></div>
<form id="f" novalidate>
{_field("name", "Your name", "text", "name", extra='required autofocus')}
{_field("email", "Email", "email", "email", extra='required')}
{_field("password", "New password", "password", "new-password", "At least 10 characters.", 'required minlength="10"')}
{legacy}
<button class="cta" type="submit">Create owner account</button></form>{FOOT}</div></main>"""
    script = """<script>wire("f",async d=>{ await post("/api/auth/setup",d); location.href="/account?welcome=1" })</script>"""
    return page("Set up", inner, script)


def forgot(smtp):
    note = ("" if smtp else '<p class="help" style="font-size:12px;color:var(--dim);margin-bottom:12px">Email is not set '
            'up on this desk yet, so the reset link goes to the desk owner, who can pass it on.</p>')
    inner = f"""<main class="auth"><div class="box">
<h1>Reset your password</h1><p class="lead">Enter your email and we will send a link to set a new password.</p>
<div id="msg" class="msg" role="alert"></div>{note}
<form id="f" novalidate>{_field("email", "Email", "email", "email", extra='required autofocus')}
<button class="cta" type="submit">Send reset link</button></form>
<div class="alt"><a href="/signin">Back to sign in</a></div></div></main>"""
    script = """<script>wire("f",async d=>{ await post("/api/auth/forgot",d);
  show($("msg"),"If that email has an account, a reset link is on its way. It works for one hour.",true) })</script>"""
    return page("Reset password", inner, script)


def reset(token):
    inner = f"""<main class="auth"><div class="box">
<h1>Choose a new password</h1><p class="lead">You will be signed out everywhere else.</p>
<div id="msg" class="msg" role="alert"></div>
<form id="f" novalidate><input type="hidden" name="token" value="{html.escape(token)}">
{_field("password", "New password", "password", "new-password", "At least 10 characters.", 'required autofocus minlength="10"')}
<button class="cta" type="submit">Save password</button></form></div></main>"""
    script = """<script>wire("f",async d=>{ await post("/api/auth/reset",d); location.href="/" })</script>"""
    return page("New password", inner, script)


def message(title, text, link="/", link_text="Go to the desk"):
    inner = (f'<main class="auth"><div class="box"><h1>{html.escape(title)}</h1><p class="lead">{html.escape(text)}</p>'
             f'<a class="cta" style="display:block;text-align:center;text-decoration:none" href="{html.escape(link)}">'
             f'{html.escape(link_text)}</a></div></main>')
    return page(title, inner)


def account():
    inner = """<main class="wrap2">
<div id="welcome" class="msg"></div>
<section class="sec"><h2>Profile</h2><p class="lead" id="who">Loading…</p>
  <div class="row"><div class="f"><label for="name">Name</label><input id="name"></div>
  <button class="btn2" id="saveName">Save</button><button class="btn2" id="resend" hidden>Resend confirmation email</button>
  <button class="btn2" id="signout">Sign out</button></div></section>

<section class="sec" id="brokers"><h2>Brokers and data</h2>
  <p class="lead">Connect the accounts you trade from and every closed trade lands in your journal by itself.
  Northchart only ever reads: it cannot place, change or cancel orders.</p>
  <div id="brokerList" class="lead">Broker connections arrive in the next update. Until then, trades come in through
  TradingView trade sync or a sync script using the token below.</div>
  <div id="tvRow" hidden><button class="btn2" onclick="location.href='/#tradingview'">TradingView data login (desk owner)</button></div>
</section>

<section class="sec"><h2>Sync tokens</h2>
  <p class="lead">For scripts that send your trades in: TradingView trade sync, the Northchart Bridge for MetaTrader.
  A token can only add trades to your journal; it cannot read anything or sign in. You see it once.</p>
  <div class="row"><div class="f"><label for="tokLabel">Label</label><input id="tokLabel" placeholder="e.g. Laptop TradingView sync"></div>
  <button class="btn2" id="newTok">Create token</button></div><div id="tokOut"></div>
  <table class="t" id="toks"></table></section>

<section class="sec" id="invSec" hidden><h2>Invites</h2>
  <p class="lead">Sign-up on this desk is by invite. Each code works once and lasts 14 days.</p>
  <div class="row"><div class="f"><label for="invEmail">Email (optional, locks the code to it)</label><input id="invEmail" type="email"></div>
  <button class="btn2" id="newInv">Create invite</button></div><div id="invOut"></div>
  <table class="t" id="invs"></table></section>

<section class="sec"><h2>Password</h2>
  <div class="row"><div class="f"><label for="oldpw">Current password</label><input id="oldpw" type="password" autocomplete="current-password"></div>
  <div class="f"><label for="newpw">New password</label><input id="newpw" type="password" autocomplete="new-password"></div>
  <button class="btn2" id="savePw">Change password</button></div></section>

<section class="sec"><h2>Where you are signed in</h2><table class="t" id="sess"></table>
  <div style="margin-top:10px"><button class="btn2" id="others">Sign out everywhere else</button></div></section>

<section class="sec"><h2>Your data</h2>
  <p class="lead">Download your journal as a zip of CSV files at any time. Deleting your account removes your
  journal, tokens and sessions from this desk.</p>
  <div class="row"><a class="btn2" href="/api/auth/export.zip" style="text-decoration:none">Download my data</a>
  <span style="flex:1"></span><button class="btn2 danger" id="del">Delete account</button></div></section>
<div id="msg" class="msg" role="alert" style="position:fixed;bottom:14px;right:14px;max-width:360px;z-index:9"></div>
</main>"""
    script = r"""<script>
const when=t=>t?new Date(t*1000).toLocaleString("en-GB",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"}):"never";
const escH=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function flash(t,ok){ show($("msg"),t,ok); clearTimeout(flash.h); flash.h=setTimeout(()=>$("msg").className="msg",3500) }
async function load(){
  const me=await (await fetch("/api/auth/me")).json(); if(!me.user){ location.href="/signin?next=/account"; return }
  const u=me.user;
  $("who").innerHTML=`${escH(u.email)} · <span class="badge ${u.role==="owner"?"ok":"mut"}">${u.role==="owner"?"Owner":"Member"}</span> `+
    (u.verified?'<span class="badge ok">Email confirmed</span>':'<span class="badge warn">Email not confirmed</span>');
  $("name").value=u.name||""; $("resend").hidden=!!u.verified;
  $("invSec").hidden=!(u.role==="owner"&&me.signup_mode==="invite"); $("tvRow").hidden=u.role!=="owner";
  const t=await (await fetch("/api/auth/tokens")).json();
  $("toks").innerHTML=(t.tokens||[]).length?"<tr><th>Label</th><th>Created</th><th>Last used</th><th></th></tr>"+t.tokens.map(k=>
    `<tr><td>${escH(k.label)}</td><td>${when(k.created)}</td><td>${when(k.last)}</td><td><button class="btn2 danger" data-tok="${k.id}">Revoke</button></td></tr>`).join(""):"";
  const s=await (await fetch("/api/auth/sessions")).json();
  $("sess").innerHTML="<tr><th>Device</th><th>IP address</th><th>Signed in</th><th></th></tr>"+(s.sessions||[]).map(x=>
    `<tr><td>${escH((x.ua||"").replace(/\(.*?\)/g,"").slice(0,60)||"Unknown")}</td><td>${escH(x.ip||"")}</td><td>${when(x.created)}</td><td>${x.current?'<span class="badge ok">This device</span>':`<button class="btn2" data-sid="${x.sid}">Sign out</button>`}</td></tr>`).join("");
  if(!$("invSec").hidden){ const iv=await (await fetch("/api/auth/invites")).json();
    $("invs").innerHTML=(iv.invites||[]).length?"<tr><th>Code</th><th>For</th><th>Expires</th><th>Status</th><th></th></tr>"+iv.invites.map(i=>
      `<tr><td><code>${escH(i.hint)}</code></td><td>${escH(i.email||"Anyone")}</td><td>${when(i.exp)}</td><td>${i.used_by?"Used by "+escH(i.used_by):"Open"}</td><td>${i.used_by?"":`<button class="btn2 danger" data-inv="${i.id}">Revoke</button>`}</td></tr>`).join(""):"" }
}
if(new URLSearchParams(location.search).get("welcome")) show($("welcome"),"Welcome to Northchart. Next: connect a broker or create a sync token so your trades start arriving.",true);
const act=(id,fn)=>$(id).onclick=async()=>{ try{ await fn() }catch(e){ flash(e.message) } };
act("saveName",async()=>{ await post("/api/auth/profile",{name:$("name").value}); flash("Saved",true) });
act("resend",async()=>{ await post("/api/auth/verify/resend"); flash("Confirmation email sent",true) });
act("signout",async()=>{ await post("/api/auth/signout"); location.href="/signin" });
act("savePw",async()=>{ await post("/api/auth/password",{old:$("oldpw").value,new:$("newpw").value}); $("oldpw").value=$("newpw").value=""; flash("Password changed. Other devices were signed out.",true); load() });
act("others",async()=>{ await post("/api/auth/sessions/revoke",{others:true}); flash("Signed out everywhere else",true); load() });
act("newTok",async()=>{ const j=await post("/api/auth/tokens",{label:$("tokLabel").value});
  $("tokOut").innerHTML=`<p class="lead" style="margin:10px 0 0">Copy it now; it will not be shown again.</p><code class="k">${escH(j.token)}</code>`; load() });
act("newInv",async()=>{ const j=await post("/api/auth/invites",{email:$("invEmail").value});
  const link=location.origin+"/signup?invite="+encodeURIComponent(j.code);
  $("invOut").innerHTML=`<p class="lead" style="margin:10px 0 0">Send this link; it is shown once.</p><code class="k">${escH(link)}</code>`; load() });
act("del",async()=>{ const pw=prompt("Type your password to delete your account and journal from this desk. This cannot be undone.");
  if(!pw) return; await post("/api/auth/delete",{password:pw}); location.href="/signin" });
document.addEventListener("click",async e=>{ const b=e.target.closest("button[data-tok],button[data-sid],button[data-inv]"); if(!b) return;
  try{ if(b.dataset.tok) await post("/api/auth/tokens/revoke",{id:b.dataset.tok});
    if(b.dataset.sid) await post("/api/auth/sessions/revoke",{sid:b.dataset.sid});
    if(b.dataset.inv) await post("/api/auth/invites/revoke",{id:b.dataset.inv}); load() }catch(err){ flash(err.message) } });
load();
</script>"""
    return page("Account", inner, script, nav="account")


LEGAL = {
    "terms": ("Terms of use", """
<p>These terms cover your use of Northchart, a trading research desk run by Precifarm AI Ltd, Nairobi, Kenya.</p>
<h2>What Northchart is</h2><p>Northchart shows market data, the desk's analysis of it, and a journal of trades you take
elsewhere. It is research software. It does not hold money, execute trades, or act as a broker or investment adviser.</p>
<h2>Your account</h2><p>Keep your password private. You are responsible for what happens under your account. Tell the desk
owner straight away if you think someone else has used it.</p>
<h2>Broker connections</h2><p>You connect a broker with read-only credentials (an investor password, a read-only key or a
statement file). Northchart only reads closed trades and account history. You can disconnect at any time, which deletes
the stored credentials.</p>
<h2>Acceptable use</h2><p>Do not try to break into other accounts, overload the service, or resell the desk's data.</p>
<h2>No warranty</h2><p>Data can be late, wrong or missing. The service is provided as it is, without any promise that it
will be uninterrupted or error free.</p>
<h2>Changes</h2><p>These terms may change. Material changes will be shown when you next sign in.</p>"""),
    "privacy": ("Privacy notice", """
<p>Precifarm AI Ltd runs Northchart and is responsible for your personal data under the Kenya Data Protection Act, 2019.</p>
<h2>What we collect</h2><ul><li>Account details: name, email, a hash of your password (never the password itself).</li>
<li>Sign-in records: when and from which IP address and browser you signed in.</li>
<li>Your trades: closed trades from the brokers or files you connect, and the notes, emotions and stops you add.</li>
<li>Broker credentials you provide, stored encrypted and used only to read your trade history.</li></ul>
<h2>Why</h2><p>To run your journal and show you your own statistics. We do not sell your data and we do not use your
trades to trade.</p>
<h2>Who can see it</h2><p>Only you. The desk owner can see that your account exists and when it was created, not your
journal. AI assistants (ChatGPT, Claude) see your journal only if you connect them yourself.</p>
<h2>Your rights</h2><p>You can download your data and delete your account from the account page at any time. To ask
about your data, write to david.givondoh@gmail.com.</p>
<h2>Keeping it</h2><p>We keep your data while your account exists. Deleting the account removes your journal, tokens and
sessions from the desk.</p>"""),
    "risk": ("Risk notice", """
<p><b>Trading foreign exchange, metals, indices and crypto on margin carries a high level of risk and may not suit
you.</b> Leverage can work against you as well as for you, and you can lose more than you deposit with some brokers.</p>
<p>Northchart's signals, grades, levels and agent answers are information for research. They are not personal
recommendations, they do not take your circumstances into account, and past results do not predict future results.</p>
<p>Before trading, make sure you understand the risks and use a broker licensed by the Capital Markets Authority
of Kenya or by the regulator where you live. Seek independent advice if you are unsure.</p>"""),
}


def legal(kind):
    title, body = LEGAL[kind]
    inner = f'<main class="legal"><h1>{html.escape(title)}</h1><p class="fine" style="text-align:left">Last updated 21 November 2026</p>{body}</main>'
    return page(title, inner)
