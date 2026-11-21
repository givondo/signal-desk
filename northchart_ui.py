"""Shared Northchart look for every page: one source of design, injected by the server.

The home page (dashboard.html) defines the design. This module carries the same
tokens (colours, fonts, radii, light and dark themes), the same app bar and the
theme switch, and maps the older variable names used by the tracker and router
onto them, so those pages restyle without rewriting every rule.

Pages opt in with two markers: ``<!--NC:HEAD-->`` inside <head> and
``<!--NC:NAV-->`` where the app bar goes. Standard library only, so it deploys
with the ``*.py`` copy step like the rest of the server.
"""

from __future__ import annotations

import html

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;'
         '9..40,700;9..40,800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">')

# Same values as dashboard.html :root and html[data-theme="dark"].
CSS = """
:root{--bg:#ffffff;--p1:#ffffff;--p2:#F9FAFB;--p3:#F3F4F6;--bd:#E5E7EB;--bd2:#D1D5DB;--tx:#111827;--dim:#6B7280;--dim2:#9CA3AF;
--g:#0d9b6e;--r:#d04438;--a:#b8860b;--gold:#c9a227;--b:#2563eb;
--gt:rgba(13,155,110,.12);--rt:rgba(208,68,56,.12);--at:rgba(184,134,11,.14);--bt:rgba(37,99,235,.12);
--shadow:0 1px 2px rgba(16,24,40,.06),0 4px 16px rgba(16,24,40,.06);--shadow-lg:0 8px 32px rgba(16,24,40,.12);
--radius:10px;--radius-sm:6px;--header-bg:rgba(255,255,255,.94);
--nc-font:'DM Sans',system-ui,-apple-system,sans-serif;--nc-mono:'JetBrains Mono',Consolas,monospace}
html[data-theme="dark"]{--bg:#0b0d10;--p1:#12161d;--p2:#181e28;--p3:#222a36;--bd:rgba(255,255,255,.08);--bd2:rgba(255,255,255,.14);
--tx:#f0f2f5;--dim:#98a2b3;--dim2:#667085;--g:#34d399;--r:#f87171;--a:#fbbf24;--gold:#e4c04a;--b:#60a5fa;
--gt:rgba(52,211,153,.14);--rt:rgba(248,113,113,.14);--at:rgba(251,191,36,.16);--bt:rgba(96,165,250,.14);
--shadow:0 1px 2px rgba(0,0,0,.25),0 4px 20px rgba(0,0,0,.35);--shadow-lg:0 12px 40px rgba(0,0,0,.5);--header-bg:rgba(18,22,29,.92)}
/* older page variables, mapped onto the shared tokens (tracker and router) */
:root,html[data-theme="dark"]{--font:var(--nc-font);--mono:var(--nc-mono);
--card:var(--p1);--soft:var(--p2);--soft2:var(--p3);--line:var(--bd);--line2:var(--bd2);--ink:var(--tx);--ink2:var(--tx);
--mute:var(--dim);--mute2:var(--dim2);--brand:var(--b);--brand2:var(--bt);--good:var(--g);--goodbg:var(--gt);
--warn:var(--a);--warnbg:var(--at);--bad:var(--r);--badbg:var(--rt);--info:var(--b);--infobg:var(--bt)}
html{background:var(--bg)}
body{background:var(--bg)!important;color:var(--tx)!important;font-family:var(--nc-font)!important;font-size:13.5px}
.mono,.num,td.n{font-family:var(--nc-mono);font-variant-numeric:tabular-nums}
::-webkit-scrollbar{width:7px;height:7px}::-webkit-scrollbar-thumb{background:var(--bd2);border-radius:99px}
:focus-visible{outline:2px solid var(--b);outline-offset:2px}

/* app bar, same as the home page */
.nc-head{position:sticky;top:0;z-index:40;background:var(--header-bg);backdrop-filter:blur(12px) saturate(1.2);
border-bottom:1px solid var(--bd)}
.nc-bar{display:flex;align-items:center;gap:12px;padding:10px 14px;flex-wrap:wrap;max-width:1920px;margin:0 auto}
.nc-brand{display:flex;align-items:center;gap:10px;min-width:0;text-decoration:none;color:var(--tx)}
.nc-mark{width:36px;height:36px;border-radius:8px;background:#0b0d10;display:flex;align-items:center;justify-content:center;
font:800 13px var(--nc-mono);color:var(--gold);flex-shrink:0;box-shadow:inset 0 0 0 1px rgba(201,162,39,.35)}
.nc-name{font-size:15px;font-weight:800;letter-spacing:.6px;line-height:1.1}
.nc-sub{font-size:10px;color:var(--dim);letter-spacing:.3px;margin-top:1px}
.nc-actions{display:flex;align-items:center;gap:8px;margin-left:auto;flex-wrap:wrap}
.nc-pill{font:500 11px var(--nc-font);color:var(--dim);padding:6px 10px;border-radius:var(--radius-sm);border:1px solid var(--bd);
background:var(--p2);cursor:pointer;transition:all .15s;white-space:nowrap;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.nc-pill:hover{color:var(--tx);border-color:var(--bd2)}
.nc-pill.on{color:var(--tx);border-color:var(--bd2);background:var(--p3);font-weight:600}
.nc-icon{background:var(--p2);border:1px solid var(--bd2);color:var(--dim);width:36px;height:36px;border-radius:var(--radius-sm);
cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:15px}
.nc-icon:hover{color:var(--tx);background:var(--p3)}
.nc-sec{display:flex;align-items:center;gap:6px;padding:8px 14px;border-top:1px solid var(--bd);overflow-x:auto;scrollbar-width:thin}
@media(max-width:700px){.nc-bar{padding:8px 10px}.nc-sub{display:none}}

/* shared components so older page markup matches the home page */
.btn{font-family:var(--nc-font)!important;background:var(--p2)!important;color:var(--tx)!important;border:1px solid var(--bd2)!important;
border-radius:var(--radius-sm)!important;font-weight:600!important}
.btn:hover{border-color:var(--b)!important;color:var(--b)!important}
.btn.pri{background:var(--b)!important;border-color:var(--b)!important;color:#fff!important}
.btn.pri:hover{filter:brightness(1.06);color:#fff!important}
.card{background:var(--p1)!important;border:1px solid var(--bd)!important;border-radius:var(--radius)!important;box-shadow:var(--shadow)!important}
.card.flat{background:var(--p2)!important;box-shadow:none!important}
input,select,textarea{font-family:var(--nc-font);background:var(--p2);color:var(--tx);border:1px solid var(--bd2);border-radius:var(--radius-sm)}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--b);box-shadow:0 0 0 3px var(--bt)}
table{color:var(--tx)}th{color:var(--dim)}
h1{font-weight:800;letter-spacing:-.2px}
"""

