#!/usr/bin/env node
/**
 * TradingView -> Signal Desk trade sync (read-only).
 *
 * Reads closed trades from the Trading Panel (Account Manager) of TradingView
 * (web window or Desktop) through the local debug port, and posts them to the Signal Desk
 * tracker inbox (/api/tracker/import/fills). It never clicks, types, places,
 * modifies or cancels anything: the only page code it runs reads table text.
 *
 * Open TradingView first with tools/start_tradingview_web.ps1 (web, any plan)
 * or tools/start_tradingview_sync.ps1 (Desktop, paid plans).
 *
 * Usage:
 *   node tools/tv_trade_sync.mjs --dump            print what the panel shows (for checking)
 *   node tools/tv_trade_sync.mjs --once            read and post once
 *   node tools/tv_trade_sync.mjs --watch 60        read and post every 60 seconds
 *
 * Settings (environment variables):
 *   DESK_URL           default http://localhost:8899
 *   DESK_USER, DESK_PASS  only if the desk asks for a password (live site)
 *   TV_ACCOUNT_LABEL   e.g. PEPPERSTONE-TV-01 (default: broker name + "-TV")
 *   TV_ACCOUNT_TYPE    PERSONAL | DEMO | PROP (default PERSONAL)
 *   TV_BROKER          override the detected broker name (e.g. "Paper Trading")
 *   TV_TZ_OFFSET_MIN   chart time zone offset from UTC in minutes (default 0 = UTC)
 *   CDP_PORT           default 9222
 *
 * Requires Node 22+ (built-in fetch and WebSocket). No packages to install.
 */

const args = process.argv.slice(2);
const DESK_URL = (process.env.DESK_URL || "http://localhost:8899").replace(/\/$/, "");
const CDP = `http://127.0.0.1:${process.env.CDP_PORT || 9222}`;

// --------------------------------------------------------------- page reader
// Runs inside TradingView. Read-only: collects visible tables in the bottom
// trading panel as {tab, headers, rows}. No events, no clicks, no writes.
const READER = String.raw`(() => {
  const txt = (el) => (el ? (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim() : "");
  // Cells can stack two values (Paper Trading shows exit above entry); keep them apart with " ¦ ".
  const cell = (el) => (el ? (el.innerText || el.textContent || "").split(/\n+/).map(s => s.replace(/\s+/g, " ").trim()).filter(Boolean).join(" ¦ ") : "");
  const root = document.querySelector('[data-name="account-manager"]')
            || document.querySelector('[class*="accountManager"]')
            || document.querySelector('[class*="bottom-widgetbar-content"]') || document.body;
  const out = { broker: "", account: "", activeTab: "", tables: [] };
  const brokerEl = root.querySelector('[data-name="broker-name"], [class*="brokerName"], [class*="broker-title"], img[alt]');
  out.broker = brokerEl ? (brokerEl.getAttribute("alt") || txt(brokerEl)) : "";
  if (!out.broker) {
    const amBtn = [...document.querySelectorAll('[aria-label="Open account manager"], [title="Open account manager"]')].find(b => txt(b));
    out.broker = amBtn ? txt(amBtn) : "";
  }
  const acc = root.querySelector('[data-name="account-selector"], [class*="accountSelector"], [class*="account-title"]');
  out.account = txt(acc);
  const active = root.querySelector('[role="tab"][aria-selected="true"], [class*="tab"][class*="selected"], [class*="isActive"]');
  out.activeTab = txt(active);
  out.tabs = [...root.querySelectorAll('[role="tab"]')].map(txt).filter(Boolean).slice(0, 20);
  const seen = new Set();
  const pushTable = (headers, rows, kind) => {
    const key = headers.join("|") + rows.length;
    if (!headers.length || seen.has(key)) return;
    seen.add(key);
    out.tables.push({ kind, headers, rows: rows.slice(0, 1000) });
  };
  for (const t of root.querySelectorAll("table")) {
    const headers = [...t.querySelectorAll("thead th, thead td")].map(txt);
    const rows = [...t.querySelectorAll("tbody tr")].map(tr => [...tr.children].map(cell)).filter(r => r.some(Boolean));
    pushTable(headers, rows, "table");
  }
  for (const g of root.querySelectorAll('[role="table"], [role="grid"], [role="treegrid"]')) {
    const headers = [...g.querySelectorAll('[role="columnheader"]')].map(txt);
    const rows = [...g.querySelectorAll('[role="row"]')]
      .map(r => [...r.querySelectorAll('[role="gridcell"], [role="cell"]')].map(cell))
      .filter(r => r.length && r.some(Boolean));
    pushTable(headers, rows, "grid");
  }
  return out;
})()`;

