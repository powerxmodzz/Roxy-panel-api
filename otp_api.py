#!/usr/bin/env python3
"""
╔══════════════════════════════════════╗
║   ⚡ POWER MODZ - OTP REST API ⚡     ║
║   Flask API — Pydroid Compatible     ║
╚══════════════════════════════════════╝

Install:
    pip install flask requests beautifulsoup4

Run:
    python otp_api.py

Endpoints:
    GET /otps          — Saare latest OTPs
    GET /otps/new      — Sirf naye OTPs (last fetch ke baad)
    GET /otps/latest   — Sirf 1 latest OTP
    GET /status        — API status + login check
    POST /refresh      — Force refresh (naya data fetch karo)

Example response:
    {
      "success": true,
      "count": 3,
      "otps": [
        {
          "date": "2026-03-02 19:46:23",
          "country": "Vietnam",
          "flag": "🇻🇳",
          "service": "WhatsApp",
          "number": "84903934208",
          "number_masked": "849****208",
          "otp": "949-172",
          "full_message": "Your WhatsApp code 949-172 Dont share..."
        }
      ]
    }
"""

import re
import time
import threading
import logging
import queue
import json
from datetime import datetime, timedelta

import requests as req
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request

# ════════════════════════════════════════
#         ⚙️  SETTINGS
# ════════════════════════════════════════

LOGIN_URL    = "http://www.roxysms.net/Login"
AJAX_URL     = "http://www.roxysms.net/agent/res/data_smscdr.php"
REFERER_URL  = "http://www.roxysms.net/agent/SMSCDRStats"
SIGNIN_URL   = "http://www.roxysms.net/signin"

USERNAME = "powerxtream"
PASSWORD = "Khang1.com"

API_PORT         = int(__import__('os').environ.get('PORT', 5000))  # Railway auto PORT
REFRESH_INTERVAL = 5          # Background mein har 5s refresh
DAYS_BACK        = 2          # Kitne din pehle ka data fetch karo

# Optional API Key — security ke liye (khali chhodo = no auth)
API_KEY = ""   # Example: "mySecretKey123"

# ════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Global State ──
session   = req.Session()
session.headers.update({
    "User-Agent"      : "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
    "Connection"      : "keep-alive",
    "Accept"          : "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding" : "gzip, deflate",
})

all_otps    = []          # Saare fetched OTPs
seen_uids   = set()       # Duplicate check
new_otps    = []          # Last refresh ke baad naye
last_refresh = None       # Last refresh time
is_logged_in = False      # Login state
lock         = threading.Lock()

# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────

COUNTRY_MAP = {
    "263": ("🇿🇼","Zimbabwe"), "234":("🇳🇬","Nigeria"), "254":("🇰🇪","Kenya"),
    "212": ("🇲🇦","Morocco"),  "855":("🇰🇭","Cambodia"),"856":("🇱🇦","Laos"),
    "880": ("🇧🇩","Bangladesh"),"966":("🇸🇦","Saudi Arabia"),"971":("🇦🇪","UAE"),
    "964": ("🇮🇶","Iraq"),     "963":("🇸🇾","Syria"),    "961":("🇱🇧","Lebanon"),
    "973": ("🇧🇭","Bahrain"),  "974":("🇶🇦","Qatar"),    "968":("🇴🇲","Oman"),
    "967": ("🇾🇪","Yemen"),    "962":("🇯🇴","Jordan"),   "380":("🇺🇦","Ukraine"),
    "92":  ("🇵🇰","Pakistan"), "84": ("🇻🇳","Vietnam"),  "62": ("🇮🇩","Indonesia"),
    "63":  ("🇵🇭","Philippines"),"66":("🇹🇭","Thailand"),"60": ("🇲🇾","Malaysia"),
    "95":  ("🇲🇲","Myanmar"),  "65": ("🇸🇬","Singapore"),"91": ("🇮🇳","India"),
    "86":  ("🇨🇳","China"),    "82": ("🇰🇷","South Korea"),"81":("🇯🇵","Japan"),
    "44":  ("🇬🇧","UK"),       "27": ("🇿🇦","South Africa"),"20":("🇪🇬","Egypt"),
    "55":  ("🇧🇷","Brazil"),   "52": ("🇲🇽","Mexico"),   "90": ("🇹🇷","Turkey"),
    "98":  ("🇮🇷","Iran"),     "33": ("🇫🇷","France"),   "49": ("🇩🇪","Germany"),
    "7":   ("🇷🇺","Russia"),   "1":  ("🇺🇸","USA"),
}

