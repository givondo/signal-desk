//+------------------------------------------------------------------+
//| Northcharts Bridge for MetaTrader 4                               |
//| Sends your closed trades to your Northcharts journal.             |
//|                                                                   |
//| Read-only by design: this add-on only reads account history. It   |
//| contains no OrderSend, OrderModify or OrderClose calls, so it     |
//| cannot place, change or cancel trades. It signs in to Northcharts |
//| with a sync token that can only add trades to your journal.       |
//|                                                                   |
//| Setup:                                                            |
//| 1. Northcharts > Account > Sync tokens: create a token, copy it.  |
//| 2. MetaTrader: Tools > Options > Expert Advisors > tick "Allow    |
//|    WebRequest for listed URL" and add your desk address.          |
//| 3. In the Account History tab, right-click > All History, so the  |
//|    terminal has your past trades loaded.                          |
//| 4. Copy this file to File > Open Data Folder > MQL4 > Experts,    |
//|    refresh the Navigator, drag it onto any chart, paste the token.|
//|                                                                   |
//| MT4 keeps only the last stop of each trade, so the journal uses   |
//| that stop. MT5 also records the stop at entry.                    |
//+------------------------------------------------------------------+
#property copyright "Precifarm AI Ltd"
#property link      "https://desktrading-today.precifarm.com"
#property version   "1.00"
#property strict
#property description "Sends closed trades to your Northcharts journal. Read-only: it never trades."

input string DeskUrl        = "https://desktrading-today.precifarm.com"; // Northcharts address
input string SyncToken      = "";                                        // Sync token (starts nci_)
input string BrokerName     = "";                                        // Broker name (blank = from the terminal)
input int    LookbackDays   = 30;                                        // History sent on the first run
input int    IntervalSeconds= 60;                                        // How often to check

string g_key;

int OnInit()
{
   if(StringLen(SyncToken) < 10 || StringSubstr(SyncToken, 0, 4) != "nci_")
   {
      Alert("Northcharts Bridge: paste your sync token (Account > Sync tokens on Northcharts).");
      return(INIT_PARAMETERS_INCORRECT);
   }
   g_key = "NC_BRIDGE_" + IntegerToString(AccountNumber());
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

string Iso(datetime serverTime, int offset)
{
   string s = TimeToString((datetime)(serverTime - offset), TIME_DATE | TIME_SECONDS);
   StringReplace(s, ".", "-");
   StringReplace(s, " ", "T");
   return s + "Z";
}

void Sync()
{
   int offset = (int)(TimeCurrent() - TimeGMT());
   offset = (int)MathRound(offset / 900.0) * 900;            // whole quarter hours: TimeCurrent is the last tick
   datetime since = (datetime)GlobalVariableGet(g_key);
   if(since <= 0) since = TimeCurrent() - LookbackDays * 86400;
   since -= 3600;
   string items = "";
   int count = 0;
   datetime newest = since;
   for(int i = OrdersHistoryTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
      int type = OrderType();
      if(type != OP_BUY && type != OP_SELL) continue;
      if(OrderCloseTime() < since) continue;
      int digits = (int)MarketInfo(OrderSymbol(), MODE_DIGITS);
      if(digits <= 0) digits = 5;
      string r = StringFormat("{\"position\":\"%d\",\"symbol\":\"%s\",\"type\":\"%s\",\"volume\":%s,"
                              "\"open_price\":%s,\"close_price\":%s,\"open_time\":\"%s\",\"close_time\":\"%s\","
                              "\"sl_initial\":0,\"sl_final\":%s,\"tp\":%s,\"commission\":%s,\"swap\":%s,"
                              "\"profit\":%s,\"magic\":%d}",
                              OrderTicket(), Esc(OrderSymbol()), type == OP_BUY ? "buy" : "sell",
                              DoubleToString(OrderLots(), 2), DoubleToString(OrderOpenPrice(), digits),
                              DoubleToString(OrderClosePrice(), digits), Iso(OrderOpenTime(), offset),
                              Iso(OrderCloseTime(), offset), DoubleToString(OrderStopLoss(), digits),
                              DoubleToString(OrderTakeProfit(), digits), DoubleToString(OrderCommission(), 2),
                              DoubleToString(OrderSwap(), 2), DoubleToString(OrderProfit(), 2), OrderMagicNumber());
      items += (count > 0 ? "," : "") + r;
      count++;
      if(OrderCloseTime() > newest) newest = OrderCloseTime();
      if(count >= 200) { if(!Post(items, count)) return; items = ""; count = 0; }
   }
   if(count > 0 && !Post(items, count)) return;
   GlobalVariableSet(g_key, (double)MathMax(newest, since + 3600));
}

bool Post(string items, int count)
{
   string broker = BrokerName != "" ? BrokerName : AccountCompany();
   string mode = IsDemo() ? "DEMO" : "REAL";
   string body = StringFormat("{\"platform\":\"mt4\",\"bridge_version\":\"1.00\",\"broker\":\"%s\",\"server\":\"%s\","
                              "\"login\":\"%d\",\"currency\":\"%s\",\"account_mode\":\"%s\",\"positions\":[%s]}",
                              Esc(broker), Esc(AccountServer()), AccountNumber(), AccountCurrency(), mode, items);
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
      if(err == 4060 || err == 4014)
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
   Print("Northcharts Bridge: sent ", count, " closed trade(s).");
   return true;
}