async function cdpTargets() {
  const r = await fetch(`${CDP}/json/list`).catch(() => null);
  if (!r || !r.ok) throw new Error("TradingView is not open with sync access. Run tools/start_tradingview_web.ps1 (or start_tradingview_sync.ps1) first.");
  return r.json();
}

async function evaluate(wsUrl, expression) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const timer = setTimeout(() => { ws.close(); reject(new Error("TradingView did not answer")); }, 15000);
    ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate",
      params: { expression, returnByValue: true, awaitPromise: true } }));
    ws.onerror = () => { clearTimeout(timer); reject(new Error("could not connect to TradingView")); };
    ws.onmessage = (m) => {
      const r = JSON.parse(m.data);
      if (r.id !== 1) return;
      clearTimeout(timer); ws.close();
      if (r.result?.exceptionDetails) reject(new Error(r.result.exceptionDetails.text));
      else resolve(r.result?.result?.value);
    };
  });
}

export async function readPanels() {
  const pages = (await cdpTargets()).filter(t => t.type === "page" && /tradingview\.com\/chart/.test(t.url));
  if (!pages.length) throw new Error("Open a chart in TradingView (the Trading Panel lives on chart tabs).");
  const results = [];
  for (const p of pages) results.push({ url: p.url, ...(await evaluate(p.webSocketDebuggerUrl, READER)) });
  return results;
}

// --------------------------------------------------------------- mapping
const COLS = {
  id: ["position id", "trade id", "deal id", "ticket", "order id", "id"],
  symbol: ["symbol", "instrument", "ticker", "market"],
  side: ["side", "direction", "type"],
  qty: ["qty", "quantity", "volume", "amount", "units", "lots", "size"],
  entry: ["avg open price", "open price", "entry price", "avg fill price", "avg price", "fill price", "price"],
  exit: ["avg close price", "close price", "closing price", "exit price"],
  openTime: ["open time", "opening time", "opened", "placing time", "time"],
  closeTime: ["close time", "closing time", "closed", "execution time", "time"],
  pnl: ["realized p&l", "realised p&l", "realized pnl", "realised pnl", "net p&l", "net pnl", "net profit", "profit", "p&l", "pnl", "pl"],
  commission: ["commission", "fees", "fee"],
  swap: ["swap", "rollover", "financing"],
  sl: ["stop loss", "sl"],
  tp: ["take profit", "tp"],
  status: ["status"],
};
const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9&% ]/g, " ").replace(/\s+/g, " ").trim();
function colIndex(headers, names, used) {
  const h = headers.map(norm);
  for (const n of names) {
    const i = h.findIndex((x, k) => !used.has(k) && x === n);
    if (i >= 0) return i;
  }
  for (const n of names) {
    const i = h.findIndex((x, k) => !used.has(k) && x.startsWith(n));
    if (i >= 0) return i;
  }
  return -1;
}
const parts = (s) => String(s ?? "").split("¦").map(x => x.trim());
const num = (s) => {
  if (s == null) return "";
  let raw = parts(s)[0].replace(/[−–]/g, "-");
  const k = /\d\s*K\s*[A-Z]{0,3}$/i.test(raw) ? 1000 : 1;   // "62.45 KUSD"
  const t = raw.replace(/[^0-9.\-]/g, "");
  return t && t !== "-" && !isNaN(Number(t)) ? Number(t) * k : "";
};
const costOf = (v) => (v === "" ? "" : Math.abs(v));
const swapCost = (v) => (v === "" ? "" : -v);

