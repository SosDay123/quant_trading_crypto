import hmac, hashlib, base64, json, requests
from datetime import datetime, timezone

API_KEY = "22d70968-3c37-467a-b551-7894db8d9b6e"
SECRET_KEY = "08C878DFCCFBDD51EAAB856197465376"
PASSPHRASE = "zH468457@"
BASE_URL = "https://www.okx.com"

def sign(ts, method, path, body=""):
    msg = ts + method + path + body
    mac = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def test_api(method, path, data=None):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    body = json.dumps(data) if data else ""
    headers = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sign(ts, method, path, body),
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
        "x-simulated-trading": "0",
    }
    url = BASE_URL + path
    if method == "GET":
        r = requests.get(url, headers=headers, timeout=10)
    else:
        r = requests.post(url, headers=headers, data=body, timeout=10)
    print(f"{method} {path}")
    print(f"  Response: {r.json()}")
    return r.json()

# Test 1: 查余额
print("=== Test 1: Get Balance ===")
test_api("GET", "/api/v5/account/balance")

# Test 2: 查余额带参数
print("\n=== Test 2: Get Balance with ccy ===")
test_api("GET", "/api/v5/account/balance?ccy=USDT")