THEME_BOOT = """<script>(function(){try{var t=localStorage.getItem('sd-theme-v2')==='dark'?'dark':'light';
document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>"""

THEME_SWITCH = """<script>(function(){
function cur(){return document.documentElement.getAttribute('data-theme')==='dark'?'dark':'light'}
function paint(){var b=document.getElementById('nc-theme');if(b){var d=cur()==='dark';b.textContent=d?'\\u2600':'\\u263E';
b.title=d?'Light mode':'Dark mode';b.setAttribute('aria-label',b.title)}
var m=document.querySelector('meta[name=theme-color]');if(m)m.content=cur()==='dark'?'#0b0d10':'#ffffff'}
window.ncToggleTheme=function(){var t=cur()==='dark'?'light':'dark';document.documentElement.setAttribute('data-theme',t);
try{localStorage.setItem('sd-theme-v2',t)}catch(e){}paint();window.dispatchEvent(new CustomEvent('nc-theme',{detail:t}))};
window.addEventListener('storage',function(e){if(e.key==='sd-theme-v2'){document.documentElement.setAttribute('data-theme',
e.newValue==='dark'?'dark':'light');paint()}});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',paint);else paint();})();</script>"""

PAGES = {
    "journal": ("Journal", "Every trade you take, journalled automatically"),
    "account": ("Account", "Profile, brokers, sync tokens and your data"),
}


def head() -> str:
    return (FONTS + '<meta name="theme-color" content="#ffffff">'
            '<link rel="manifest" href="/manifest.webmanifest">'
            '<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">'
            '<link rel="icon" type="image/png" href="/icons/icon-192.png">'
            + THEME_BOOT + "<style>" + CSS + "</style>")


def nav(page: str) -> str:
    title, sub = PAGES.get(page, ("", ""))
    links = [("/", "Desk", "home"), ("/journal", "Journal", "journal"), ("/#agents", "Agents", "agents"),
             ("/account", "Account", "account")]
    current = ' aria-current="page"'
    pills = "".join(
        f'<a class="nc-pill{" on" if key == page else ""}" href="{href}"'
        f'{current if key == page else ""}>{html.escape(label)}</a>'
        for href, label, key in links)
    return (f'<header class="nc-head"><div class="nc-bar">'
            f'<a class="nc-brand" href="/" aria-label="Northchart home"><div class="nc-mark" aria-hidden="true">N</div>'
            f'<div><div class="nc-name">Northchart</div><div class="nc-sub">{html.escape(title)} · {html.escape(sub)}</div></div></a>'
            f'<nav class="nc-actions" aria-label="Main">{pills}'
            f'<button type="button" class="nc-icon" id="nc-theme" onclick="ncToggleTheme()">☾</button></nav>'
            f'</div></header>' + THEME_SWITCH)


def inject(doc: bytes, page: str) -> bytes:
    text = doc.decode("utf-8")
    text = text.replace("<!--NC:HEAD-->", head(), 1).replace("<!--NC:NAV-->", nav(page), 1)
    return text.encode("utf-8")
