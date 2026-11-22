"""Pages for signing in, signing up, the owner's first-run setup, password reset and the account page.

Every page shares the Northcharts design (northchart_ui). Forms post JSON to /api/auth/*
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
.box a.gbtn{color:var(--tx)}
.gbtn{position:relative;display:flex;align-items:center;justify-content:center;gap:10px;width:100%;height:46px;font:600 14.5px var(--nc-font);
border:1px solid var(--bd2);border-radius:10px;background:var(--p1);color:var(--tx);cursor:pointer;text-decoration:none;
box-shadow:0 1px 2px rgba(16,24,40,.06);transition:background .15s,box-shadow .15s,border-color .15s}
.gbtn:hover{background:var(--p2);border-color:var(--dim2);box-shadow:0 2px 6px rgba(16,24,40,.08)}
.gbtn:focus-visible{outline:none;box-shadow:0 0 0 3px var(--bt);border-color:var(--b)}
.gbtn .gmark{width:22px;height:22px;border-radius:50%;border:1.5px solid var(--bd2);display:inline-flex;align-items:center;justify-content:center;
font:700 12px var(--nc-font);color:var(--dim)}
.gbtn.busy{pointer-events:none;color:var(--dim)}
.gbtn.busy .gmark{border-color:var(--b);border-right-color:transparent;animation:gspin .8s linear infinite;color:transparent}
@keyframes gspin{to{transform:rotate(360deg)}}
.gbtn .last{position:absolute;right:10px;font:700 9.5px var(--nc-font);letter-spacing:.5px;text-transform:uppercase;color:var(--g);background:var(--gt);
padding:2px 7px;border-radius:999px}
.gnote{font-size:11.5px;color:var(--dim);text-align:center;margin:8px 0 0}
.box h1 + .lead{margin-bottom:20px}
.or{display:flex;align-items:center;gap:10px;color:var(--dim2);font-size:11.5px;margin:14px 0}
.or:before,.or:after{content:"";flex:1;height:1px;background:var(--bd)}
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
.chips2{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.chip2{font:500 12.5px var(--nc-font);padding:7px 11px;border-radius:999px;border:1px solid var(--bd2);background:var(--p2);color:var(--tx);cursor:pointer}
.chip2:hover{background:var(--p3)}.chip2 .cma{font-size:10px;color:var(--g);margin-left:5px;font-weight:700}
.conn{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--bd)}
.conn b{font-size:13.5px}.conn .lead{margin:0;font-size:12px}
.route{border:1px solid var(--bd);border-radius:10px;padding:14px 16px;margin-top:12px;background:var(--p2)}
.route h3{font:700 14px var(--nc-font);margin-bottom:4px;display:flex;align-items:center;gap:8px}
.route ol{margin:8px 0 10px 18px;font-size:12.5px;line-height:1.7;color:var(--tx)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid2 .f{margin:0}.f select{width:100%;font:14px var(--nc-font);padding:9px 10px;border:1px solid var(--bd2);border-radius:8px;background:var(--p1);color:var(--tx)}
@media(max-width:640px){.grid2{grid-template-columns:1fr}}
code.k{font:12.5px var(--nc-mono);background:var(--p3);padding:6px 9px;border-radius:6px;display:block;word-break:break-all;margin-top:8px}
.acct [hidden]{display:none!important}
.acct a{color:var(--b)}
.acct{width:100%;max-width:1000px;margin:0 auto;padding:26px 16px 70px;display:grid;grid-template-columns:220px minmax(0,1fr);gap:28px;align-items:start}
.acct .lead{color:var(--dim);font-size:12.5px;line-height:1.55}
.side{position:sticky;top:78px}
.me{display:flex;gap:10px;align-items:center;padding:2px 10px 16px}
.av{width:36px;height:36px;border-radius:50%;background:var(--p3);color:var(--tx);display:flex;align-items:center;justify-content:center;font:700 14px var(--nc-font);flex-shrink:0}
.av.lg{width:48px;height:48px;font-size:19px}
.me .n{font-weight:700;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.me .e{font-size:11.5px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tabs{display:flex;flex-direction:column;gap:2px}
.tab{font:500 13.5px var(--nc-font);text-align:left;padding:8px 10px;border:0;border-radius:8px;background:none;color:var(--dim);cursor:pointer;white-space:nowrap}
.tab:hover{background:var(--p2);color:var(--tx)}.tab[aria-selected=true]{background:var(--p3);color:var(--tx);font-weight:600}
.pane{display:none}.pane.on{display:block}
.pane>h2{font:700 21px var(--nc-font);letter-spacing:-.2px;margin-bottom:4px}.pane>.lead{font-size:13px;margin-bottom:16px;max-width:620px}
.card2{background:var(--p1);border:1px solid var(--bd);border-radius:12px;padding:18px 20px;margin-bottom:14px}
.card2 h3{font:600 14.5px var(--nc-font)}
.item{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--bd)}.item:first-child{border-top:0}
.t1{font-weight:600;font-size:13.5px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}.t2{font-size:12px;color:var(--dim);margin-top:2px;line-height:1.5}
.note{display:flex;align-items:center;gap:10px;font-size:12.5px;padding:9px 12px;border-radius:8px;margin-top:12px}.note.warn{background:var(--at);color:var(--tx)}
.gl{width:36px;height:36px;border-radius:50%;border:1px solid var(--bd2);display:flex;align-items:center;justify-content:center;font:700 15px var(--nc-font);color:var(--b);flex-shrink:0}
.btn2.pri{background:var(--tx);color:var(--bg);border-color:var(--tx)}.btn2.pri:hover{background:var(--tx);opacity:.88}.btn2[disabled]{opacity:.45;cursor:default}
.chip2.on{border-color:var(--tx);background:var(--p3);font-weight:600}
.acct .route ol{margin:8px 0 12px;padding-left:20px}
.bhead{display:flex;align-items:center;gap:12px;margin-top:16px;padding-top:16px;border-top:1px solid var(--bd)}
details.more summary,details.adv summary{cursor:pointer;font:600 13px var(--nc-font);color:var(--tx);list-style:none}
details.more summary{margin-top:14px;color:var(--b)}details.more summary::-webkit-details-marker,details.adv summary::-webkit-details-marker{display:none}
details.more summary:before,details.adv summary:before{content:"▸ ";color:var(--dim)}details[open].more summary:before,details[open].adv summary:before{content:"▾ "}
.dz{border-color:var(--rt)}
@media(max-width:760px){.acct{grid-template-columns:1fr;gap:16px;padding-top:14px}.side{position:static}.me{display:none}
.tabs{flex-direction:row;overflow-x:auto;gap:4px;padding-bottom:4px}.row .f{min-width:100%}}
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
  if(!r.ok||j.ok===false){ const e=new Error(j.error||"Something went wrong. Try again."); e.data=j; throw e }
  return j;
}
function show(el,text,ok){ el.innerHTML=String(text).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))
  .replace(/(?:at )?(\\/billing(?:\\?plan=\\w+)?)/,'<a href="$1" style="color:inherit;font-weight:700">on the Plan page</a>');
  el.className="msg "+(ok?"ok":"err") }
function nextUrl(){ const n=new URLSearchParams(location.search).get("next")||"/";
  return n.startsWith("/")&&!n.startsWith("//")?n:"/" }
function wire(formId,fn){ const f=$(formId); f.addEventListener("submit",async e=>{ e.preventDefault();
  const b=f.querySelector("button[type=submit]"); b.disabled=true;
  try{ await fn(Object.fromEntries(new FormData(f))) }catch(err){ show($("msg"),err.message) } finally{ b.disabled=false } }) }
</script>
"""