// TradingView shows times in the chart's time zone (bottom right of the chart).
// Set TV_TZ_OFFSET_MIN to that zone's offset from UTC in minutes (UTC = 0, Nairobi = 180).
const TZ_OFFSET_MIN = Number(process.env.TV_TZ_OFFSET_MIN || 0) || 0;
const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };
export function toIso(s, offsetMin = TZ_OFFSET_MIN) {
  s = parts(s)[0];
  if (!s) return "";
  let y, mo, d, h = 0, mi = 0, se = 0, m;
  if ((m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/))) {
    if (/(Z|[+-]\d{2}:?\d{2})$/.test(s)) { const z = new Date(s); return isNaN(z) ? "" : z.toISOString().replace(".000Z", "+00:00"); }
    [y, mo, d, h, mi, se] = [+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)];
  } else if ((m = s.match(/^([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4}),? (\d{1,2}):(\d{2})(?::(\d{2}))?/))) {
    if (!(m[1].toLowerCase() in MONTHS)) return "";
    [y, mo, d, h, mi, se] = [+m[3], MONTHS[m[1].toLowerCase()], +m[2], +m[4], +m[5], +(m[6] || 0)];
  } else return "";
  const ms = Date.UTC(y, mo, d, h, mi, se) - offsetMin * 60000;
  return new Date(ms).toISOString().replace(".000Z", "+00:00");
}
const symbolOf = (s) => parts(s).pop().split(/[\s:]/).pop().toUpperCase();

/**
 * TradingView Paper Trading "Trade history": one row per trade, with stacked
 * cells (exit value above entry value) for Type, Date and time, Order ID and Price.
 * Size shows quantity above trade value. Open trades show "Open" as the exit time.
 */
export function isTradeHistory(t) {
  const h = (t.headers || []).map(norm);
  return h.includes("type") && h.includes("date and time") && h.some(x => x === "net pnl" || x === "net p&l");
}
function mapTradeHistory(t) {
  const h = t.headers.map(norm);
  const at = (name) => h.indexOf(name);
  const I = { sym: at("symbol"), side: at("side"), type: at("type"), time: at("date and time"), id: at("order id"),
    price: at("price"), size: at("size"), pnl: h.findIndex(x => x === "net pnl" || x === "net p&l") };
  const out = [];
  for (const r of t.rows) {
    const types = parts(r[I.type]).map(x => x.toLowerCase());
    const pick = (col, which) => { const p = parts(r[col]); const k = types.indexOf(which); return k >= 0 ? (p[k] ?? "") : ""; };
    const closeTime = toIso(pick(I.time, "exit"));
    const exitId = pick(I.id, "exit"), entryId = pick(I.id, "entry");
    const exit = num(pick(I.price, "exit"));
    if (!closeTime || exit === "") continue;            // still open
    const sym = symbolOf(r[I.sym]);
    const side = /short|sell/i.test(r[I.side]) ? "SELL" : /long|buy/i.test(r[I.side]) ? "BUY" : "";
    if (!sym || !side) continue;
    out.push({
      broker_trade_id: [entryId, exitId].filter(x => x && x !== "—").join("-") || [sym, side, closeTime].join("|"),
      instrument: sym, direction: side, volume: num(parts(r[I.size])[0]),
      entry_price: num(pick(I.price, "entry")), exit_price: exit,
      open_time_utc: toIso(pick(I.time, "entry")), close_time_utc: closeTime,
      net_pl: num(r[I.pnl]), gross_pl: "", commission: "", swap: "", stop_loss: "", take_profit: "",
      raw: JSON.stringify(Object.fromEntries(t.headers.map((x, i) => [x, r[i]]))),
    });
  }
  return out;
}

