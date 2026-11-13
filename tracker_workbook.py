"""Excel workbook export for the trading tracker (stdlib only).

Built fresh from the tracker records on every download. Every total is a live
Excel formula, so editing a figure or adding a row in the blank (blue) lines
updates the summary and the net worth. Colour code: blue = recorded figures
(inputs), black = formulas, green = links to another sheet.

Prop firm nominal balances never enter net worth: only fees paid and payouts
received are real cash.
"""
from __future__ import annotations

from datetime import datetime, timezone

import trading_tracker as tt

num = tt.ta.num

from xlsx_writer import (CALC_NUM, DEFAULT, HEADER, IN_NUM, IN_TEXT, KES, LINK_NUM, NOTE, PCT,
                         SUBHEAD, TITLE, TOTAL, TOTAL_KES, BIG_KES, F, Workbook)

SPARE = {"networth": 15, "trades": 30, "purchases": 10, "payouts": 10, "capital": 20, "recon": 6}


def _txt(v):
    return (v or "").strip()


def _n(v):
    x = num(v)
    return x if x is not None else ""


def _title(ws, title, note, widths):
    ws.set(1, 1, title, TITLE)
    ws.set(2, 1, note, NOTE)
    ws.widths.update(widths)


def build() -> bytes:
    tt.ensure_files()
    wb = Workbook()
    summary = wb.add("Summary")
    nw_ws = wb.add("NetWorth")
    tr_ws = wb.add("Trades")
    pr_ws = wb.add("Prop")
    cap_ws = wb.add("Capital")
    rec_ws = wb.add("Reconciliation")
    readme = wb.add("ReadMe")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    # ------------------------------------------------------------ NetWorth
    _title(nw_ws, "Net worth", "Latest value of each item. Liabilities are entered as positive amounts and subtracted. "
           "Broker accounts come from the latest statement. Prop firm nominal balances are excluded.",
           {1: 12, 2: 30, 3: 18, 4: 11, 5: 7, 6: 15, 7: 9, 8: 11, 9: 16, 10: 26, 11: 34, 13: 20, 14: 16, 15: 12})
    hdr = ["Date", "Item", "Category", "Type", "Liquid", "Amount", "Currency", "FX to KES", "Value (KES)", "Source", "Notes"]
    nw_ws.write_row(4, hdr, HEADER)
    nw_ws.freeze_row = 4
    nw = tt.net_worth()
    r = 5
    for ln in nw["lines"]:
        nw_ws.write_row(r, [(ln["date"], IN_TEXT), (ln["item"], IN_TEXT), (ln["category"], IN_TEXT), (ln["type"], IN_TEXT),
                            (ln["liquid"], IN_TEXT), (ln["amount"], IN_NUM), (ln["currency"], IN_TEXT),
                            (ln["fx_rate_to_kes"] or "", IN_NUM)])
        nw_ws.set(r, 10, ln["source"], DEFAULT)
        nw_ws.set(r, 11, ln["notes"], DEFAULT)
        r += 1
    first, last = 5, r + SPARE["networth"] - 1
    for row in range(5, last + 1):
        nw_ws.set(row, 9, F(f'IF(F{row}="","",IF(D{row}="LIABILITY",-1,1)*F{row}*IF(G{row}="KES",1,N(H{row})))'), CALC_NUM)
        if row >= r:
            for c in (1, 2, 3, 4, 5, 7):
                nw_ws.set(row, c, "", IN_TEXT)
            for c in (6, 8):
                nw_ws.set(row, c, "", IN_NUM)
    rng = lambda col: f"{col}{first}:{col}{last}"  # noqa: E731
    t = last + 2
    nw_rows = {"assets": t, "liabilities": t + 1, "net": t + 2, "liquid": t + 3, "trading": t + 4}
    nw_ws.set(t, 8, "Total assets", TOTAL)
    nw_ws.set(t, 9, F(f'SUMIF({rng("D")},"<>LIABILITY",{rng("I")})'), TOTAL_KES)
    nw_ws.set(t + 1, 8, "Total liabilities", TOTAL)
    nw_ws.set(t + 1, 9, F(f'-SUMIF({rng("D")},"LIABILITY",{rng("I")})'), TOTAL_KES)
    nw_ws.set(t + 2, 8, "NET WORTH", TOTAL)
    nw_ws.set(t + 2, 9, F(f"I{t}-I{t + 1}"), TOTAL_KES)
    nw_ws.set(t + 3, 8, "Liquid net worth", TOTAL)
    nw_ws.set(t + 3, 9, F(f'SUMIFS({rng("I")},{rng("D")},"<>LIABILITY",{rng("E")},"Y")-I{t + 1}'), TOTAL_KES)
    nw_ws.set(t + 4, 8, "Trading accounts", TOTAL)
    nw_ws.set(t + 4, 9, F(f'SUMIF({rng("C")},"TRADING_ACCOUNT",{rng("I")})'), TOTAL_KES)
    # category breakdown
    nw_ws.write_row(4, ["Category", "Value (KES)", "% of net worth"], HEADER, start_col=13)
    for i, cat in enumerate(tt.ASSET_CATEGORIES + tt.LIABILITY_CATEGORIES):
        row = 5 + i
        nw_ws.set(row, 13, cat, DEFAULT)
        nw_ws.set(row, 14, F(f'SUMIF({rng("C")},M{row},{rng("I")})'), KES)
        nw_ws.set(row, 15, F(f"IF($I${t + 2}<=0,0,N{row}/$I${t + 2})"), PCT)
    nw_ws.set(5 + len(tt.ASSET_CATEGORIES + tt.LIABILITY_CATEGORIES) + 1, 13,
              "Categories: " + ", ".join(tt.ASSET_CATEGORIES) + " (assets); " + ", ".join(tt.LIABILITY_CATEGORIES) + " (liabilities).", NOTE)

    # ------------------------------------------------------------ Trades
    tr_ws.widths.update({1: 26, 2: 11, 3: 11, 4: 18, 5: 9, 6: 7, 7: 14, 8: 10, 9: 11, 10: 11, 11: 11,
                         12: 10, 13: 8, 14: 11, 15: 8, 16: 11, 17: 14})
    tr_hdr = ["Trade ID", "Date", "Account type", "Account", "Instrument", "Side", "Setup", "Session", "Entry", "Stop",
              "Exit", "Risk", "Units", "Net P/L", "R", "Rules", "Emotion"]
    tr_ws.write_row(1, tr_hdr, HEADER)
    tr_ws.freeze_row = 1
    trades = sorted(tt.read("trades"), key=lambda x: (x.get("exit_time_utc") or x.get("recorded_at_utc") or ""))
    r = 2
    for x in trades:
        when = (x.get("exit_time_utc") or x.get("recorded_at_utc") or "")[:10]
        tr_ws.write_row(r, [(x.get("trade_id"), IN_TEXT), (when, IN_TEXT), (x.get("account_type"), IN_TEXT),
                            (x.get("account_label"), IN_TEXT), (x.get("instrument"), IN_TEXT), (x.get("direction"), IN_TEXT),
                            (x.get("setup"), IN_TEXT), (x.get("session"), IN_TEXT), (_n(x.get("entry_price")), IN_NUM),
                            (_n(x.get("stop_loss")), IN_NUM), (_n(x.get("exit_price")), IN_NUM),
                            (_n(x.get("initial_risk_amount")), IN_NUM), (x.get("currency"), IN_TEXT),
                            (_n(x.get("net_pl")), IN_NUM)])
        tr_ws.set(r, 16, x.get("rule_compliance"), IN_TEXT)
        tr_ws.set(r, 17, x.get("emotional_state"), IN_TEXT)
        r += 1
    tr_last = r + SPARE["trades"] - 1
    for row in range(2, tr_last + 1):
        tr_ws.set(row, 15, F(f'IF(AND(ISNUMBER(L{row}),L{row}>0,ISNUMBER(N{row})),N{row}/L{row},"")'), CALC_NUM)
        if row >= r:
            for c in (1, 2, 3, 4, 5, 6, 7, 8, 13, 16, 17):
                tr_ws.set(row, c, "", IN_TEXT)
            for c in (9, 10, 11, 12, 14):
                tr_ws.set(row, c, "", IN_NUM)
    tr_ws.autofilter = f"A1:Q{tr_last}"
    T = lambda col: f"Trades!${col}$2:${col}${tr_last}"  # noqa: E731

    # ------------------------------------------------------------ Prop
    _title(pr_ws, "Prop firms: real cash only", "Fees, resets and payouts. Nominal account sizes are for reference and are never counted as money. "
           "Only payouts with status RECEIVED count as cash.",
           {1: 12, 2: 20, 3: 16, 4: 22, 5: 11, 6: 12, 7: 11, 8: 9, 9: 10, 10: 13, 11: 12, 12: 13})
    pr_ws.set(3, 1, "Purchases (challenges, resets, add-ons)", SUBHEAD)
    pr_ws.write_row(4, ["Date", "Account", "Firm", "Programme", "Type", "Nominal (info)", "Fee", "Currency",
                        "FX to KES", "Fee (KES)", "Status", "Refund recd"], HEADER)
    r = 5
    for e in tt.read("evaluations"):
        fee, kes, fx = num(e.get("fee_paid")), num(e.get("fee_kes")), num(e.get("fx_rate_to_kes"))
        if fx is None and fee and kes:
            fx = kes / fee
        pr_ws.write_row(r, [(e.get("purchase_date"), IN_TEXT), (e.get("account_label"), IN_TEXT), (e.get("firm"), IN_TEXT),
                            (e.get("programme"), IN_TEXT), (e.get("purchase_type"), IN_TEXT), (_n(e.get("nominal_size")), IN_NUM),
                            (fee if fee is not None else "", IN_NUM), (e.get("fee_currency"), IN_TEXT), (fx or "", IN_NUM)])
        pr_ws.set(r, 11, e.get("status"), IN_TEXT)
        pr_ws.set(r, 12, _n(e.get("fee_refund_received")), IN_NUM)
        r += 1
    p_first, p_last = 5, r + SPARE["purchases"] - 1
    for row in range(p_first, p_last + 1):
        pr_ws.set(row, 10, F(f'IF(G{row}="","",G{row}*IF(H{row}="KES",1,N(I{row})))'), CALC_NUM)
        if row >= r:
            for c in (1, 2, 3, 4, 5, 8, 11):
                pr_ws.set(row, c, "", IN_TEXT)
            for c in (6, 7, 9, 12):
                pr_ws.set(row, c, "", IN_NUM)
    ps = p_last + 3
    pr_ws.set(ps - 1, 1, "Payouts", SUBHEAD)
    pr_ws.write_row(ps, ["Requested", "Account", "Firm", "Requested amt", "Currency", "Status", "Received amt",
                         "Received on", "Method", "Transfer fees", "FX to KES", "Received (KES)"], HEADER)
    r = ps + 1
    for p in tt.read("payouts"):
        recv, kes, fx, fees = num(p.get("amount_received")), num(p.get("kes_received")), num(p.get("fx_rate_to_kes")), num(p.get("payout_fees")) or 0
        if fx is None and recv and kes:
            fx = kes / max(recv - fees, 1e-9)
        pr_ws.write_row(r, [(p.get("requested_date"), IN_TEXT), (p.get("account_label"), IN_TEXT), (p.get("firm"), IN_TEXT),
                            (_n(p.get("amount_requested")), IN_NUM), (p.get("currency"), IN_TEXT), (p.get("status"), IN_TEXT),
                            (recv if recv is not None else "", IN_NUM), (p.get("received_date"), IN_TEXT),
                            (p.get("method"), IN_TEXT), (fees or "", IN_NUM), (fx or "", IN_NUM)])
        r += 1
    po_first, po_last = ps + 1, r + SPARE["payouts"] - 1
    for row in range(po_first, po_last + 1):
        pr_ws.set(row, 12, F(f'IF(AND(F{row}="RECEIVED",ISNUMBER(G{row})),(G{row}-N(J{row}))*IF(E{row}="KES",1,N(K{row})),"")'), CALC_NUM)
        if row >= r:
            for c in (1, 2, 3, 5, 6, 8, 9):
                pr_ws.set(row, c, "", IN_TEXT)
            for c in (4, 7, 10, 11):
                pr_ws.set(row, c, "", IN_NUM)
    PP = lambda col: f"Prop!${col}${p_first}:${col}${p_last}"  # noqa: E731
    PO = lambda col: f"Prop!${col}${po_first}:${col}${po_last}"  # noqa: E731

    # ------------------------------------------------------------ Capital
    _title(cap_ws, "Personal capital: deposits and withdrawals", "Every movement of your own money into or out of trading accounts, "
           "in original currency and KES. FX source ACTUAL = from a receipt; ESTIMATED = indicative rate.",
           {1: 12, 2: 18, 3: 13, 4: 12, 5: 9, 6: 10, 7: 14, 8: 11, 9: 10, 10: 9, 11: 14, 12: 22})
    cap_ws.write_row(4, ["Date", "Account", "Type", "Amount", "Currency", "FX to KES", "Value (KES)", "FX source",
                         "Charges", "Charge ccy", "Method", "Reference"], HEADER)
    cap_ws.freeze_row = 4
    moves = [(x, "DEPOSIT") for x in tt.read("deposits")] + [(x, "WITHDRAWAL") for x in tt.read("withdrawals")]
    moves.sort(key=lambda m: m[0].get("date") or "")
    r = 5
    for x, kind in moves:
        cap_ws.write_row(r, [(x.get("date"), IN_TEXT), (x.get("account"), IN_TEXT), (kind, IN_TEXT), (_n(x.get("amount")), IN_NUM),
                             (x.get("currency"), IN_TEXT), (_n(x.get("fx_rate_to_kes")), IN_NUM)])
        cap_ws.write_row(r, [(x.get("fx_rate_source"), IN_TEXT), (_n(x.get("charges")), IN_NUM), (x.get("charges_currency"), IN_TEXT),
                             (x.get("payment_method"), IN_TEXT), (x.get("reference"), IN_TEXT)], start_col=8)
        r += 1
    c_first, c_last = 5, r + SPARE["capital"] - 1
    for row in range(c_first, c_last + 1):
        cap_ws.set(row, 7, F(f'IF(D{row}="","",D{row}*IF(E{row}="KES",1,N(F{row})))'), CALC_NUM)
        if row >= r:
            for c in (1, 2, 3, 5, 8, 10, 11, 12):
                cap_ws.set(row, c, "", IN_TEXT)
            for c in (4, 6, 9):
                cap_ws.set(row, c, "", IN_NUM)
    C = lambda col: f"Capital!${col}${c_first}:${col}${c_last}"  # noqa: E731

    # ------------------------------------------------------------ Reconciliation
    _title(rec_ws, "Monthly reconciliation", "Closing equity is rebuilt from its parts. A difference must be explained before the month is marked reconciled.",
           {1: 12, 2: 18, 3: 8, 4: 13, 5: 11, 6: 11, 7: 12, 8: 12, 9: 11, 10: 9, 11: 10, 12: 14, 13: 14, 14: 11, 15: 10})
    rec_ws.write_row(4, ["Period end", "Account", "Ccy", "Opening", "Deposits", "Withdrawals", "Realised P/L", "Unrealised chg",
                         "Commissions", "Swaps", "Other", "Closing (stmt)", "Closing (calc)", "Difference", "Reconciled"], HEADER)
    rec_ws.freeze_row = 4
    r = 5
    for x in sorted(tt.read("reconciliation"), key=lambda y: y.get("period_end") or ""):
        rec_ws.write_row(r, [(x.get("period_end"), IN_TEXT), (x.get("account"), IN_TEXT), (x.get("currency"), IN_TEXT)]
                         + [(_n(x.get(k)), IN_NUM) for k in ("opening_equity", "deposits", "withdrawals", "realised_pl",
                                                             "unrealised_pl_change", "commissions", "swaps", "other_charges",
                                                             "closing_equity_statement")])
        rec_ws.set(r, 15, x.get("reconciled"), IN_TEXT)
        r += 1
    r_first, r_last = 5, r + SPARE["recon"] - 1
    for row in range(r_first, r_last + 1):
        rec_ws.set(row, 13, F(f'IF(D{row}="","",D{row}+N(E{row})-N(F{row})+N(G{row})+N(H{row})-N(I{row})-N(J{row})-N(K{row}))'), CALC_NUM)
        rec_ws.set(row, 14, F(f'IF(OR(L{row}="",M{row}=""),"",L{row}-M{row})'), CALC_NUM)
        if row >= r:
            for c in (1, 2, 3, 15):
                rec_ws.set(row, c, "", IN_TEXT)
            for c in range(4, 13):
                rec_ws.set(row, c, "", IN_NUM)
    RC = lambda col: f"Reconciliation!${col}${r_first}:${col}${r_last}"  # noqa: E731

    # ------------------------------------------------------------ Summary
    _title(summary, "Trading Tracker", f"Built {stamp} from your Signal Desk records. Blue = recorded figures, black = formulas, "
           "green = links to other sheets. Download again from the tracker for the latest figures.",
           {1: 34, 2: 18, 3: 12, 4: 14, 5: 12, 6: 13, 7: 18})
    summary.set(4, 1, "Net worth (KES)", SUBHEAD)
    summary.set(4, 2, "", SUBHEAD)
    summary.set(5, 1, "Net worth", TOTAL)
    summary.set(5, 2, F(f"NetWorth!$I${nw_rows['net']}"), BIG_KES)
    for i, (label, key) in enumerate((("Total assets", "assets"), ("Total liabilities", "liabilities"),
                                      ("Liquid net worth", "liquid"), ("Trading accounts", "trading"))):
        summary.set(6 + i, 1, label)
        summary.set(6 + i, 2, F(f"NetWorth!$I${nw_rows[key]}"), LINK_NUM)
    summary.set(10, 1, "Trading accounts as % of net worth")
    summary.set(10, 2, F("IF(B5<=0,0,B9/B5)"), PCT)

    summary.set(12, 1, "Personal capital", SUBHEAD)
    summary.set(12, 2, "", SUBHEAD)
    rows = [("Deposits (KES)", F(f'SUMIF({C("C")},"DEPOSIT",{C("G")})'), LINK_NUM),
            ("Withdrawals (KES)", F(f'SUMIF({C("C")},"WITHDRAWAL",{C("G")})'), LINK_NUM),
            ("Net deposits (KES)", F("B13-B14"), CALC_NUM),
            ("Trading P/L from statements (statement ccy)",
             F(f"SUM({RC('G')})+SUM({RC('H')})-SUM({RC('I')})-SUM({RC('J')})-SUM({RC('K')})"), LINK_NUM),
            ("Unreconciled months", F(f'COUNTIFS({RC("A")},"<>",{RC("O")},"<>Y")'), LINK_NUM)]
    for i, (label, f, st) in enumerate(rows):
        summary.set(13 + i, 1, label)
        summary.set(13 + i, 2, f, st)

    summary.set(19, 1, "Prop firms (real cash)", SUBHEAD)
    summary.set(19, 2, "", SUBHEAD)
    rows = [("Fees and resets paid (USD)", F(f'SUMIF({PP("H")},"USD",{PP("G")})')),
            ("Fees and resets paid (EUR)", F(f'SUMIF({PP("H")},"EUR",{PP("G")})')),
            ("Fees and resets paid (KES)", F(f"SUM({PP('J')})")),
            ("Payouts received (KES)", F(f"SUM({PO('L')})")),
            ("Payouts pending, NOT cash (USD)", F(f'SUMIFS({PO("D")},{PO("F")},"REQUESTED",{PO("E")},"USD")+SUMIFS({PO("D")},{PO("F")},"APPROVED",{PO("E")},"USD")')),
            ("Evaluations failed", F(f'COUNTIF({PP("K")},"FAILED")')),
            ("Accounts funded", F(f'COUNTIF({PP("K")},"FUNDED")'))]
    for i, (label, f) in enumerate(rows):
        summary.set(20 + i, 1, label)
        summary.set(20 + i, 2, f, LINK_NUM)
    summary.set(27, 1, "Net prop cash result (KES)", TOTAL)
    summary.set(27, 2, F("B23-B22"), TOTAL_KES)

    summary.set(29, 1, "Trading performance", SUBHEAD)
    summary.write_row(30, ["Account type", "Trades", "Win rate", "Net P/L", "Avg R", "Profit factor", "Units"], HEADER)
    for i, (typ, units) in enumerate((("PERSONAL", "account ccy"), ("PROP", "account ccy"), ("DEMO", "account ccy"), ("PAPER", "R"))):
        row = 31 + i
        summary.set(row, 1, typ)
        summary.set(row, 2, F(f'COUNTIF({T("C")},A{row})'), KES)
        summary.set(row, 3, F(f'IF(B{row}=0,0,COUNTIFS({T("C")},A{row},{T("N")},">0")/B{row})'), PCT)
        summary.set(row, 4, F(f'SUMIF({T("C")},A{row},{T("N")})'), CALC_NUM)
        summary.set(row, 5, F(f'IFERROR(AVERAGEIF({T("C")},A{row},{T("O")}),0)'), CALC_NUM)
        summary.set(row, 6, F(f'IFERROR(SUMIFS({T("N")},{T("C")},A{row},{T("N")},">0")/-SUMIFS({T("N")},{T("C")},A{row},{T("N")},"<0"),0)'), CALC_NUM)
        summary.set(row, 7, units)
    summary.set(35, 1, "Rule violations (all accounts)")
    summary.set(35, 2, F(f'COUNTIF({T("P")},"VIOLATED")'), KES)
    summary.set(36, 1, "Sample note: no edge is claimed below 100 trades; see the tracker for confidence bounds.", NOTE)

    # ------------------------------------------------------------ ReadMe
    readme.widths.update({1: 110})
    lines = [
        ("How this workbook works", TITLE),
        ("Download it any time from the tracker (Download Excel). It is rebuilt from your records, so it always shows the latest figures.", DEFAULT),
        ("Every total is a formula. Change a blue figure, or fill in one of the blank blue rows on any sheet, and the totals and Summary update.", DEFAULT),
        ("Rows you add here stay in this file only. To keep them for good, enter them in the tracker; the tracker is the master record.", DEFAULT),
        ("", DEFAULT),
        ("Colours", SUBHEAD),
        ("Blue text: recorded figures (inputs). Black text: formulas. Green text: links pulled from another sheet.", DEFAULT),
        ("", DEFAULT),
        ("Rules built in", SUBHEAD),
        ("Net worth = assets minus liabilities, in KES. Liabilities are entered as positive amounts.", DEFAULT),
        ("Prop firm nominal balances are never net worth. Only fees paid and payouts received are real cash.", DEFAULT),
        ("Pending payouts are shown separately and are not cash.", DEFAULT),
        ("Paper trades are measured in R; live trades in account currency. They are never mixed.", DEFAULT),
        ("Example net worth row (format only): 2026-11-30 | M-Pesa wallet | CASH | ASSET | Y | 25000 | KES | 1", NOTE),
    ]
    for i, (txt, st) in enumerate(lines, 1):
        readme.set(i, 1, txt, st)
    return wb.to_bytes()