FOOT = ('<p class="fine">Northcharts is research software. It does not place trades and nothing on it is '
        'investment advice. <a href="/legal/terms">Terms</a> · <a href="/legal/privacy">Privacy</a> · '
        '<a href="/legal/risk">Risk notice</a></p>')


def _bare_head(title):
    return ('<header class="nc-head"><div class="nc-bar">'
            '<a class="nc-brand" href="/" aria-label="Northcharts home"><div class="nc-mark" aria-hidden="true">N</div>'
            f'<div><div class="nc-name">Northcharts</div><div class="nc-sub">{html.escape(title)}</div></div></a>'
            '<nav class="nc-actions"><button type="button" class="nc-icon" id="nc-theme" onclick="ncToggleTheme()">☾</button></nav>'
            '</div></header>' + ui.THEME_SWITCH)


def page(title, inner, script="", nav=None):
    head = ui.nav(nav) if nav else _bare_head(title)
    return ("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{html.escape(title)} · Northcharts</title><meta name='robots' content='noindex'>"
            f"{ui.head()}<style>{CSS}</style></head><body>{head}{inner}{JS}{script}</body></html>").encode("utf-8")


def _field(name, label, typ="text", auto="", help_="", extra=""):
    return (f'<div class="f"><label for="{name}">{label}</label>'
            f'<input id="{name}" name="{name}" type="{typ}" autocomplete="{auto}" {extra}>'
            + (f'<div class="help">{help_}</div>' if help_ else "") + "</div>")


def _google_block(href, label, note="Google confirms your email. Northcharts never sees your Google password."):
    return (f'<a class="gbtn" id="gbtn" href="{html.escape(href)}" data-label="{html.escape(label)}">'
            f'<span class="gmark" aria-hidden="true">G</span><span class="gl">{html.escape(label)}</span>'
            '<span class="last" id="glast" hidden>Last used</span></a>'
            f'<p class="gnote">{html.escape(note)}</p><div class="or">or use your email</div>')


GOOGLE_JS = """<script>
(function(){ const g=document.getElementById("gbtn"); if(!g) return;
  try{ if(localStorage.getItem("nc-last-login")==="google") document.getElementById("glast").hidden=false }catch(e){}
  const err=new URLSearchParams(location.search).get("gerr"); if(err) show($("msg"),err);
  g.addEventListener("click",e=>{ if(e.defaultPrevented) return;
    try{ localStorage.setItem("nc-last-login","google") }catch(_){}
    g.classList.add("busy"); g.querySelector(".gl").textContent="Opening Google…" });
  window.addEventListener("pageshow",()=>{ g.classList.remove("busy"); g.querySelector(".gl").textContent=g.dataset.label });
})();
const f0=document.getElementById("f"); if(f0) f0.addEventListener("submit",()=>{ try{ localStorage.setItem("nc-last-login","email") }catch(_){} });
</script>"""


