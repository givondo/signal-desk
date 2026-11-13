// node --test tools/   (Node 22+)
import test from "node:test";
import assert from "node:assert/strict";
import { mapClosedTrades } from "./tv_trade_sync.mjs";

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
