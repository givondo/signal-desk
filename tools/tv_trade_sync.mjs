#!/usr/bin/env node
/**
 * TradingView -> Signal Desk trade sync (read-only).
 *
 * Reads closed trades from the Trading Panel (Account Manager) of TradingView
 * Desktop through the local debug port, and posts them to the Signal Desk
 * tracker inbox (/api/tracker/import/fills). It never clicks, types, places,
 * modifies or cancels anything: the only page code it runs reads table text.
 *
 * Start TradingView with tools/start_tradingview_sync.ps1 first.
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
 *   TV_BROKER          override the detected broker name
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
  const root = document.querySelector('[data-name="account-manager"]')
            || document.querySelector('[class*="accountManager"]')
            || document.querySelector('[class*="bottom-widgetbar-content"]') || document.body;
  const out = { broker: "", account: "", activeTab: "", tables: [] };
  const brokerEl = root.querySelector('[data-name="broker-name"], [class*="brokerName"], [class*="broker-title"], img[alt]');
  out.broker = brokerEl ? (brokerEl.getAttribute("alt") || txt(brokerEl)) : "";
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
    const rows = [...t.querySelectorAll("tbody tr")].map(tr => [...tr.children].map(txt)).filter(r => r.some(Boolean));
    pushTable(headers, rows, "table");
  }
  for (const g of root.querySelectorAll('[role="table"], [role="grid"], [role="treegrid"]')) {
    const headers = [...g.querySelectorAll('[role="columnheader"]')].map(txt);
    const rows = [...g.querySelectorAll('[role="row"]')]
      .map(r => [...r.querySelectorAll('[role="gridcell"], [role="cell"]')].map(txt))
      .filter(r => r.length && r.some(Boolean));
    pushTable(headers, rows, "grid");
  }
  return out;
})()`;

async function cdpTargets() {
  const r = await fetch(`${CDP}/json/list`).catch(() => null);
  if (!r || !r.ok) throw new Error("TradingView is not open with sync access. Run tools/start_tradingview_sync.ps1 first.");
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
  pnl: ["realized p&l", "realised p&l", "net p&l", "net profit", "profit", "p&l", "pl"],
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
const num = (s) => {
  if (s == null) return "";
  const t = String(s).replace(/[−–]/g, "-").replace(/[^0-9.\-]/g, "");
  return t && !isNaN(Number(t)) ? Number(t) : "";
};
const costOf = (v) => (v === "" ? "" : Math.abs(v));
const swapCost = (v) => (v === "" ? "" : -v);
function toIso(s) {
  if (!s) return "";
  const d = new Date(String(s).replace(/(\d{4}-\d{2}-\d{2}) (\d)/, "$1T$2"));
  return isNaN(d) ? "" : d.toISOString().replace(".000Z", "+00:00");
}

/** Pick tables that list CLOSED trades (they have an exit price or a realised P&L). */
export function mapClosedTrades(panel) {
  const fills = [];
  for (const t of panel.tables || []) {
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
      const sym = String(get("symbol")).split(/[\s:]/).pop().toUpperCase();
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