SERVICE_MAP = {
    "whatsapp":"WhatsApp","telegram":"Telegram","facebook":"Facebook",
    "instagram":"Instagram","twitter":"Twitter","tiktok":"TikTok",
    "google":"Google","microsoft":"Microsoft","apple":"Apple",
    "paypal":"PayPal","binance":"Binance","bybit":"Bybit","uber":"Uber",
}

def get_country(number):
    n = re.sub(r'\D','',number)
    for l in [3,2,1]:
        if n[:l] in COUNTRY_MAP:
            return COUNTRY_MAP[n[:l]]
    return ("🌍","Unknown")

def detect_service(cli, sms):
    combined = (cli+" "+sms).lower()
    for k,v in SERVICE_MAP.items():
        if k in combined:
            return v
    return cli.strip() or "Unknown"

def extract_otp(text):
    m = re.search(r'\b\d{3}[-\s]\d{3}\b', text)
    if m: return m.group()
    m = re.search(r'G-(\d{4,8})', text)
    if m: return "G-"+m.group(1)
    m = re.search(r'\b(\d{4,8})\b', text)
    if m: return m.group(1)
    return "N/A"

def mask_phone(number):
    n = re.sub(r'\D','',number)
    if len(n) >= 8:
        return n[:3]+"****"+n[-3:]
    return n

def solve_math(question):
    clean = re.sub(r'[^0-9\+\-\*\/]','',question)
    try:
        if clean and all(c in '0123456789+-*/' for c in clean):
            return str(int(eval(clean)))
    except:
        pass
    return "0"

# ─────────────────────────────────────────
#  AUTH CHECK
# ─────────────────────────────────────────

def check_api_key():
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    return key == API_KEY

# ─────────────────────────────────────────
#  LOGIN
# ─────────────────────────────────────────

def do_login():
    global is_logged_in
    try:
        # Step 1: Login page GET
        r0 = session.get(LOGIN_URL, timeout=15)
        soup0 = BeautifulSoup(r0.text, "html.parser")

        # Step 2: Math captcha solve
        cap = re.search(r'What\s+is\s+([\d\s\+\-\*\/]+)\s*=\s*\?', soup0.get_text(), re.IGNORECASE)
        ans = solve_math(cap.group(1)) if cap else "0"
        log.info(f"Captcha: {cap.group(1).strip() if cap else 'N/A'} = {ans}")

        # Step 3: Form fields collect
        login_data = {}
        for inp in soup0.find_all("input"):
            ph  = (inp.get("placeholder","")).lower()
            nm  = inp.get("name","")
            tp  = (inp.get("type","text")).lower()
            if not nm or tp in ["submit","button","reset"]: continue
            if "user" in ph:   login_data[nm] = USERNAME
            elif "pass" in ph: login_data[nm] = PASSWORD
            elif "answer" in ph or "captcha" in nm.lower(): login_data[nm] = ans
            else: login_data[nm] = inp.get("value","")

        log.info(f"Login data keys: {list(login_data.keys())}")

        # Step 4: POST to signin
        r1 = session.post(SIGNIN_URL, data=login_data, timeout=15, allow_redirects=True)
        resp_text = r1.text.lower()

        # Step 5: Success check — multiple conditions
        success_signals = ["logout", "dashboard", "smscdr", "sms module", "my profile"]
        fail_signals    = ["invalid", "wrong", "incorrect", "sign into your account"]

        is_success = any(s in resp_text for s in success_signals)
        is_fail    = any(s in resp_text for s in fail_signals)

        if is_success and not is_fail:
            is_logged_in = True
        elif is_fail:
            is_logged_in = False
            log.warning(f"Login fail — wrong credentials ya captcha")
        else:
            # URL check as fallback
            is_logged_in = "login" not in r1.url.lower() and r1.status_code == 200

        log.info(f"Login: {'✅ OK' if is_logged_in else '❌ FAIL'} | URL: {r1.url}")
        return is_logged_in

    except Exception as e:
        log.error(f"Login error: {e}")
        return False

# ─────────────────────────────────────────
#  FETCH OTPs
# ─────────────────────────────────────────