def signin(mode, has_users, smtp, google=False):
    if not has_users:
        return setup_page(True)
    signup_link = ('<div class="alt">New here? <a href="/signup">Create an account</a></div>'
                   if mode != "closed" else "")
    inner = f"""<main class="auth"><div class="box">
<h1>Welcome back</h1><p class="lead">Sign in to your desk, journal and broker connections.</p>
<div id="msg" class="msg" role="alert"></div>
{_google_block("/auth/google/start", "Continue with Google") if google else ""}
<form id="f" novalidate>
{_field("email", "Email", "email", "email", extra='required autofocus')}
{_field("password", "Password", "password", "current-password", extra='required')}
<button class="cta" type="submit">Sign in</button></form>
<div class="alt"><a href="/forgot">Forgot your password?</a></div>{signup_link}{FOOT}</div></main>"""
    script = """<script>wire("f",async d=>{ await post("/api/auth/signin",d); location.href=nextUrl() });
const g=document.getElementById("gbtn"); if(g) g.href="/auth/google/start?next="+encodeURIComponent(nextUrl());</script>""" + GOOGLE_JS
    return page("Sign in", inner, script)


def signup(mode, invite="", google=False):
    if mode == "closed":
        inner = ('<main class="auth"><div class="box"><h1>Sign-up is closed</h1><p class="lead">This desk is not '
                 'taking new accounts. <a href="/signin">Sign in</a> if you already have one.</p></div></main>')
        return page("Sign up", inner)
    inv = (_field("invite", "Invite code", "text", "one-time-code",
                  "From the desk owner. It looks like 1A2B-3C4D-5E6F.",
                  f'required form="f" value="{html.escape(invite)}"') if mode == "invite" else "")
    inner = f"""<main class="auth"><div class="box">
<h1>Create your account</h1><p class="lead">Connect your broker and every trade you take is journalled for you,
matched to the desk's view at entry. Read-only: Northcharts never places trades.</p>
<div id="msg" class="msg" role="alert"></div>
{inv}
{_google_block("/auth/google/start", "Sign up with Google") if google else ""}
<form id="f" novalidate>
{_field("name", "Your name", "text", "name", extra='required' + ('' if inv else ' autofocus'))}
{_field("email", "Email", "email", "email", extra='required')}
{_field("password", "Password", "password", "new-password", "At least 10 characters.", 'required minlength="10"')}
<button class="cta" type="submit">Create account</button></form>
<div class="alt">Already have an account? <a href="/signin">Sign in</a></div>
<p class="fine">By creating an account you accept the <a href="/legal/terms">terms</a> and the
<a href="/legal/privacy">privacy notice</a>, and confirm you have read the <a href="/legal/risk">risk notice</a>.</p>
</div></main>"""
    script = """<script>wire("f",async d=>{ await post("/api/auth/signup",d); location.href="/account?welcome=1" });
const g=document.getElementById("gbtn");
if(g) g.addEventListener("click",e=>{ const i=document.getElementById("invite");
  if(i&&!i.value.trim()){ e.preventDefault(); show($("msg"),"Enter your invite code first, then choose Sign up with Google."); i.focus(); return }
  g.href="/auth/google/start"+(i?"?invite="+encodeURIComponent(i.value.trim()):"") });</script>""" + GOOGLE_JS
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
    inner = """<main class="acct">
<aside class="side">
  <div class="me"><div class="av" id="av">·</div><div style="min-width:0"><div class="n" id="meName">Loading…</div><div class="e" id="meEmail"></div></div></div>
  <nav class="tabs" role="tablist" aria-label="Account sections">
    <button class="tab" role="tab" data-pane="profile">Profile</button>
    <button class="tab" role="tab" data-pane="brokers">Brokers</button>
    <button class="tab" role="tab" data-pane="security">Sign-in and security</button>
    <button class="tab" role="tab" data-pane="plan">Plan</button>
    <button class="tab" role="tab" data-pane="invites" id="invTab" hidden>Invites</button>
    <button class="tab" role="tab" data-pane="data">Your data</button>
  </nav>
</aside>
<div>
<div id="welcome" class="msg"></div>

<section class="pane" id="pane-profile">
  <h2>Profile</h2><p class="lead">How you appear on this desk.</p>
  <div class="card2">
    <div class="item" style="border:0;padding-top:0"><div class="av lg" id="av2">·</div>
      <div style="flex:1;min-width:0"><div class="t1" id="pName"></div><div class="t2" id="pEmail"></div></div>
      <span class="badge ok" id="roleBadge" hidden>Owner</span></div>
    <div class="note warn" id="unverified" hidden><span style="flex:1">Your email is not confirmed yet.</span>
      <button class="btn2" id="resend">Resend email</button></div>
    <div class="row" style="margin-top:14px"><div class="f"><label for="name">Name</label><input id="name" autocomplete="name"></div>
      <button class="btn2 pri" id="saveName" disabled>Save</button></div>
  </div>
  <div class="card2" id="gRow" hidden><div class="item" style="border:0;padding:0">
    <div class="gl" aria-hidden="true">G</div>
    <div style="flex:1;min-width:0"><div class="t1">Google</div><div class="t2" id="gText"></div></div>
    <a class="btn2" id="gLink" href="/auth/google/start?mode=link" style="text-decoration:none">Connect</a>
    <button class="btn2" id="gUnlink" hidden>Disconnect</button></div></div>
  <button class="btn2" id="signout">Sign out</button>
</section>

<section class="pane" id="pane-plan">
  <h2>Plan</h2><p class="lead">What your plan includes and how you pay for it.</p>
  <div class="card2"><div class="item" style="border:0;padding:0"><div style="flex:1;min-width:0">
    <div class="t1" id="planName">Loading…</div><div class="t2" id="planText"></div></div>
    <a class="btn2 pri" href="/billing" style="text-decoration:none" id="planBtn">Plans and billing</a></div></div>
</section>

<section class="pane" id="pane-brokers">
  <h2>Brokers</h2><p class="lead">Connect the account you trade from and your closed trades appear in your journal
  automatically. Northcharts only reads; it can never place or change a trade.</p>
  <div class="card2" id="connCard"><div id="conns"></div></div>
  <div class="card2"><h3 id="addTitle">Connect a broker</h3>
    <div class="f" style="margin:10px 0 8px"><input id="bq" placeholder="Search: Exness, HFM, FXPesa, Pepperstone…" autocomplete="off" aria-label="Find your broker"></div>
    <div id="bres" class="chips2"></div>
    <div id="bpanel"></div>
    <p class="lead" id="planned" style="margin-top:12px"></p>
  </div>
  <details class="card2 adv"><summary>Advanced: sync tokens for scripts</summary>
    <p class="lead" style="margin-top:10px">Only needed if you run the TradingView sync script yourself. The broker steps
    above create a token for you. A token can only add trades to your journal and is shown once.</p>
    <div class="row"><div class="f"><label for="tokLabel">Name it</label><input id="tokLabel" autocomplete="off" placeholder="e.g. Laptop TradingView sync"></div>
      <button class="btn2" id="newTok">Create token</button></div>
    <div id="tokOut"></div><div id="toks" style="margin-top:8px"></div>
    <div id="tvRow" hidden style="margin-top:12px"><button class="btn2" onclick="location.href='/#tradingview'">TradingView data login (desk owner)</button></div>
  </details>
</section>

<section class="pane" id="pane-security">
  <h2>Sign-in and security</h2><p class="lead">Your password and the devices signed in to your account.</p>
  <div class="card2"><h3 id="pwTitle">Password</h3>
    <div class="row" style="margin-top:10px"><div class="f"><label for="oldpw">Current password</label><input id="oldpw" type="password" autocomplete="current-password"></div>
      <div class="f"><label for="newpw">New password</label><input id="newpw" type="password" autocomplete="new-password" placeholder="At least 10 characters"></div>
      <button class="btn2 pri" id="savePw">Change password</button></div></div>
  <div class="card2"><h3>Devices</h3><div id="sess" style="margin-top:6px"></div>
    <button class="btn2" id="others" style="margin-top:12px">Sign out of all other devices</button></div>
</section>

<section class="pane" id="pane-invites">
  <h2>Invites</h2><p class="lead">New traders join this desk by invite. Each link works once and lasts 14 days.</p>
  <div class="card2"><div class="row"><div class="f"><label for="invEmail">Their email (optional)</label><input id="invEmail" type="email" autocomplete="off"></div>
    <button class="btn2 pri" id="newInv">Create invite link</button></div><div id="invOut"></div><div id="invs" style="margin-top:8px"></div></div>
</section>

<section class="pane" id="pane-data">
  <h2>Your data</h2><p class="lead">Your journal belongs to you.</p>
  <div class="card2"><div class="item" style="border:0;padding:0"><div style="flex:1"><div class="t1">Download your journal</div>
    <div class="t2">Every trade and note, as CSV files in a zip.</div></div>
    <a class="btn2" href="/api/auth/export.zip" style="text-decoration:none">Download</a></div></div>
  <div class="card2 dz"><div class="item" style="border:0;padding:0"><div style="flex:1"><div class="t1">Delete account</div>
    <div class="t2">Removes your journal, broker connections and sign-ins from this desk. This cannot be undone.</div></div>
    <button class="btn2 danger" id="del">Delete</button></div></div>
</section>
</div>
<div id="msg" class="msg" role="alert" style="position:fixed;bottom:14px;right:14px;max-width:360px;z-index:9"></div>
</main>"""
    script = r"""<script>
const when=t=>t?new Date(t*1000).toLocaleString("en-GB",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}):"never";
const escH=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function flash(t,ok){ show($("msg"),t,ok); clearTimeout(flash.h); flash.h=setTimeout(()=>$("msg").className="msg",3500) }
function device(ua){ ua=ua||"";
  const b=/Edg\//.test(ua)?"Edge":/OPR\//.test(ua)?"Opera":/Chrome\//.test(ua)?"Chrome":/Firefox\//.test(ua)?"Firefox":/Safari\//.test(ua)?"Safari":"A browser";
  const o=/Windows/.test(ua)?"Windows":/Android/.test(ua)?"Android":/iPhone|iPad/.test(ua)?"iPhone or iPad":/Mac OS X|Macintosh/.test(ua)?"Mac":/Linux/.test(ua)?"Linux":"";
  return o?b+" on "+o:b }
const place=ip=>!ip?"":/^(127\.|::1$|::ffff:127\.)/.test(ip)?"this computer":ip;
function pick(name){ if(!$("pane-"+name)||(name==="invites"&&$("invTab").hidden)) name="profile";
  document.querySelectorAll(".pane").forEach(p=>p.classList.toggle("on",p.id==="pane-"+name));
  document.querySelectorAll(".tab").forEach(t=>t.setAttribute("aria-selected",t.dataset.pane===name));
  if(location.hash!=="#"+name) history.replaceState(null,"","#"+name) }
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>pick(t.dataset.pane));
let ME=null;
async function load(){
  const me=await (await fetch("/api/auth/me")).json(); if(!me.user){ location.href="/signin?next=/account"; return }
  const u=ME=me.user, initial=(u.name||u.email||"?").trim().charAt(0).toUpperCase();
  $("av").textContent=$("av2").textContent=initial;
  $("meName").textContent=$("pName").textContent=u.name||"No name yet"; $("meEmail").textContent=$("pEmail").textContent=u.email;
  $("roleBadge").hidden=u.role!=="owner"; $("unverified").hidden=!!u.verified;
  if(document.activeElement!==$("name")){ $("name").value=u.name||""; $("saveName").disabled=true }
  const pn={free:"Free",plus:"Plus",pro:"Pro"}[me.plan]||"Free", owner=u.role==="owner";
  $("planName").textContent=owner?"Owner":me.billing==="off"?"Everything is open":"Northcharts "+pn;
  $("planText").textContent=owner?"You own this desk, so every feature is on.":me.billing==="off"?"Every feature is open on this desk for now.":
    me.plan==="free"?"Desk calls, the chart and your manual journal. Plus adds automatic broker sync and the agent desk.":"Manage payments and receipts on the billing page.";
  $("planBtn").textContent=me.plan==="free"&&!owner&&me.billing!=="off"?"Upgrade":"Plans and billing";
  $("gRow").hidden=!me.google&&!u.google;
  $("gText").innerHTML=u.google?`Connected as ${escH(u.google_email)}`:"Sign in with one click.";
  $("gLink").hidden=!!u.google; $("gUnlink").hidden=!u.google;
  $("oldpw").closest(".f").hidden=!u.has_password; $("savePw").textContent=u.has_password?"Change password":"Set a password";
  $("pwTitle").textContent=u.has_password?"Password":"Add a password";
  $("invTab").hidden=!(u.role==="owner"&&me.signup_mode==="invite"); $("tvRow").hidden=u.role!=="owner";
  const t=await (await fetch("/api/auth/tokens")).json();
  $("toks").innerHTML=(t.tokens||[]).map(k=>`<div class="item"><div style="flex:1;min-width:0"><div class="t1">${escH(k.label||"Token")}</div>
    <div class="t2">Created ${when(k.created)} · last used ${when(k.last)}</div></div><button class="btn2 danger" data-tok="${k.id}">Revoke</button></div>`).join("");
  const s=await (await fetch("/api/auth/sessions")).json();
  $("sess").innerHTML=(s.sessions||[]).sort((a,b)=>(b.current?1:0)-(a.current?1:0)).map(x=>`<div class="item"><div style="flex:1;min-width:0">
    <div class="t1">${escH(device(x.ua))}</div><div class="t2">Signed in ${when(x.created)}${place(x.ip)?" · "+escH(place(x.ip)):""}</div></div>
    ${x.current?'<span class="badge ok">This device</span>':`<button class="btn2" data-sid="${x.sid}">Sign out</button>`}</div>`).join("");
  $("others").hidden=(s.sessions||[]).length<2;
  if(!$("invTab").hidden){ const iv=await (await fetch("/api/auth/invites")).json();
    $("invs").innerHTML=(iv.invites||[]).map(i=>`<div class="item"><div style="flex:1;min-width:0"><div class="t1">${escH(i.email||"Anyone with the link")}</div>
      <div class="t2">${i.used_by?"Used by "+escH(i.used_by):"Open · expires "+when(i.exp)} · code ${escH(i.hint)}</div></div>
      ${i.used_by?"":`<button class="btn2 danger" data-inv="${i.id}">Revoke</button>`}</div>`).join("") }
}
const qs=new URLSearchParams(location.search);
if(qs.get("welcome")) show($("welcome"),"Welcome to Northcharts. Connect your broker below and your trades will start arriving.",true);
if(qs.get("google")==="linked") show($("welcome"),"Google connected. You can now sign in with it.",true);
pick(location.hash.slice(1)||(qs.get("welcome")?"brokers":"profile"));
const act=(id,fn)=>$(id).onclick=async()=>{ const b=$(id); b.disabled=true; try{ await fn() }catch(e){ flash(e.message) } finally{ b.disabled=false } };
$("name").addEventListener("input",()=>$("saveName").disabled=!ME||$("name").value.trim()===(ME.name||""));
act("saveName",async()=>{ await post("/api/auth/profile",{name:$("name").value}); flash("Saved",true); await load() });
act("resend",async()=>{ await post("/api/auth/verify/resend"); flash("Confirmation email sent",true) });
act("gUnlink",async()=>{ await post("/api/auth/google/unlink"); flash("Google disconnected",true); load() });
{ const ge=qs.get("gerr"); if(ge) show($("welcome"),ge) }
act("signout",async()=>{ await post("/api/auth/signout"); location.href="/signin" });
act("savePw",async()=>{ await post("/api/auth/password",{old:$("oldpw").value,new:$("newpw").value}); $("oldpw").value=$("newpw").value=""; flash("Password saved. Other devices were signed out.",true); load() });
act("others",async()=>{ await post("/api/auth/sessions/revoke",{others:true}); flash("Signed out of all other devices",true); load() });
act("newTok",async()=>{ const j=await post("/api/auth/tokens",{label:$("tokLabel").value}); $("tokLabel").value="";
  $("tokOut").innerHTML=`<p class="lead" style="margin:10px 0 0">Copy it now; it will not be shown again.</p><code class="k">${escH(j.token)}</code>`; load() });
act("newInv",async()=>{ const j=await post("/api/auth/invites",{email:$("invEmail").value});
  const link=location.origin+"/signup?invite="+encodeURIComponent(j.code);
  $("invOut").innerHTML=`<p class="lead" style="margin:10px 0 0">Send this link. It is shown once.</p><code class="k">${escH(link)}</code>`; load() });
act("del",async()=>{ const pw=prompt("To delete your account and journal, type your password (or your email address if you only sign in with Google). This cannot be undone.");
  if(!pw) return; await post("/api/auth/delete",{password:pw}); location.href="/signin" });
document.addEventListener("click",async e=>{ const b=e.target.closest("button[data-tok],button[data-sid],button[data-inv]"); if(!b) return;
  try{ if(b.dataset.tok) await post("/api/auth/tokens/revoke",{id:b.dataset.tok});
    if(b.dataset.sid) await post("/api/auth/sessions/revoke",{sid:b.dataset.sid});
    if(b.dataset.inv) await post("/api/auth/invites/revoke",{id:b.dataset.inv}); load() }catch(err){ flash(err.message) } });

let BK=null;
const kindName={bridge:"Northcharts Bridge",metaapi:"Investor password",statement:"Statement upload",tradingview:"TradingView sync"};
async function loadBrokers(){
  BK=await (await fetch("/api/brokers")).json();
  const cs=BK.connections||[];
  $("connCard").hidden=!cs.length; $("addTitle").textContent=cs.length?"Connect another broker":"Connect a broker";
  $("conns").innerHTML=cs.map(c=>`<div class="item"><div style="flex:1;min-width:0"><div class="t1">${escH(c.label||c.broker)}
    <span class="badge ${c.state==="error"?"warn":"ok"}">${c.state==="error"?"Needs attention":"Connected"}</span></div>
    <div class="t2">${escH(kindName[c.kind]||c.kind)} · ${c.trades||0} trade${c.trades===1?"":"s"} · last sync ${when(c.last_sync)}${c.last_error?' · <span style="color:var(--r)">'+escH(c.last_error)+'</span>':""}</div></div>
    ${c.kind==="metaapi"?`<button class="btn2" data-sync="${c.id}">Sync now</button>`:""}<button class="btn2 danger" data-rm="${c.id}">Disconnect</button></div>`).join("");
  const pl=(BK.planned||[]).map(p=>p.name); $("planned").textContent=pl.length?"Coming soon: "+pl.join(", ")+".":"";
  showBrokers($("bq").value);
}
function showBrokers(q){
  q=q.trim().toLowerCase();
  const list=(BK.brokers||[]).filter(b=>!q||(b.search||b.name.toLowerCase()).includes(q));
  $("bres").innerHTML=list.slice(0,q?12:8).map(b=>`<button class="chip2${$("bpanel").dataset.b===b.id?" on":""}" data-b="${b.id}">${escH(b.name)}</button>`).join("")
    +(q&&!list.length?'<p class="lead" style="margin:4px 0 0">Not listed? Pick "Any other MetaTrader broker", or upload a statement.</p>':"")
    +(!q?`<p class="lead" style="margin:6px 0 0">Showing the 8 most used in Kenya. Search to see all ${(BK.brokers||[]).length}.</p>`:"");
}
function brokerPanel(id){
  const b=BK.brokers.find(x=>x.id===id); const mt=b.platforms.some(p=>p==="mt5"||p==="mt4");
  const routes=[];
  if(mt) routes.push(`<div class="route"><h3>Install the Northcharts Bridge <span class="badge ok">Free · recommended</span></h3>
    <p class="lead" style="margin:0">A small read-only add-on for your MetaTrader. Your password never leaves your computer.</p>
    <ol><li>Download it for <a href="/bridge/NorthchartBridge.mq5">MetaTrader 5</a> or <a href="/bridge/NorthchartBridge.mq4">MetaTrader 4</a>.</li>
    <li>In MetaTrader, open File &gt; Open Data Folder &gt; MQL5 (or MQL4) &gt; Experts, put the file there, then refresh the Navigator.</li>
    <li>Open Tools &gt; Options &gt; Expert Advisors, tick <i>Allow WebRequest for listed URL</i> and add <code>${escH(BK.desk_url)}</code>.</li>
    <li>Drag Northcharts Bridge onto any chart and paste your token.</li></ol>
    <button class="btn2 pri" id="brTok">Get my token</button><div id="brTokOut"></div></div>`);
  if(mt) routes.push(`<div class="route"><h3>Use your investor password <span class="badge mut">No install</span></h3>`+(BK.routes.metaapi?`
    <p class="lead" style="margin:0">Use the <b>investor</b> (read-only) password from your broker, never the master password. Northcharts refuses a master password and does not store it.</p>
    <div class="grid2" style="margin-top:10px"><div class="f"><label>Platform</label><select id="maPlat">${b.platforms.filter(p=>p==="mt5"||p==="mt4").map(p=>`<option value="${p}">${p==="mt5"?"MetaTrader 5":"MetaTrader 4"}</option>`).join("")}</select></div>
    <div class="f"><label>Account type</label><select id="maType"><option value="REAL">Live</option><option value="DEMO">Demo</option><option value="PROP">Prop firm</option></select></div>
    <div class="f"><label>Account number</label><input id="maLogin" inputmode="numeric" autocomplete="off"></div>
    <div class="f"><label>Server</label><input id="maServer" placeholder="e.g. Exness-MT5Real8" autocomplete="off"></div>
    <div class="f" style="grid-column:1/-1"><label>Investor password</label><input id="maPw" type="password" autocomplete="off"></div></div>
    <button class="btn2 pri" id="maGo" style="margin-top:10px">Connect read-only</button>`:`<p class="lead" style="margin:0">Not switched on for this desk. The Bridge does the same job for free.</p>`)+`</div>`);
  if(b.platforms.includes("tradingview")) routes.push(`<div class="route"><h3>TradingView trade sync</h3><p class="lead" style="margin:0">If you trade ${escH(b.name)} through TradingView, run <code>tools/start_tradingview_sync.ps1</code> with <code>DESK_TOKEN</code> set to a token from Advanced, below.</p></div>`);
  routes.push(`<div class="route"><h3>Upload a statement</h3><p class="lead" style="margin:0">MetaTrader: History tab, right-click &gt; Report &gt; HTML. cTrader and prop firms: export history as CSV. Trades already in your journal are skipped.</p>
    <div class="grid2" style="margin-top:10px"><div class="f"><label>File</label><input id="stFile" type="file" accept=".htm,.html,.csv,.txt"></div>
    <div class="f"><label>Times in the file are</label><select id="stOff">${[0,1,2,3,-4,-5].map(o=>`<option value="${o}">UTC${o>0?"+"+o:o<0?o:""}${o===0?" (Exness, cTrader)":o===2||o===3?" (most MetaTrader servers)":""}</option>`).join("")}</select></div>
    <div class="f"><label>Account type</label><select id="stType"><option value="REAL">Live</option><option value="DEMO">Demo</option><option value="PROP">Prop firm</option></select></div>
    <div class="f"><label>Account name</label><input id="stLabel" autocomplete="off" placeholder="${escH(b.name.toUpperCase().replace(/[^A-Z0-9]+/g,"-"))}-01"></div></div>
    <div id="stMap"></div><button class="btn2 pri" id="stGo" style="margin-top:10px">Import trades</button></div>`);
  let h=`<div class="bhead"><div style="flex:1;min-width:0"><div class="t1">${escH(b.name)}${b.cma?' <span class="badge ok">CMA licensed</span>':""}</div>
    <div class="t2">${escH(b.platform_names.join(", "))}${b.note?" · "+escH(b.note):""}</div></div><button class="btn2" id="bClose">Close</button></div>`
    +(!b.cma&&!b.prop&&b.id!=="other_mt"?`<div class="note warn" style="margin-top:12px">${escH(b.name)} is not on the CMA register of brokers licensed in Kenya. Northcharts can still journal its trades; check where your money is held before you deposit.</div>`:"")+routes[0];
  if(routes.length>1) h+=`<details class="more"><summary>Other ways to connect (${routes.length-1})</summary>${routes.slice(1).join("")}</details>`;
  $("bpanel").innerHTML=h; $("bpanel").dataset.b=id; showBrokers($("bq").value);
  $("bClose").onclick=()=>{ $("bpanel").innerHTML=""; delete $("bpanel").dataset.b; showBrokers($("bq").value) };
  const tok=$("brTok"); if(tok) tok.onclick=async()=>{ try{ const j=await post("/api/auth/tokens",{label:b.name+" Bridge"});
    $("brTokOut").innerHTML=`<p class="lead" style="margin:10px 0 0">Paste this into the Bridge. It is shown once.</p><code class="k">${escH(j.token)}</code>`; load() }catch(e){ flash(e.message) } };
  const ma=$("maGo"); if(ma) ma.onclick=async()=>{ ma.disabled=true; try{ await post("/api/brokers/metaapi",{broker:b.name,platform:$("maPlat").value,account_type:$("maType").value,
    login:$("maLogin").value,server:$("maServer").value,password:$("maPw").value}); $("maPw").value=""; flash("Connected. The first sync runs in the background.",true); loadBrokers() }
    catch(e){ flash(e.message) } finally{ ma.disabled=false } };
  $("stGo").onclick=()=>importStatement(b, null);
}
async function importStatement(b, mapping){
  const f=$("stFile").files[0]; if(!f) return flash("Choose a file first");
  const buf=new Uint8Array(await f.arrayBuffer()); let bin=""; for(let i=0;i<buf.length;i+=0x8000) bin+=String.fromCharCode.apply(null,buf.subarray(i,i+0x8000));
  try{ const j=await post("/api/brokers/import",{filename:f.name,content_b64:btoa(bin),offset_hours:+$("stOff").value,account_type:$("stType").value,
      account_label:$("stLabel").value,broker:b.name,mapping});
    flash(`${j.found} trade(s) found: ${j.added} new, ${j.duplicates} already in your journal.`,true); $("stMap").innerHTML=""; loadBrokers() }
  catch(e){ flash(e.message); if(e.data&&e.data.headers&&e.data.headers.length) mapUI(b,e.data) }
}
function mapUI(b,j){
  const fields=[["instrument","Symbol"],["direction","Buy or sell"],["entry_price","Entry price"],["exit_price","Exit price"],["volume","Size"],
    ["open_time_utc","Open time"],["close_time_utc","Close time"],["stop_loss","Stop loss"],["net_pl","Net profit"],["gross_pl","Profit"],["broker_trade_id","Trade ID"]];
  $("stMap").innerHTML=`<p class="lead" style="margin:12px 0 6px">Match the columns in your file:</p><div class="grid2">`+fields.map(([k,l])=>`<div class="f"><label>${l}${["instrument","direction","entry_price","exit_price"].includes(k)?" (needed)":""}</label><select data-map="${k}"><option value="">Not in file</option>${j.headers.map(h=>`<option${(j.mapping||{})[k]===h?" selected":""}>${escH(h)}</option>`).join("")}</select></div>`).join("")+"</div>";
  $("stGo").onclick=()=>{ const m={}; document.querySelectorAll("[data-map]").forEach(s=>{ if(s.value) m[s.dataset.map]=s.value }); importStatement(b,m) };
}
$("bq").addEventListener("input",e=>showBrokers(e.target.value));
document.addEventListener("click",async e=>{ const c=e.target.closest("[data-b]"); if(c){ brokerPanel(c.dataset.b); return }
  const rm=e.target.closest("[data-rm]"); if(rm){ if(!confirm("Disconnect this broker? Trades already in your journal stay.")) return;
    try{ await post("/api/brokers/remove",{id:rm.dataset.rm}); loadBrokers() }catch(err){ flash(err.message) } return }
  const sy=e.target.closest("[data-sync]"); if(sy){ try{ const j=await post("/api/brokers/sync",{id:sy.dataset.sync}); flash(`${j.added} new trade(s)`,true); loadBrokers() }catch(err){ flash(err.message) } } });
loadBrokers();
load();
</script>"""
    return page("Account", inner, script, nav="account")


