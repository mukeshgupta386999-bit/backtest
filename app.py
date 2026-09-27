import http.server
import json
import threading
import os
import hmac
import hashlib
import time
import requests

# ============================================================
# DELTA INDIA TESTNET CONFIG
# ============================================================
API_KEY    = os.getenv("DELTA_API_KEY", "M3QCY7ccOy1EJcQuzwKk7nh5dOCEpi")
API_SECRET = os.getenv("DELTA_API_SECRET", "5FZrF4yBOzwUk2hdPiZpc95Krz8dQzdarTTGwGQsNeFXPMdEiJ4hFKrhLAdo")

BASE_URL = "https://cdn-ind.testnet.deltaex.org"

# ============================================================
# DELTA API SIGNED REQUEST
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
            return {"error": f"Non-JSON response: {r.text[:200]}", "status_code": r.status_code}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# ORDER EXECUTION
# ============================================================
def process_order(data):
    try:
        side = str(data.get('side', '')).lower()
        action = str(data.get('action', '')).lower()

        if 'exit' in side or 'close' in side or 'exit' in action or 'close' in action:
            print("Exit signal mila — positions close kar rahe hain...")
            positions = delta_request('GET', '/v2/positions/margined')
            if isinstance(positions, dict) and 'result' in positions:
                for pos in positions['result']:
                    size = float(pos.get('size', 0))
                    if size > 0:
                        close_side = 'sell' if pos.get('side') == 'buy' else 'buy'
                        close_body = {
                            "product_symbol": pos.get('product_symbol'),
                            "size": int(size),
                            "side": close_side,
                            "order_type": "market_order"
                        }
                        res = delta_request('POST', '/v2/orders', close_body)
                        print(f"Close order: {res}")
            return

        raw_size = float(data.get('size', 1))
        size = int(raw_size) if raw_size >= 1 else 1

        order_body = {
            "product_symbol": "BTCUSD",
            "size": size,
            "side": side,
            "order_type": "market_order"
        }

        print(f"Executing -> BTCUSD, {side}, size={size}")
        result = delta_request('POST', '/v2/orders', order_body)
        print("Order response:", result)

    except Exception as e:
        print("Order Execution Error:", str(e))


# ============================================================
# WEBHOOK SERVER
# ============================================================
class WebhookHandler(http.server.BaseHTTPRequestHandler):

    def _send(self, code, payload):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    # ✅ GET route — Render health check ke liye
    def do_GET(self):
        if self.path in ('/', '/health', '/healthz'):
            self._send(200, {"status": "ok", "service": "tradingview-delta-webhook"})
        else:
            self._send(404, {"status": "not found"})

    def do_HEAD(self):
        self._send(200, {"status": "ok"})

    def do_POST(self):
        if self.path == '/webhook':
            try:
                length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(length)
                data = json.loads(post_data.decode('utf-8'))
                print("TradingView se data mila:", data)

                self._send(200, {"status": "success"})

                threading.Thread(target=process_order, args=(data,), daemon=True).start()
            except Exception as e:
                print("Server Error:", str(e))
                self._send(200, {"status": "error", "message": str(e)})
        else:
            self._send(404, {"status": "not found"})

    def log_message(self, format, *args):
        pass


# ============================================================
# ✅ RENDER PORT FIX
# ============================================================
if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    server_address = ('0.0.0.0', port)
    httpd = http.server.HTTPServer(server_address, WebhookHandler)
    print(f"Webhook Server {port} par chal raha hai...")
    print(f"Delta Base URL: {BASE_URL}")
    httpd.serve_forever()