def fetch_otps():
    global all_otps, new_otps, last_refresh, seen_uids

    try:
        now   = datetime.now()
        fdate1 = (now - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d") + " 00:00:00"
        fdate2 = now.strftime("%Y-%m-%d %H:%M:%S")

        params = {
            "fdate1": fdate1, "fdate2": fdate2,
            "frange":"", "fclient":"", "fnum":"", "fcli":"",
            "fgdate":"", "fgmonth":"", "fgrange":"",
            "fgclient":"", "fgnumber":"", "fgcli":"", "fg":"0",
            "sEcho":"1", "iDisplayStart":"0", "iDisplayLength":"500", "sSearch":"",
        }
        headers = {
            "Referer"         : REFERER_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Connection"      : "keep-alive",
        }

        r = session.get(AJAX_URL, params=params, headers=headers, timeout=8)

        if "login" in r.url.lower():
            log.warning("Session expire — re-login...")
            do_login()
            return

        data    = r.json()
        aa_data = data.get("aaData", [])
        total   = int(data.get("iTotalRecords", 0))

        fresh_new = []
        fresh_all = []

        for row in aa_data:
            if not isinstance(row, list) or len(row) < 5:
                continue
            if "NAN" in str(row[0]) or str(row[2]) == "0":
                continue

            date_val = str(row[0]).strip()
            number   = re.sub(r'<[^>]+>', '', str(row[2])).strip()
            cli      = re.sub(r'<[^>]+>', '', str(row[3])).strip()
            sms_text = re.sub(r'<[^>]+>', '', str(row[4])).strip()

            if not number or not sms_text:
                continue

            flag, country = get_country(number)
            service       = detect_service(cli, sms_text)
            otp           = extract_otp(sms_text)
            masked        = mask_phone(number)
            uid           = f"{number}|{sms_text[:30]}"

            otp_obj = {
                "uid"           : uid,
                "date"          : date_val,
                "flag"          : flag,
                "country"       : country,
                "service"       : service,
                "number"        : number,
                "number_masked" : "+"+masked,
                "otp"           : otp,
                "full_message"  : sms_text,
            }

            fresh_all.append(otp_obj)
            if uid not in seen_uids:
                seen_uids.add(uid)
                fresh_new.append(otp_obj)

        with lock:
            all_otps     = fresh_all
            new_otps     = fresh_new
            last_refresh = now.strftime("%Y-%m-%d %H:%M:%S")

        # SSE subscribers ko push karo
        for otp_obj in fresh_new:
            push_to_subscribers(otp_obj)

        log.info(f"Fetched {len(fresh_all)} total | {len(fresh_new)} new")

    except Exception as e:
        log.error(f"Fetch error: {e}")

# ─────────────────────────────────────────
#  BACKGROUND REFRESH THREAD
# ─────────────────────────────────────────

def background_refresh():
    log.info(f"Background refresh — har {REFRESH_INTERVAL}s")
    while True:
        try:
            fetch_otps()
        except Exception as e:
            log.error(f"Refresh error: {e}")
        time.sleep(REFRESH_INTERVAL)

# ─────────────────────────────────────────
#  API ENDPOINTS
# ─────────────────────────────────────────

# SSE subscribers list
sse_subscribers = []
sse_lock = threading.Lock()

def push_to_subscribers(otp_obj):
    """Naya OTP aaya — saare SSE subscribers ko push karo"""
    import json
    data = f"data: {json.dumps(otp_obj, ensure_ascii=False)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_subscribers:
            try:
                q.put(data)
            except Exception:
                dead.append(q)
        for q in dead:
            sse_subscribers.remove(q)

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "api"      : "⚡ POWER MODZ OTP API",
        "version"  : "1.0",
        "endpoints": {
            "GET /otps"        : "Saare latest OTPs",
            "GET /otps/new"    : "Sirf naye OTPs (last fetch ke baad)",
            "GET /otps/latest" : "Sirf 1 latest OTP",
            "GET /status"      : "API status",
            "POST /refresh"    : "Force refresh",
            "GET /stream"      : "SSE — Live OTP stream (auto push)",
            "GET /live"        : "Live dashboard browser mein",
        }
    })

@app.route("/stream", methods=["GET"])
def stream():
    """
    Server-Sent Events — Live OTP stream
    Browser/app ek baar connect kare, naye OTP automatically milte rahenge
    Usage:
        EventSource('http://127.0.0.1:5000/stream')
    """
    import queue, json

    def event_stream(q):
        # Welcome message
        yield f"data: {json.dumps({'type':'connected','message':'⚡ POWER MODZ Live Stream Connected!'})}\n\n"
        # Existing OTPs bhi bhejo
        with lock:
            existing = list(all_otps)  # Saare OTPs
        for o in existing:
            yield f"data: {json.dumps(o, ensure_ascii=False)}\n\n"
            time.sleep(0.1)
        # Naye OTPs ka wait
        while True:
            try:
                data = q.get(timeout=8)
                yield data
            except Exception:
                # Heartbeat — connection alive rakhne ke liye
                yield f"data: {json.dumps({'type':'heartbeat','time': datetime.now().strftime('%H:%M:%S'), 'total': len(all_otps)})}\n\n"

    import queue
    q = queue.Queue()
    with sse_lock:
        sse_subscribers.append(q)

    from flask import Response, stream_with_context
    return Response(
        stream_with_context(event_stream(q)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control"              : "no-cache",
            "X-Accel-Buffering"          : "no",
            "Access-Control-Allow-Origin": "*",
        }
    )

