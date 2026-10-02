import http.server
import json
import threading
import os
import hmac
import hashlib
import time
import requests

API_KEY    = os.getenv("DELTA_API_KEY", "M3QCY7ccOy1EJcQuzwKk7nh5dOCEpi")
API_SECRET = os.getenv("DELTA_API_SECRET", "5FZrF4yBOzwUk2hdPiZpc95Krz8dQzdarTTGwGQsNeFXPMdEiJ4hFKrhLAdo")

BASE_URL = "https://cdn-ind.testnet.deltaex.org"
order_lock = threading.Lock()

active_position = None


# ============================================================
# DELTA API
# ============================================================
def delta_request(method, path, body=None):
    if body is None:
        body = ""
    elif isinstance(body, dict):
        body = json.dumps(body, separators=(',', ':'))

    timestamp = str(int(time.time()))
    signature_data = method + timestamp + path + body
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        'api-key': API_KEY,
        'timestamp': timestamp,
        'signature': signature,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    url = BASE_URL + path
    try:
        if method == 'GET':
            r = requests.get(url, headers=headers, timeout=10)
        elif method == 'POST':
            r = requests.post(url, headers=headers, data=body, timeout=10)
        elif method == 'DELETE':
            r = requests.delete(url, headers=headers, data=body, timeout=10)
        else:
            return {"error": "Unsupported method"}
        try:
            return r.json()
        except Exception:
            return {"error": f"Non-JSON: {r.text[:200]}", "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}


def get_open_positions():
    positions = delta_request('GET', '/v2/positions/margined')
    open_pos = []
    if isinstance(positions, dict) and 'result' in positions:
        for pos in positions['result']:
            try:
                size = abs(float(pos.get('size', 0)))
            except (ValueError, TypeError):
                size = 0
            if size > 0:
                open_pos.append(pos)
    return open_pos


# ============================================================
# ✅ CLOSE POSITIONS
# ============================================================
def close_all_positions():
    global active_position
    print("=" * 50)
    print("🔴 Closing positions...")
    print("=" * 50)
    
    positions = delta_request('GET', '/v2/positions/margined')
    
    if isinstance(positions, dict) and 'result' in positions:
        for pos in positions['result']:
            raw_size = pos.get('size', 0)
            try:
                raw_size_float = float(raw_size)
                size = abs(raw_size_float)
            except (ValueError, TypeError):
                continue
            
            symbol = pos.get('product_symbol', 'N/A')
            
            if size > 0:
                if raw_size_float < 0:
                    close_side = 'buy'
                else:
                    close_side = 'sell'
                
                close_body = {
                    "product_symbol": symbol,
                    "size": int(size),
                    "side": close_side,
                    "order_type": "market_order"
                }
                print(f"📤 Closing {symbol} | Size: {size} | Side: {close_side}")
                res = delta_request('POST', '/v2/orders', close_body)
                print(f"✅ Close response: {res}")
    
    active_position = None
    print("🔴 Done")


def get_product_symbol(symbol_str):
    symbol = symbol_str.upper()
    if symbol.endswith('.P'):
        symbol = symbol[:-2]
    if 'BTC' in symbol: return 'BTCUSD'
    elif 'ETH' in symbol: return 'ETHUSD'
    elif 'XAUT' in symbol or 'XAU' in symbol or 'GOLD' in symbol: return 'XAUTUSD'
    elif 'SOL' in symbol: return 'SOLUSD'
    else: return symbol


# ============================================================
# ✅ ORDER EXECUTION — SIRF SIGNAL BASED
# ============================================================
def process_order(data):
    global active_position
    with order_lock:
        try:
            action = str(data.get('action', '')).lower().strip()
            side   = str(data.get('side', '')).lower().strip()
            signal = str(data.get('signal', '')).lower().strip()
            
            print(f"📥 Action: '{action}' | Side: '{side}' | Signal: '{signal}'")
            
            # ========================================================
            # ✅ EXIT — Support BUY ya Exit SELL par
            # ========================================================
            is_exit = (
                action == 'exit' or
                side == 'exit' or
                signal == 'support_buy' or
                signal == 'exit_sell'
            )
            
            if is_exit:
                print(f"✅ EXIT SIGNAL: '{signal or action}'")
                close_all_positions()
                return
            
            # ========================================================
            # ✅ ENTRY — SIRF Resistance SELL
            # ========================================================
            is_resistance_sell = (
                signal == 'resistance_sell' or
                'resistance_sell' in signal
            )
            
            if is_resistance_sell:
                open_positions = get_open_positions()
                if len(open_positions) > 0:
                    print(f"⚠️ Position already open, SKIP")
                    return
                
                symbol_raw = str(data.get('symbol', 'XAUTUSD')).upper()
                product_symbol = get_product_symbol(symbol_raw)
                
                raw_qty = data.get('qty', data.get('size', 1))
                try:
                    size = int(float(raw_qty))
                    if size < 1: size = 1
                except (ValueError, TypeError):
                    size = 1
                
                order_body = {
                    "product_symbol": product_symbol,
                    "size": size,
                    "side": "sell",
                    "order_type": "market_order"
                }
                
                print(f"✅ RESISTANCE SELL -> {product_symbol}, size={size}")
                result = delta_request('POST', '/v2/orders', order_body)
                print("Order response:", result)
                
                if isinstance(result, dict) and result.get('success'):
                    active_position = {
                        'product_symbol': product_symbol,
                        'side': 'sell'
                    }
                    print(f"📌 SELL tracked")
                return
            
            print(f"⚠️ Unknown action '{action}' | signal '{signal}'")
        except Exception as e:
            print("Order Execution Error:", str(e))


# ============================================================
# WEBHOOK SERVER
# ============================================================
class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/webhook':
            try:
                length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(length)
                raw_str = post_data.decode('utf-8')
                print("RAW:", raw_str)
                
                if not raw_str or not raw_str.strip():
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "skipped"}).encode())
                    return
                
                data = json.loads(raw_str)
                print("TradingView se data:", data)

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())

                threading.Thread(target=process_order, args=(data,)).start()
            except Exception as e:
                print("Server Error:", str(e))
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    # ❌ monitor_position thread HATA DIYA
    
    server_address = ('0.0.0.0', 5000)
    httpd = http.server.HTTPServer(server_address, WebhookHandler)
    print("Webhook Server http://localhost:5000 par chal raha hai...")
    print(f"Delta Base URL: {BASE_URL}")
    print(f"✅ Entry: Resistance SELL only")
    print(f"✅ Exit: Support BUY ya Exit SELL signal par")
    httpd.serve_forever()
