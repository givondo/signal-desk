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

const TOOLS = [
  { name: "tv_status", description: "Check that TradingView Desktop is open with sync access, which chart tabs are open, the broker shown in the Trading Panel and which tables are visible. Read-only.",
    inputSchema: { type: "object", properties: {} } },
  { name: "tv_read_closed_trades", description: "Read closed trades from the TradingView Trading Panel (History tab) as structured rows. Read-only; nothing is changed in TradingView.",
    inputSchema: { type: "object", properties: { limit: { type: "number", description: "max rows, default 50" } } } },
  { name: "tv_sync_now", description: "Read closed trades from TradingView and post new ones into the Signal Desk tracker inbox. Duplicates are skipped. Does not touch TradingView.",
    inputSchema: { type: "object", properties: {} } },
  { name: "desk_summary", description: "Signal Desk tracker summary: net worth, live/prop/practice performance, prop cash, trades waiting in the inbox.",
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
          capabilities: { tools: {} }, serverInfo: { name: "signal-desk-tradingview", version: "1.0.0" } } });
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