@app.route("/live", methods=["GET"])
def live_dashboard():
    """Live HTML dashboard — browser mein kholo"""
    from flask import Response
    html = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚡ POWER MODZ OTP LIVE</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#0a0a0a; color:#fff; font-family:monospace; }
  .header { background:linear-gradient(135deg,#1a1a2e,#16213e);
            padding:15px; text-align:center; border-bottom:2px solid #7b2fff; }
  .header h1 { color:#7b2fff; font-size:20px; }
  .status-bar { background:#111; padding:8px 15px; font-size:12px;
                display:flex; justify-content:space-between; border-bottom:1px solid #222; }
  .dot { width:8px; height:8px; border-radius:50%; background:#00ff88;
         display:inline-block; margin-right:5px; animation:pulse 1s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .counter { color:#7b2fff; font-weight:bold; }
  #feed { padding:10px; overflow-y:auto; }
  .otp-card { background:#111; border:1px solid #222; border-left:3px solid #7b2fff;
              border-radius:8px; padding:12px; margin-bottom:10px;
              animation:slideIn 0.3s ease; }
  @keyframes slideIn { from{opacity:0;transform:translateY(-10px)} to{opacity:1;transform:translateY(0)} }
  .otp-card.new { border-left-color:#00ff88; }
  .row { display:flex; justify-content:space-between; margin:3px 0; font-size:13px; }
  .label { color:#888; }
  .value { color:#fff; font-weight:bold; }
  .otp-val { color:#00ff88; font-size:16px; letter-spacing:2px; }
  .number { color:#7b2fff; }
  .time-val { color:#888; font-size:11px; }
  .msg { background:#0a0a0a; padding:6px 8px; border-radius:4px;
         font-size:11px; color:#aaa; margin-top:6px; word-break:break-all; }
  .badge { background:#7b2fff; color:#fff; padding:2px 6px;
           border-radius:10px; font-size:10px; margin-left:5px; }
  .empty { text-align:center; color:#444; padding:40px; font-size:14px; }
</style>
</head>
<body>
<div class="header">
  <h1>⚡ POWER MODZ OTP LIVE</h1>
</div>
<div class="status-bar">
  <div><span class="dot" id="dot"></span><span id="status">Connecting...</span></div>
  <div>Total: <span class="counter" id="count">0</span></div>
  <div id="last-time">--:--:--</div>
</div>
<div id="feed"><div class="empty">🔄 Connecting to live stream...</div></div>

<script>
let totalCount = 0;
const feed = document.getElementById('feed');
const dot  = document.getElementById('dot');

function addCard(otp, isNew=true) {
  if (otp.type) return; // heartbeat skip
  totalCount++;
  // No card limit — saare show honge
  document.getElementById('count').textContent = totalCount;
  document.getElementById('last-time').textContent = new Date().toLocaleTimeString();

  if (feed.querySelector('.empty')) feed.innerHTML = '';

  const card = document.createElement('div');
  card.className = 'otp-card' + (isNew ? ' new' : '');
  card.innerHTML = `
    <div class="row">
      <span class="label">🕐 Time</span>
      <span class="time-val">${otp.date || '--'}</span>
    </div>
    <div class="row">
      <span class="label">${otp.flag || '🌍'} Country</span>
      <span class="value">${otp.country || '--'} <span class="badge">${otp.service || '--'}</span></span>
    </div>
    <div class="row">
      <span class="label">📞 Number</span>
      <span class="number">${otp.number_masked || '--'}</span>
    </div>
    <div class="row">
      <span class="label">🔑 OTP</span>
      <span class="otp-val">${otp.otp || '--'}</span>
    </div>
    <div class="msg">📧 ${otp.full_message || '--'}</div>
  `;
  feed.insertBefore(card, feed.firstChild);
}

const es = new EventSource('/stream');

es.onopen = () => {
  dot.style.background = '#00ff88';
  document.getElementById('status').textContent = 'Live Connected ✅';
};

es.onmessage = (e) => {
  try {
    const otp = JSON.parse(e.data);
    addCard(otp, true);
  } catch(err) {}
};

es.onerror = () => {
  dot.style.background = '#ff4444';
  document.getElementById('status').textContent = 'Reconnecting...';
  setTimeout(() => location.reload(), 3000);
};
</script>
</body>
</html>"""
    return Response(html, mimetype="text/html")

@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "success"      : True,
        "logged_in"    : is_logged_in,
        "last_refresh" : last_refresh,
        "total_otps"   : len(all_otps),
        "new_otps"     : len(new_otps),
    })

@app.route("/otps", methods=["GET"])
def get_all_otps():
    if not check_api_key():
        return jsonify({"success": False, "error": "Invalid API Key"}), 401

    # Optional filters
    service_filter = request.args.get("service","").lower()
    country_filter = request.args.get("country","").lower()
    limit          = request.args.get("limit","")  # Empty = no limit

    with lock:
        result = list(all_otps)

    if service_filter:
        result = [o for o in result if service_filter in o["service"].lower()]
    if country_filter:
        result = [o for o in result if country_filter in o["country"].lower()]

    if limit:
        result = result[:int(limit)]
    # No limit by default — saare OTPs

    return jsonify({
        "success"      : True,
        "count"        : len(result),
        "last_refresh" : last_refresh,
        "otps"         : result,
    })

@app.route("/otps/new", methods=["GET"])
def get_new_otps():
    """
    Naye OTPs fetch karo.
    ?since=2026-03-03 02:00:00  — us time ke baad ke OTPs
    Bina since param ke — last API refresh ke baad ke OTPs
    """
    if not check_api_key():
        return jsonify({"success": False, "error": "Invalid API Key"}), 401

    since_str = request.args.get("since", "")

    with lock:
        if since_str:
            try:
                since_dt = datetime.strptime(since_str, "%Y-%m-%d %H:%M:%S")
                result = [
                    o for o in all_otps
                    if datetime.strptime(o["date"], "%Y-%m-%d %H:%M:%S") > since_dt
                ]
            except Exception:
                result = list(new_otps)
        else:
            result = list(new_otps)

    return jsonify({
        "success"      : True,
        "count"        : len(result),
        "last_refresh" : last_refresh,
        "next_refresh" : (datetime.now() + timedelta(seconds=REFRESH_INTERVAL)).strftime("%Y-%m-%d %H:%M:%S"),
        "otps"         : result,
    })

@app.route("/otps/latest", methods=["GET"])
def get_latest_otp():
    if not check_api_key():
        return jsonify({"success": False, "error": "Invalid API Key"}), 401

    with lock:
        otp = all_otps[0] if all_otps else None

    if not otp:
        return jsonify({"success": False, "error": "Koi OTP nahi mila"}), 404

    return jsonify({"success": True, "otp": otp})

@app.route("/refresh", methods=["POST"])
def force_refresh():
    if not check_api_key():
        return jsonify({"success": False, "error": "Invalid API Key"}), 401

    fetch_otps()
    return jsonify({
        "success"      : True,
        "message"      : "Refresh complete",
        "total_otps"   : len(all_otps),
        "new_otps"     : len(new_otps),
        "last_refresh" : last_refresh,
    })

# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────

if __name__ == "__main__":
    print("╔══════════════════════════════════╗")
    print("║  ⚡ POWER MODZ OTP API STARTING  ║")
    print("╚══════════════════════════════════╝")

    # Login
    for i in range(3):
        if do_login():
            break
        time.sleep(3)

    if not is_logged_in:
        print("❌ Login fail!")
        exit(1)

    # First fetch
    fetch_otps()

    # Background thread — har 30s refresh
    t = threading.Thread(target=background_refresh, daemon=True)
    t.start()

    railway_url = __import__('os').environ.get('RAILWAY_PUBLIC_DOMAIN','')
    base_url = f"https://{railway_url}" if railway_url else f"http://127.0.0.1:{API_PORT}"
    print(f"\n✅ API Ready!")
    print(f"   URL: {base_url}")
    print(f"\nEndpoints:")
    print(f"   {base_url}/otps")
    print(f"   {base_url}/otps/new")
    print(f"   {base_url}/otps/latest")
    print(f"   {base_url}/status")
    print(f"   {base_url}/live")

    app.run(host="0.0.0.0", port=API_PORT, debug=False, threaded=True)