/** Pick tables that list CLOSED trades (they have an exit price or a realised P&L). */
export function mapClosedTrades(panel) {
  const fills = [];
  for (const t of panel.tables || []) {
    if (isTradeHistory(t)) { fills.push(...mapTradeHistory(t)); continue; }
    const used = new Set();
    const idx = {};
    for (const key of ["id", "symbol", "side", "qty", "exit", "entry", "closeTime", "openTime", "pnl", "commission", "swap", "sl", "tp", "status"]) {
      idx[key] = colIndex(t.headers, COLS[key], used);
      if (idx[key] >= 0) used.add(idx[key]);
    }
    const looksClosed = idx.symbol >= 0 && idx.side >= 0 && (idx.exit >= 0 || idx.pnl >= 0);
    if (!looksClosed) continue;
    for (const r of t.rows) {
      const get = (k) => (idx[k] >= 0 ? r[idx[k]] : "");
      const status = norm(get("status"));
      if (status && /(working|pending|placed|cancel|reject|inactive)/.test(status)) continue;
      const exit = num(get("exit"));
      const pnl = num(get("pnl"));
      if (exit === "" && pnl === "") continue;
      const sym = symbolOf(get("symbol"));
      const side = /sell|short/i.test(get("side")) ? "SELL" : /buy|long/i.test(get("side")) ? "BUY" : "";
      if (!sym || !side) continue;
      const closeTime = toIso(get("closeTime"));
      const id = get("id") || [sym, side, get("qty"), get("entry"), exit, closeTime].join("|");
      fills.push({
        broker_trade_id: String(id).slice(0, 120), instrument: sym, direction: side,
        volume: num(get("qty")), entry_price: num(get("entry")), exit_price: exit,
        open_time_utc: idx.openTime !== idx.closeTime ? toIso(get("openTime")) : "", close_time_utc: closeTime,
        // Journal convention: commission and swap are costs (subtracted from gross).
        // Brokers show fees as negative or positive numbers; a fee is always a cost.
        // A negative swap is a cost, a positive swap a credit.
        gross_pl: pnl, commission: costOf(num(get("commission"))), swap: swapCost(num(get("swap"))),
        stop_loss: num(get("sl")), take_profit: num(get("tp")),
        raw: JSON.stringify(Object.fromEntries(t.headers.map((h, i) => [h, r[i]]))),
      });
    }
  }
  return fills;
}

// --------------------------------------------------------------- desk
async function postFills(broker, fills) {
  const headers = { "Content-Type": "application/json" };
  if (process.env.DESK_USER) {
    headers.Authorization = "Basic " + Buffer.from(`${process.env.DESK_USER}:${process.env.DESK_PASS || ""}`).toString("base64");
  }
  const body = {
    source: "tradingview", broker,
    account_label: process.env.TV_ACCOUNT_LABEL || `${broker.toUpperCase().replace(/[^A-Z0-9]+/g, "-")}-TV`,
    account_type: process.env.TV_ACCOUNT_TYPE || "PERSONAL", fills,
  };
  const r = await fetch(`${DESK_URL}/api/tracker/import/fills`, { method: "POST", headers, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({ ok: false, error: `desk answered ${r.status}` }));
  if (!j.ok) throw new Error(j.error || `desk answered ${r.status}`);
  return j;
}

export async function syncOnce({ log = console.log } = {}) {
  const panels = await readPanels();
  let total = { read: 0, added: 0, duplicates: 0, auto_journaled: 0 };
  for (const p of panels) {
    const broker = process.env.TV_BROKER || p.broker || "TradingView broker";
    const fills = mapClosedTrades(p);
    total.read += fills.length;
    if (!fills.length) continue;
    const res = await postFills(broker, fills);
    total.added += res.added; total.duplicates += res.duplicates; total.auto_journaled += res.auto_journaled;
    if (res.errors?.length) log("desk notes: " + res.errors.join("; "));
  }
  return { ...total, panels: panels.length };
}

async function main() {
  if (args.includes("--dump")) {
    const panels = await readPanels();
    for (const p of panels) {
      console.log(JSON.stringify({ url: p.url, broker: p.broker, account: p.account, activeTab: p.activeTab, tabs: p.tabs,
        tables: p.tables.map(t => ({ kind: t.kind, headers: t.headers, rows: t.rows.length, sample: t.rows.slice(0, 2) })),
        mapped: mapClosedTrades(p).slice(0, 3) }, null, 2));
    }
    return;
  }
  const wi = args.indexOf("--watch");
  if (wi >= 0) {
    const every = Math.max(15, Number(args[wi + 1]) || 60) * 1000;
    console.log(`Syncing TradingView trades to ${DESK_URL} every ${every / 1000}s. Ctrl+C to stop.`);
    for (;;) {
      try {
        const r = await syncOnce();
        if (r.added || r.auto_journaled) console.log(new Date().toISOString(), JSON.stringify(r));
      } catch (e) { console.log(new Date().toISOString(), "waiting:", e.message); }
      await new Promise(res => setTimeout(res, every));
    }
  }
  console.log(JSON.stringify(await syncOnce()));
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("tv_trade_sync.mjs")) {
  main().catch(e => { console.error(e.message); process.exit(1); });
}
