//+------------------------------------------------------------------+
//| Northcharts Bridge for MetaTrader 5                               |
//| Sends your closed trades to your Northcharts journal.             |
//|                                                                   |
//| Read-only by design: this add-on only reads the terminal's trade  |
//| history. It contains no order, modify or close calls, so it cannot|
//| place, change or cancel trades. Your password never leaves the    |
//| terminal; it signs in to Northcharts with a sync token that can   |
//| only add trades to your journal.                                  |
//|                                                                   |
//| Setup:                                                            |
//| 1. Northcharts > Account > Sync tokens: create a token, copy it.  |
//| 2. MetaTrader: Tools > Options > Expert Advisors > tick "Allow    |
//|    WebRequest for listed URL" and add your desk address, e.g.     |
//|    https://desktrading-today.precifarm.com                        |
//| 3. Copy this file to File > Open Data Folder > MQL5 > Experts,    |
//|    refresh the Navigator, drag it onto any chart, paste the token.|
//|    "Allow Algo Trading" is NOT needed.                            |
//+------------------------------------------------------------------+
#property copyright "Precifarm AI Ltd"
#property link      "https://desktrading-today.precifarm.com"
#property version   "1.00"
#property description "Sends closed trades to your Northcharts journal. Read-only: it never trades."

input string DeskUrl        = "https://desktrading-today.precifarm.com"; // Northcharts address
input string SyncToken      = "";                                        // Sync token (starts nci_)
input string BrokerName     = "";                                        // Broker name (blank = from the terminal)
input int    LookbackDays   = 30;                                        // History sent on the first run
input int    IntervalSeconds= 60;                                        // How often to check

string   g_key;
datetime g_lastOk = 0;

