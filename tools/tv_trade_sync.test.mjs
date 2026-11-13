// node --test tools/   (Node 22+)
import test from "node:test";
import assert from "node:assert/strict";
import { mapClosedTrades, toIso } from "./tv_trade_sync.mjs";

test("maps a closed-trade history table and skips working orders", () => {
  const panel = { tables: [
    { headers: ["Symbol", "Side", "Qty", "Avg Fill Price", "Close Price", "Close Time", "Realized P&L", "Commission", "Swap", "Position ID"],
      rows: [["OANDA:XAUUSD", "Buy", "0.1", "2,650.10", "2,660.30", "2026-11-13 14:00:05", "102.00", "-0.70", "-1.20", "8812"],
             ["XAUUSD", "Sell", "0.1", "2,661.00", "2,665.00", "2026-11-13 15:00:00", "−40.00", "0.70", "0.50", "8813"]] },
    { headers: ["Symbol", "Side", "Qty", "Limit Price", "Status"], rows: [["XAUUSD", "Sell", "0.1", "2700", "Working"]] },
  ] };
  const f = mapClosedTrades(panel);
  assert.equal(f.length, 2);
  assert.equal(f[0].instrument, "XAUUSD");
  assert.equal(f[0].direction, "BUY");
  assert.equal(f[0].entry_price, 2650.1);
  assert.equal(f[0].exit_price, 2660.3);
  assert.equal(f[0].commission, 0.7);      // fee is a cost either way
  assert.equal(f[0].swap, 1.2);            // negative swap is a cost
  assert.equal(f[1].gross_pl, -40);        // unicode minus handled
  assert.equal(f[1].swap, -0.5);           // positive swap is a credit
  assert.equal(f[1].broker_trade_id, "8813");
});

test("ignores tables without an exit price or realised P&L", () => {
  const panel = { tables: [{ headers: ["Symbol", "Side", "Qty", "Avg Fill Price", "Unrealized P&L"],
    rows: [["XAUUSD", "Buy", "0.1", "2650", "12.00"]] }] };
  assert.deepEqual(mapClosedTrades(panel), []);
});

test("maps Paper Trading trade history (stacked exit/entry cells) and skips open trades", () => {
  const panel = { tables: [{
    headers: ["Symbol", "Side", "Type", "Date and time", "Order ID", "Price", "Size", "Net PnL", "Return", ""],
    rows: [
      ["D ¦ DERIV:XAUUSD", "SHORT", "Exit ¦ Entry", "Open ¦ Oct 12, 2026, 17:18", "— ¦ 1000000003", "— ¦ 4,163.263", "15 ¦ 62.45 KUSD", "0 USD", "—", ""],
      ["F ¦ FX:XAUUSD", "SHORT", "Exit ¦ Entry", "Oct 7, 2026, 23:21 ¦ Oct 7, 2026, 23:20", "1000000002 ¦ 1000000001", "4,108.88 ¦ 4,108.58", "1 ¦ 4.11 KUSD", "−0.30 USD", "−0.01%", ""],
    ] }] };
  const f = mapClosedTrades(panel);
  assert.equal(f.length, 1);
  assert.equal(f[0].instrument, "XAUUSD");
  assert.equal(f[0].direction, "SELL");
  assert.equal(f[0].volume, 1);
  assert.equal(f[0].entry_price, 4108.58);
  assert.equal(f[0].exit_price, 4108.88);
  assert.equal(f[0].net_pl, -0.3);
  assert.equal(f[0].broker_trade_id, "1000000001-1000000002");
  assert.equal(f[0].open_time_utc, "2026-10-07T23:20:00+00:00");
  assert.equal(f[0].close_time_utc, "2026-10-07T23:21:00+00:00");
});

test("reads TradingView display times in the chart time zone", () => {
  assert.equal(toIso("Oct 7, 2026, 23:21", 0), "2026-10-07T23:21:00+00:00");
  assert.equal(toIso("Oct 7, 2026, 23:21", 180), "2026-10-07T20:21:00+00:00");
  assert.equal(toIso("2026-11-13 14:00:05", 0), "2026-11-13T14:00:05+00:00");
  assert.equal(toIso("2026-11-13T14:00:05Z"), "2026-11-13T14:00:05+00:00");
  assert.equal(toIso("Open"), "");
});
