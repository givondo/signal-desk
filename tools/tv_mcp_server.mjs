#!/usr/bin/env node
/**
 * Signal Desk x TradingView MCP server (stdio, read-only on TradingView).
 *
 * Lets Claude Desktop see your TradingView Trading Panel and your Signal Desk
 * tracker. TradingView access is read-only: no tool places, modifies or
 * cancels an order, changes a chart, or types into TradingView. The only write
 * is posting closed trades into your own tracker inbox (tv_sync_now).
 *
 * Register in Claude Desktop (%APPDATA%\Claude\claude_desktop_config.json):
 *   "signal-desk-tradingview": { "command": "node", "args": ["C:/Users/<you>/Dev/signal-desk/tools/tv_mcp_server.mjs"] }
 */
import { readPanels, mapClosedTrades, syncOnce } from "./tv_trade_sync.mjs";

const DESK_URL = (process.env.DESK_URL || "http://localhost:8899").replace(/\/$/, "");
const authHeaders = () => process.env.DESK_USER
  ? { Authorization: "Basic " + Buffer.from(`${process.env.DESK_USER}:${process.env.DESK_PASS || ""}`).toString("base64") } : {};
async function desk(path) {
  const r = await fetch(DESK_URL + path, { headers: authHeaders() });
  if (!r.ok) throw new Error(`Signal Desk answered ${r.status} at ${DESK_URL}${path}`);
  return r.json();
}
async function deskPost(path, body) {
  const r = await fetch(DESK_URL + path, { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || `Signal Desk answered ${r.status} at ${DESK_URL}${path}`);
  return j;
}
const ticketView = (r) => ({ route_id: r.route_id, signal: r.signal, notes: r.notes,
  accounts: r.accounts.map(a => ({ account: a.account, type: a.type, status: a.status, decision: a.decision,
    side: a.side, lots: a.lots, money_at_risk: a.money_at_risk, entry: a.entry, stop: a.stop, tp1: a.tp1,
    reasons: a.reasons, checks: a.checks })) });

const TOOLS = [
  { name: "tv_status", description: "Check that TradingView Desktop is open with sync access, which chart tabs are open, the broker shown in the Trading Panel and which tables are visible. Read-only.",
    inputSchema: { type: "object", properties: {} } },
  { name: "tv_read_closed_trades", description: "Read closed trades from the TradingView Trading Panel (History tab) as structured rows. Read-only; nothing is changed in TradingView.",
    inputSchema: { type: "object", properties: { limit: { type: "number", description: "max rows, default 50" } } } },
  { name: "tv_sync_now", description: "Read closed trades from TradingView and post new ones into the Signal Desk tracker inbox. Duplicates are skipped. Does not touch TradingView.",
    inputSchema: { type: "object", properties: {} } },
  { name: "desk_summary", description: "Signal Desk tracker summary: net worth, live/prop/practice performance, prop cash, trades waiting in the inbox.",
    inputSchema: { type: "object", properties: {} } },
  { name: "desk_route_signal", description: "Route the desk's current signal for a symbol across the live/prop/practice portfolio: per account TICKET, WATCH, DRY_RUN or BLOCKED with lot size and the risk checks. Read-only on brokers: never places an order (decision D-011).",
    inputSchema: { type: "object", properties: { sym: { type: "string", description: "Desk symbol, default XAUUSD" } } } },
  { name: "desk_route_idea", description: "Size a manual trade idea (symbol, BUY/SELL, entry, stop, optional target) for every account in the portfolio with the same checks as desk signals. Logs the route on the desk; never places an order.",
    inputSchema: { type: "object", required: ["symbol", "direction", "entry", "stop"], properties: {
      symbol: { type: "string" }, direction: { type: "string", enum: ["BUY", "SELL"] },
      entry: { type: "number" }, stop: { type: "number" }, tp1: { type: "number" } } } },
  { name: "desk_router_accounts", description: "Portfolio accounts in the router: type (PERSONAL, PROP, PAPER, DEMO), status, risk %, equity source, today's P/L and losses in a row.",
    inputSchema: { type: "object", properties: {} } },
  { name: "desk_pending_trades", description: "Trades synced from TradingView that still need a stop, risk and review before entering the journal.",
    inputSchema: { type: "object", properties: {} } },
];

async function callTool(name, a = {}) {
  if (name === "tv_status") {
    const panels = await readPanels();
    return panels.map(p => ({ chart: p.url, broker: p.broker, account: p.account, activeTab: p.activeTab, tabs: p.tabs,
      tables: p.tables.map(t => ({ headers: t.headers, rows: t.rows.length })) }));
  }
  if (name === "tv_read_closed_trades") {
    const lim = Math.min(Math.max(Number(a.limit) || 50, 1), 500);
    return (await readPanels()).flatMap(p => mapClosedTrades(p).map(f => ({ broker: p.broker, ...f, raw: undefined }))).slice(0, lim);
  }
  if (name === "tv_sync_now") return syncOnce({ log: (m) => console.error(m) });
  if (name === "desk_summary") {
    const s = await desk("/api/tracker/summary");
    const m = (x) => x.metrics.n ? { n: x.metrics.n, win_rate: x.metrics.win_rate, expectancy_r: x.metrics.expectancy_r,
      lower95_r: x.metrics.expectancy_r_lower95, profit_factor: x.metrics.profit_factor, verdict: x.metrics.verdict } : { n: 0 };
    return { net_worth_kes: s.net_worth.net_worth_kes, liquid_net_worth_kes: s.net_worth.liquid_net_worth_kes,
      personal: m(s.trades.personal), prop: m(s.trades.prop), practice: m(s.trades.practice),
      prop_cash: { fees_kes: s.prop_economics.fees_paid_kes, payouts_kes: s.prop_economics.payouts_received_kes },
      prop_accounts: s.prop_accounts, pending_fills: s.pending_fills, integrity: s.integrity };
  }
  if (name === "desk_route_signal")
    return ticketView(await desk("/api/router/route?sym=" + encodeURIComponent(String(a.sym || "XAUUSD").toUpperCase())));
  if (name === "desk_route_idea") {
    for (const k of ["entry", "stop"]) if (!Number.isFinite(Number(a[k]))) throw new Error(`${k} must be a number`);
    return ticketView(await deskPost("/api/router/route", { symbol: a.symbol, direction: a.direction, entry: Number(a.entry),
      stop: Number(a.stop), tp1: a.tp1 === undefined ? null : Number(a.tp1), mode: "EXECUTE", source: "claude-mcp" }));
  }
  if (name === "desk_router_accounts")
    return (await desk("/api/router/accounts")).accounts.map(x => ({ label: x.label, type: x.type, status: x.status,
      risk_pct: x.risk_pct, rules_preset: x.rules_preset, equity: x.state.equity, equity_source: x.state.source, today: x.today }));
  if (name === "desk_pending_trades") return (await desk("/api/tracker/fills/pending")).fills;
  throw new Error(`unknown tool ${name}`);
}

// --------------------------------------------------------------- JSON-RPC over stdio
const send = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");
let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", async (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
    if (!line) continue;
    let req;
    try { req = JSON.parse(line); } catch { continue; }
    const { id, method, params } = req;
    if (id === undefined) continue;                     // notifications
    try {
      if (method === "initialize") {
        send({ jsonrpc: "2.0", id, result: { protocolVersion: params?.protocolVersion || "2025-06-18",
          capabilities: { tools: {} }, serverInfo: { name: "signal-desk-tradingview", version: "1.1.0" } } });
      } else if (method === "tools/list") {
        send({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
      } else if (method === "tools/call") {
        try {
          const out = await callTool(params?.name, params?.arguments || {});
          send({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: JSON.stringify(out, null, 2) }] } });
        } catch (e) {
          send({ jsonrpc: "2.0", id, result: { isError: true, content: [{ type: "text", text: e.message }] } });
        }
      } else if (method === "ping") {
        send({ jsonrpc: "2.0", id, result: {} });
      } else {
        send({ jsonrpc: "2.0", id, error: { code: -32601, message: `method not found: ${method}` } });
      }
    } catch (e) {
      send({ jsonrpc: "2.0", id, error: { code: -32603, message: e.message } });
    }
  }
});
console.error("signal-desk-tradingview MCP server ready");