int OnInit()
{
   if(StringLen(SyncToken) < 10 || StringSubstr(SyncToken, 0, 4) != "nci_")
   {
      Alert("Northcharts Bridge: paste your sync token (Account > Sync tokens on Northcharts).");
      return(INIT_PARAMETERS_INCORRECT);
   }
   g_key = "NC_BRIDGE_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   EventSetTimer(MathMax(IntervalSeconds, 15));
   Sync();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { Sync(); }
void OnTick() {}

string Esc(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   return s;
}

string Iso(datetime serverTime, long offset)
{
   string s = TimeToString((datetime)(serverTime - offset), TIME_DATE | TIME_SECONDS);
   StringReplace(s, ".", "-");
   StringReplace(s, " ", "T");
   return s + "Z";
}

string Num(double v, int digits) { return DoubleToString(v, digits); }

void Sync()
{
   long offset = (long)(TimeTradeServer() - TimeGMT());      // server clock minus UTC, in seconds
   datetime since = (datetime)GlobalVariableGet(g_key);
   if(since <= 0) since = TimeCurrent() - LookbackDays * 86400;
   since -= 3600;                                            // small overlap; the desk ignores duplicates

   if(!HistorySelect(since, TimeCurrent() + 86400)) return;
   ulong ids[];
   int n = HistoryDealsTotal();
   for(int i = 0; i < n; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      long entry = HistoryDealGetInteger(t, DEAL_ENTRY);
      long type = HistoryDealGetInteger(t, DEAL_TYPE);
      if((entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) || (type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)) continue;
      ulong pid = (ulong)HistoryDealGetInteger(t, DEAL_POSITION_ID);
      bool seen = false;
      for(int k = 0; k < ArraySize(ids); k++) if(ids[k] == pid) { seen = true; break; }
      if(!seen) { int sz = ArraySize(ids); ArrayResize(ids, sz + 1); ids[sz] = pid; }
   }
   if(ArraySize(ids) == 0) { GlobalVariableSet(g_key, (double)TimeCurrent()); return; }

   string items = "";
   int count = 0;
   datetime newest = since;
   for(int k = 0; k < ArraySize(ids); k++)
   {
      ulong pid = ids[k];
      if(PositionSelectByTicket(pid)) continue;               // still partly open: send it once it is closed
      if(!HistorySelectByPosition(pid)) continue;
      double inVol = 0, inVal = 0, outVol = 0, outVal = 0, profit = 0, comm = 0, swap = 0, sl0 = 0, sl1 = 0, tp = 0;
      datetime tOpen = 0, tClose = 0;
      string symbol = "", side = "";
      long magic = 0, reason = -1;
      int m = HistoryDealsTotal();
      for(int j = 0; j < m; j++)
      {
         ulong d = HistoryDealGetTicket(j);
         long e = HistoryDealGetInteger(d, DEAL_ENTRY);
         double vol = HistoryDealGetDouble(d, DEAL_VOLUME), px = HistoryDealGetDouble(d, DEAL_PRICE);
         datetime tm = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
         comm += HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
         swap += HistoryDealGetDouble(d, DEAL_SWAP);
         if(e == DEAL_ENTRY_IN)
         {
            if(tOpen == 0 || tm < tOpen)
            {
               tOpen = tm;
               symbol = HistoryDealGetString(d, DEAL_SYMBOL);
               side = HistoryDealGetInteger(d, DEAL_TYPE) == DEAL_TYPE_BUY ? "buy" : "sell";
               sl0 = HistoryDealGetDouble(d, DEAL_SL);
               if(sl0 == 0)                                    // fall back to the stop on the opening order
                  sl0 = HistoryOrderGetDouble((ulong)HistoryDealGetInteger(d, DEAL_ORDER), ORDER_SL);
               magic = HistoryDealGetInteger(d, DEAL_MAGIC);
            }
            inVol += vol; inVal += vol * px;
         }
         else if(e == DEAL_ENTRY_OUT || e == DEAL_ENTRY_OUT_BY || e == DEAL_ENTRY_INOUT)
         {
            outVol += vol; outVal += vol * px;
            profit += HistoryDealGetDouble(d, DEAL_PROFIT);
            if(tm >= tClose)
            {
               tClose = tm;
               sl1 = HistoryDealGetDouble(d, DEAL_SL);
               tp = HistoryDealGetDouble(d, DEAL_TP);
               reason = HistoryDealGetInteger(d, DEAL_REASON);
            }
         }
      }
      if(inVol <= 0 || outVol <= 0 || symbol == "") continue;
      int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      if(digits <= 0) digits = 5;
      string r = StringFormat("{\"position\":\"%I64u\",\"symbol\":\"%s\",\"type\":\"%s\",\"volume\":%s,"
                              "\"open_price\":%s,\"close_price\":%s,\"open_time\":\"%s\",\"close_time\":\"%s\","
                              "\"sl_initial\":%s,\"sl_final\":%s,\"tp\":%s,\"commission\":%s,\"swap\":%s,"
                              "\"profit\":%s,\"magic\":%I64d,\"reason\":%I64d}",
                              pid, Esc(symbol), side, Num(inVol, 2), Num(inVal / inVol, digits + 1),
                              Num(outVal / outVol, digits + 1), Iso(tOpen, offset), Iso(tClose, offset),
                              Num(sl0, digits), Num(sl1, digits), Num(tp, digits), Num(comm, 2), Num(swap, 2),
                              Num(profit, 2), magic, reason);
      items += (count > 0 ? "," : "") + r;
      count++;
      if(tClose > newest) newest = tClose;
      if(count >= 200) { if(!Post(items, count)) return; items = ""; count = 0; }
   }
   if(count > 0 && !Post(items, count)) return;
   GlobalVariableSet(g_key, (double)newest);
}

bool Post(string items, int count)
{
   string broker = BrokerName != "" ? BrokerName : AccountInfoString(ACCOUNT_COMPANY);
   string mode = AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL ? "REAL" : "DEMO";
   string body = StringFormat("{\"platform\":\"mt5\",\"bridge_version\":\"1.00\",\"broker\":\"%s\",\"server\":\"%s\","
                              "\"login\":\"%I64d\",\"currency\":\"%s\",\"account_mode\":\"%s\",\"positions\":[%s]}",
                              Esc(broker), Esc(AccountInfoString(ACCOUNT_SERVER)), AccountInfoInteger(ACCOUNT_LOGIN),
                              AccountInfoString(ACCOUNT_CURRENCY), mode, items);
   char data[], result[];
   string rh;
   StringToCharArray(body, data, 0, StringLen(body), CP_UTF8);
   string url = DeskUrl;
   if(StringSubstr(url, StringLen(url) - 1) == "/") url = StringSubstr(url, 0, StringLen(url) - 1);
   string headers = "Content-Type: application/json\r\nAuthorization: Bearer " + SyncToken + "\r\n";
   ResetLastError();
   int code = WebRequest("POST", url + "/api/ingest/bridge", headers, 20000, data, result, rh);
   if(code == -1)
   {
      int err = GetLastError();
      if(err == 4014)
         Print("Northcharts Bridge: allow ", url, " in Tools > Options > Expert Advisors > Allow WebRequest.");
      else
         Print("Northcharts Bridge: could not reach the desk (error ", err, "). Will retry.");
      return false;
   }
   if(code != 200)
   {
      Print("Northcharts Bridge: desk answered ", code, ": ", CharArrayToString(result, 0, -1, CP_UTF8));
      return false;
   }
   g_lastOk = TimeCurrent();
   Print("Northcharts Bridge: sent ", count, " closed trade(s).");
   return true;
}