LEGAL = {
    "terms": ("Terms of use", """
<p>These terms cover your use of Northcharts, a trading research desk run by Precifarm AI Ltd, Nairobi, Kenya.</p>
<h2>What Northcharts is</h2><p>Northcharts shows market data, the desk's analysis of it, and a journal of trades you take
elsewhere. It is research software. It does not hold money, execute trades, or act as a broker or investment adviser.</p>
<h2>Your account</h2><p>Keep your password private. You are responsible for what happens under your account. Tell the desk
owner straight away if you think someone else has used it.</p>
<h2>Broker connections</h2><p>You connect a broker with read-only credentials (an investor password, a read-only key or a
statement file). Northcharts only reads closed trades and account history. You can disconnect at any time, which deletes
the stored credentials.</p>
<h2>Plans and payments</h2><p>Desk calls, the chart and the manual journal are free. Plus and Pro are paid monthly,
in advance, through M-Pesa (Safaricom) or a card (Paystack). Prices are listed in US dollars and charged in
Kenyan shillings at the rate shown before you pay. Cards renew automatically; M-Pesa renews by a payment prompt or a
Ratiba standing order you approve. We email you before every renewal. You can cancel at any time and keep the plan until
the end of the period you paid for. Upgrades start at once with credit for unused time; downgrades start at the next
renewal. If a renewal is not paid within three days the account moves to Free and your data is kept. Payments are not
refunded for part of a period, except where the law requires it or a charge was made in error.</p>
<h2>Acceptable use</h2><p>Do not try to break into other accounts, overload the service, or resell the desk's data.</p>
<h2>No warranty</h2><p>Data can be late, wrong or missing. The service is provided as it is, without any promise that it
will be uninterrupted or error free.</p>
<h2>Changes</h2><p>These terms may change. Material changes will be shown when you next sign in.</p>"""),
    "privacy": ("Privacy notice", """
<p>Precifarm AI Ltd runs Northcharts and is responsible for your personal data under the Kenya Data Protection Act, 2019.</p>
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
<p>Northcharts' signals, grades, levels and agent answers are information for research. They are not personal
recommendations, they do not take your circumstances into account, and past results do not predict future results.</p>
<p>Before trading, make sure you understand the risks and use a broker licensed by the Capital Markets Authority
of Kenya or by the regulator where you live. Seek independent advice if you are unsure.</p>"""),
}


def legal(kind):
    title, body = LEGAL[kind]
    inner = f'<main class="legal"><h1>{html.escape(title)}</h1><p class="fine" style="text-align:left">Last updated 21 November 2026</p>{body}</main>'
    return page(title, inner)
