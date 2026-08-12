"""
StarsHub v4 — Backend (FastAPI)
مُصحَّح بالكامل:
  • DB_PATH مسار مطلق (يحل مشكلة "حساب غير موجود")
  • register يُميّز بين خطأ البريد المكرر وباقي الأخطاء
  • buy_stars_fragment يقرأ من ENV (لا بيانات مُضمَّنة)
  • لا دوال مكررة
  • لوحة تحكم الإحالات /admin/referrals-panel
"""

import os, hmac, json, hashlib, asyncio, logging, secrets, string, base64, random, io, smtplib, ssl
from email.message import EmailMessage
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import httpx, aiosqlite, jwt, requests as req_lib, qrcode
from fastapi import FastAPI, Request, HTTPException, Depends, Body
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
import bcrypt as bcrypt_lib

load_dotenv()

# ─── مسار قاعدة البيانات مطلق دائماً بجانب main.py ────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(_BASE_DIR, "starshub.db")

# ─── متغيرات البيئة ────────────────────────────────────────────────────────
CRYPTOMUS_MERCHANT_ID = os.getenv("CRYPTOMUS_MERCHANT_ID", "")
CRYPTOMUS_API_KEY     = os.getenv("CRYPTOMUS_API_KEY", "")
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID         = os.getenv("OWNER_CHAT_ID", "")
JWT_SECRET            = os.getenv("JWT_SECRET", "change-me-now-please")
ADMIN_SECRET          = os.getenv("ADMIN_SECRET", "change-me")
AUTO_WITHDRAW         = os.getenv("AUTO_WITHDRAW", "false").lower() == "true"
SITE_URL              = os.getenv("SITE_URL", "https://tele-starz.com")
TELEGRAM_CHANNEL_URL  = os.getenv("TELEGRAM_CHANNEL_URL", "https://t.me/YourChannel")
TON_WALLET_ADDRESS    = os.getenv("TON_WALLET_ADDRESS", "")
TON_USD_RATE          = float(os.getenv("TON_USD_RATE", "5.0"))
TON_API_KEY_CENTER    = os.getenv("TON_API_KEY", "")
TON_INVOICE_EXPIRY_MIN = int(os.getenv("TON_INVOICE_EXPIRY_MINUTES", "30"))
TONCENTER_BASE        = "https://toncenter.com/api/v2"

CRYPTOPAY_API_TOKEN   = os.getenv("CRYPTOPAY_API_TOKEN", "")
CRYPTOPAY_WEBHOOK_SECRET = os.getenv("CRYPTOPAY_WEBHOOK_SECRET", "")
if not CRYPTOPAY_WEBHOOK_SECRET and CRYPTOPAY_API_TOKEN:
    CRYPTOPAY_WEBHOOK_SECRET = hashlib.sha256(CRYPTOPAY_API_TOKEN.encode()).hexdigest()[:16]
CRYPTOPAY_SWAP_TO_USDT = os.getenv("CRYPTOPAY_SWAP_TO_USDT", "true").lower() == "true"
CRYPTOPAY_INVOICE_EXPIRY = int(os.getenv("CRYPTOPAY_INVOICE_EXPIRY", "3600"))
CRYPTOPAY_API_BASE    = "https://pay.crypt.bot/api"

# ─── سعر TON التلقائي (كاش يُحدَّث دوريًا من API) ──────────────────────────
TON_PRICE_TTL_SECONDS = int(os.getenv("TON_PRICE_TTL_SECONDS", "300"))  # كل 5 دقائق
TON_PRICE_CACHE = {"usd": TON_USD_RATE, "updated_at": 0.0}  # TON_USD_RATE = قيمة احتياطية fallback

def _telegram_api() -> str:
    return f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN', '')}"

def _get_fragment_cookies() -> dict:
    raw = os.getenv("FRAGMENT_COOKIES", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        cookies: dict = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                cookies[k] = v
        return cookies

# ─── ثوابت ─────────────────────────────────────────────────────────────────
BASE_PRICE_PER_STAR = 0.018
GLOBAL_CONFIG = {"PRICE_PER_STAR": 0.018}
COST_PER_STAR    = 0.015
MIN_STARS        = 50
MIN_WITHDRAWAL   = 100
COMMISSION_RATE  = 0.02
JWT_EXPIRE_DAYS  = 30
TON_NANO         = 1_000_000_000
TON_TOLERANCE    = 0.005
CRYPTOMUS_API    = "https://api.cryptomus.com/v1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("starshub")

security = HTTPBearer(auto_error=False)

# ═══════════════════════════════════════════════════════════════════════════
# قاعدة البيانات
# ═══════════════════════════════════════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                email          TEXT    UNIQUE NOT NULL,
                password_hash  TEXT    NOT NULL,
                referral_code  TEXT    UNIQUE NOT NULL,
                referred_by    TEXT,
                stars_balance  INTEGER NOT NULL DEFAULT 0,
                telegram_id    TEXT,
                created_at     TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id               TEXT    NOT NULL,
                stars_amount          INTEGER NOT NULL,
                price_usd             REAL    NOT NULL,
                status                TEXT    NOT NULL DEFAULT 'pending',
                invoice_id            TEXT    UNIQUE,
                referrer_code         TEXT,
                account_id            INTEGER,
                email                 TEXT,
                payment_method        TEXT    DEFAULT 'cryptomus',
                ton_amount            REAL,
                ton_amount_nano       INTEGER,
                comment               TEXT,
                expires_at            TEXT,
                underpayment_flagged  INTEGER DEFAULT 0,
                underpaid_amount      REAL,
                underpaid_detected_at TEXT,
                testnet_flagged       INTEGER DEFAULT 0,
                created_at            TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referral_earnings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                order_id     INTEGER NOT NULL,
                stars_amount INTEGER NOT NULL,
                created_at   TEXT    NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS withdrawals (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id            INTEGER NOT NULL,
                stars_amount       INTEGER NOT NULL,
                recipient_username TEXT    NOT NULL,
                status             TEXT    NOT NULL DEFAULT 'pending',
                created_at         TEXT    NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS pending_referrals (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_code    TEXT    NOT NULL,
                order_id         INTEGER NOT NULL,
                stars_amount     INTEGER NOT NULL,
                guest_identifier TEXT,
                guest_email      TEXT,
                created_at       TEXT    NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS bot_users (
                tg_id INTEGER PRIMARY KEY,
                language TEXT DEFAULT 'en',
                state TEXT DEFAULT 'idle',
                temp_stars INTEGER,
                temp_username TEXT,
                temp_method TEXT
            );
        """)
        for migration in [
            "ALTER TABLE orders ADD COLUMN email TEXT",
            "ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT 'cryptomus'",
            "ALTER TABLE orders ADD COLUMN ton_amount REAL",
            "ALTER TABLE orders ADD COLUMN ton_amount_nano INTEGER",
            "ALTER TABLE orders ADD COLUMN comment TEXT",
            "ALTER TABLE orders ADD COLUMN expires_at TEXT",
            "ALTER TABLE orders ADD COLUMN underpayment_flagged INTEGER DEFAULT 0",
            "ALTER TABLE orders ADD COLUMN underpaid_amount REAL",
            "ALTER TABLE orders ADD COLUMN underpaid_detected_at TEXT",
            "ALTER TABLE orders ADD COLUMN testnet_flagged INTEGER DEFAULT 0",
            "ALTER TABLE pending_referrals ADD COLUMN guest_email TEXT",
            "ALTER TABLE orders ADD COLUMN error_message TEXT",
            "ALTER TABLE orders ADD COLUMN cryptopay_invoice_id INTEGER",
        ]:
            try:
                await db.execute(migration)
            except Exception:
                pass
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_comment ON orders(comment)",
        ]:
            try:
                await db.execute(idx)
            except Exception:
                pass
        
        await db.commit()
        # Load config
        async with db.execute("SELECT key, value FROM config") as cur:
            rows = await cur.fetchall()
            for k, v in rows:
                if k == "PRICE_PER_STAR":
                    GLOBAL_CONFIG[k] = float(v)
                elif k == "COMMISSION_PERCENT":
                    GLOBAL_CONFIG[k] = float(v)


# ═══════════════════════════════════════════════════════════════════════════
# Cryptomus
# ═══════════════════════════════════════════════════════════════════════════
def _cryptomus_sign(payload_dict: dict) -> str:
    payload_json = json.dumps(payload_dict, separators=(",", ":"), ensure_ascii=False)
    payload_b64  = base64.b64encode(payload_json.encode()).decode()
    return hashlib.md5((payload_b64 + CRYPTOMUS_API_KEY).encode()).hexdigest()

def cryptomus_create_invoice(amount_usd: float, order_id: str) -> dict:
    payload = {
        "amount": f"{amount_usd:.2f}", "currency": "USD",
        "order_id": order_id, "url_return": SITE_URL,
        "url_callback": f"{SITE_URL}/cryptomus-webhook",
        "lifetime": 3600, "subtract": 0, "is_payment_multiple": False,
    }
    resp = req_lib.post(
        f"{CRYPTOMUS_API}/payment",
        json=payload,
        headers={"merchant": CRYPTOMUS_MERCHANT_ID,
                 "sign": _cryptomus_sign(payload),
                 "Content-Type": "application/json"},
        timeout=15,
    )
    data = resp.json()
    if data.get("state") != 0:
        raise HTTPException(502, f"Cryptomus error: {data.get('message', data)}")
    return data["result"]

def verify_cryptomus_webhook(body: dict, received_sign: str) -> bool:
    payload_copy = {k: v for k, v in body.items() if k != "sign"}
    return hmac.compare_digest(_cryptomus_sign(payload_copy), received_sign)

# ═══════════════════════════════════════════════════════════════════════════
# Crypto Pay (@CryptoBot)
# ═══════════════════════════════════════════════════════════════════════════
def cryptopay_api_call(method: str, params: dict = None) -> dict:
    headers = {"Crypto-Pay-API-Token": CRYPTOPAY_API_TOKEN}
    url = f"{CRYPTOPAY_API_BASE}/{method}"
    resp = req_lib.post(url, json=params or {}, headers=headers, timeout=15)
    data = resp.json()
    if not data.get("ok"):
        raise HTTPException(502, f"Crypto Pay error: {data.get('error', data)}")
    return data.get("result")

def cryptopay_create_invoice(amount_usd: float, description: str, payload_str: str, order_id: int = None) -> dict:
    # رابط العودة يتضمّن order_id حتى يستأنف الفرونت اند التحقق الدوري بعد إعادة تحميل الصفحة
    paid_url = f"{SITE_URL}?cryptopay_order={order_id}" if order_id else SITE_URL
    params = {
        "currency_type": "fiat",
        "fiat": "USD",
        "amount": f"{amount_usd:.2f}",
        "description": description[:1024],
        "payload": payload_str[:4096],
        "paid_btn_name": "callback",
        "paid_btn_url": paid_url,
        "expires_in": CRYPTOPAY_INVOICE_EXPIRY
    }
    if CRYPTOPAY_SWAP_TO_USDT:
        params["swap_to"] = "USDT"
    
    return cryptopay_api_call("createInvoice", params)

def cryptopay_get_invoice(invoice_id: int) -> dict:
    data = cryptopay_api_call("getInvoices", {"invoice_ids": str(invoice_id)})
    # result is directly an array of Invoice objects
    if isinstance(data, list):
        return data[0] if data else None
    # fallback: some versions may wrap in items
    items = data.get("items", data) if isinstance(data, dict) else []
    return items[0] if items else None

def cryptopay_verify_webhook(body_bytes: bytes, signature: str) -> bool:
    if not CRYPTOPAY_API_TOKEN or not signature:
        return False
    secret = hashlib.sha256(CRYPTOPAY_API_TOKEN.encode()).digest()
    expected = hmac.HMAC(secret, body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

# ═══════════════════════════════════════════════════════════════════════════
# TON
# ═══════════════════════════════════════════════════════════════════════════
async def fetch_ton_price_usd() -> float:
    """يجلب سعر TON الحالي من CoinGecko، ويعود للقيمة الاحتياطية TON_USD_RATE عند الفشل."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "the-open-network", "vs_currencies": "usd"},
            )
            r.raise_for_status()
            price = float(r.json()["the-open-network"]["usd"])
            if price <= 0:
                raise ValueError("سعر غير صالح من الـ API")
            return price
    except Exception as e:
        log.warning(f"فشل جلب سعر TON من API: {e} — استخدام القيمة الاحتياطية {TON_USD_RATE}")
        return TON_USD_RATE

async def refresh_ton_price():
    price = await fetch_ton_price_usd()
    TON_PRICE_CACHE["usd"] = price
    TON_PRICE_CACHE["updated_at"] = datetime.utcnow().timestamp()
    log.info(f"💎 تم تحديث سعر TON: {price} USD")

async def _ton_price_updater_loop():
    while True:
        await asyncio.sleep(TON_PRICE_TTL_SECONDS)
        try:
            await refresh_ton_price()
        except Exception as e:
            log.error(f"خطأ في تحديث سعر TON: {e}")

def get_cached_ton_price() -> float:
    return TON_PRICE_CACHE["usd"]

def usd_to_ton(usd: float) -> float:
    return round(usd / get_cached_ton_price(), 6)

def ton_to_nano(ton: float) -> int:
    return int(Decimal(str(ton)) * TON_NANO)

def gen_comment() -> str:
    return str(random.randint(100_000_000, 999_999_999))

def is_testnet_address(addr: str) -> bool:
    return addr.startswith("kQ") or addr.startswith("0Q")

async def fetch_ton_transactions(wallet: str, limit: int = 50) -> list:
    url = f"{TONCENTER_BASE}/getTransactions"
    params = {"address": wallet, "limit": limit}
    headers = {}
    if TON_API_KEY_CENTER:
        headers["X-API-Key"] = TON_API_KEY_CENTER
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(url, params=params, headers=headers, timeout=15)
        data = r.json()
        return data.get("result", []) if data.get("ok") else []
    except Exception as e:
        log.warning(f"TON Center error: {e}")
        return []

async def find_ton_payment(comment: str, expected_nano: int) -> dict:
    txs = await fetch_ton_transactions(TON_WALLET_ADDRESS)
    for tx in txs:
        try:
            msg = tx.get("in_msg", {})
            if msg.get("message", "").strip() != comment:
                continue
            sender = msg.get("source", "")
            value  = int(msg.get("value", 0))
            min_ok = int(expected_nano * (1 - TON_TOLERANCE))
            return {"found": True, "amount_nano": value, "sender": sender,
                    "is_testnet": is_testnet_address(sender), "sufficient": value >= min_ok}
        except Exception:
            continue
    return {"found": False}

# ═══════════════════════════════════════════════════════════════════════════
# Auth helpers
# ═══════════════════════════════════════════════════════════════════════════
def gen_referral_code() -> str:
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

def hash_password(pw: str) -> str:
    # bcrypt يقبل حتى 72 بايت فقط لكلمة المرور
    pw_bytes = pw.encode("utf-8")[:72]
    return bcrypt_lib.hashpw(pw_bytes, bcrypt_lib.gensalt()).decode("utf-8")

def verify_password(pw: str, hashed: str) -> bool:
    try:
        pw_bytes = pw.encode("utf-8")[:72]
        return bcrypt_lib.checkpw(pw_bytes, hashed.encode("utf-8"))
    except Exception as e:
        log.error(f"Password verify error: {e}")
        return False

def create_token(uid: int) -> str:
    exp = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": str(uid), "exp": exp}, JWT_SECRET, algorithm="HS256")

def decode_token(token: str) -> Optional[int]:
    try:
        return int(jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["sub"])
    except Exception:
        return None

async def _fetch_user(uid: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id=?", (uid,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    if not creds:
        raise HTTPException(401, "يجب تسجيل الدخول")
    uid = decode_token(creds.credentials)
    if not uid:
        raise HTTPException(401, "رمز غير صالح")
    user = await _fetch_user(uid)
    if not user:
        raise HTTPException(401, "المستخدم غير موجود")
    return user

async def get_optional_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> Optional[dict]:
    if not creds:
        return None
    uid = decode_token(creds.credentials)
    return await _fetch_user(uid) if uid else None

def calc_price(stars: int) -> float:
    return round(stars * GLOBAL_CONFIG["PRICE_PER_STAR"], 2)

def calc_commission(stars: int) -> int:
    return max(1, int(stars * COMMISSION_RATE))

def fmt_email(email) -> str:
    return email if email else "(لم يُدخل بريداً)"

# ═══════════════════════════════════════════════════════════════════════════
# Telegram notify
# ═══════════════════════════════════════════════════════════════════════════
async def notify_owner(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat  = os.getenv("OWNER_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        async with httpx.AsyncClient() as c:
            await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception:
        pass

async def send_new_order_alert(order: dict):
    method = "TON Transfer" if order.get("payment_method") == "ton_transfer" else "Cryptomus"
    ref    = f"🎟 إحالة: {order['referrer_code']}\n" if order.get("referrer_code") else ""
    await notify_owner(
        f"🛒 <b>طلب جديد</b>\n👤 {order['user_id']}\n"
        f"📧 {fmt_email(order.get('email'))}\n"
        f"⭐ {order['stars_amount']:,} نجمة | 💵 ${order['price_usd']:.2f}\n"
        f"💳 {method}\n{ref}📌 انتظار الدفع\n"
        f"🕐 {order['created_at'][:16].replace('T',' ')}"
    )

async def send_success_alert(order: dict):
    profit = round((order["stars_amount"] / 1000) * 3.0, 2)
    await notify_owner(
        f"✅ <b>دفع ناجح!</b>\n👤 {order['user_id']}\n"
        f"📧 {fmt_email(order.get('email'))}\n"
        f"⭐ {order['stars_amount']:,} نجمة | 💰 ربح: ${profit}"
    )

async def send_failure_alert(order: dict):
    await notify_owner(
        f"❌ <b>فشل الإرسال!</b>\n👤 {order['user_id']}\n"
        f"📧 {fmt_email(order.get('email'))}\n"
        f"⭐ {order['stars_amount']:,} نجمة\n⚠️ تدخل يدوي مطلوب!"
    )

async def send_ton_success_alert(order: dict, ton_amount: float):
    profit = round((order["stars_amount"] / 1000) * 3.0, 2)
    await notify_owner(
        f"✅ <b>دفع TON ناجح</b>\n👤 {order['user_id']}\n"
        f"📧 {fmt_email(order.get('email'))}\n"
        f"⭐ {order['stars_amount']:,} | 💵 ${order['price_usd']:.2f}\n"
        f"🪙 {ton_amount:.4f} TON | 🔢 {order['comment']}\n💰 ربح: ${profit}"
    )

async def send_underpaid_alert(order: dict, received_nano: int):
    exp = order.get("ton_amount", 0)
    rcv = received_nano / TON_NANO
    await notify_owner(
        f"⚠️ <b>دفع ناقص</b>\n👤 {order['user_id']}\n"
        f"🪙 مطلوب: {exp:.4f} | مستلم: {rcv:.4f} | ناقص: {round(exp-rcv,4):.4f} TON\n"
        f"🔢 {order['comment']}"
    )

async def send_testnet_fraud_alert(order: dict, ton_sent: float, sender: str):
    await notify_owner(
        f"🚨 <b>احتيال Testnet!</b>\n👤 {order['user_id']}\n"
        f"📧 {fmt_email(order.get('email'))}\n"
        f"🪙 {ton_sent:.4f} TON (Testnet)\n🔢 {order['comment']}\n"
        f"📍 <code>{sender}</code>"
    )

async def send_commission_alert(referrer_email: str, commission: int, order: dict, net: float):
    await notify_owner(
        f"💰 <b>عمولة إحالة</b>\nمُحيل: {referrer_email}\n"
        f"عمولة: {commission}⭐\nمن شراء: {order['user_id']} | {order['stars_amount']:,}⭐\n"
        f"ربح صافٍ: ${net}"
    )

async def send_withdrawal_alert(user: dict, stars: int, recipient: str):
    await notify_owner(
        f"💸 <b>طلب سحب</b>\n👤 {user['email']}\n"
        f"⭐ {stars:,} → {recipient}\n🕐 {datetime.utcnow().isoformat()[:16].replace('T',' ')}"
    )

# ═══════════════════════════════════════════════════════════════════════════
# Fragment — شراء النجوم
# ═══════════════════════════════════════════════════════════════════════════
async def buy_stars_fragment(user_id: str, stars: int, order_id: int = None) -> bool:
    """يشتري النجوم عبر pyfragment — يقرأ كل البيانات من ENV."""
    from pyfragment import FragmentClient as OriginalFragmentClient
    from pyfragment.enums import PaymentMethod

    class FragmentClient(OriginalFragmentClient):
        @property
        def seed(self):
            return self._seed_str.split(" ") if isinstance(self._seed_str, str) else self._seed_str
        @seed.setter
        def seed(self, value):
            self._seed_str = value.strip() if isinstance(value, str) else value

    seed    = os.getenv("WALLET_SEED", "")
    api_key = os.getenv("TON_API_KEY", "")
    cookies = _get_fragment_cookies()

    if not seed:
        log.error("WALLET_SEED غير موجود في .env")
        await _save_order_error(order_id, "WALLET_SEED غير موجود في .env")
        return False
    if not cookies:
        log.error("FRAGMENT_COOKIES غير موجودة في .env")
        await _save_order_error(order_id, "FRAGMENT_COOKIES غير موجودة في .env")
        return False

    recipient = user_id.strip()
    last_error = ""
    for attempt in range(3):
        try:
            log.info(f"محاولة {attempt+1}/3: إرسال {stars}⭐ إلى {recipient}")
            async with FragmentClient(
                seed=seed, 
                api_key=api_key, 
                cookies=cookies, 
                api_provider="toncenter", 
                wallet_version="V5R1"
            ) as client:
                result = await client.purchase_stars(recipient, amount=stars, payment_method=PaymentMethod.GRAM)
            log.info(f"✅ تم: {stars}⭐ → {recipient} | tx: {getattr(result, 'transaction_id', 'N/A')}")
            return True
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            log.error(f"Fragment خطأ محاولة {attempt+1}: {last_error}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    await _save_order_error(order_id, last_error)
    return False

async def _save_order_error(order_id: int, error_msg: str):
    """يحفظ رسالة الخطأ في سجل الطلب."""
    if not order_id:
        return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE orders SET error_message=? WHERE id=?", (error_msg[:500], order_id))
            await db.commit()
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════
# Referral logic
# ═══════════════════════════════════════════════════════════════════════════
async def process_referral_commission(order: dict):
    ref_code = order.get("referrer_code")
    if not ref_code:
        return
    
    # NEW PROFIT CALCULATION
    price_usd = order.get("price_usd", 0.0)
    stars_amount = order["stars_amount"]
    cost_price = 0.015 * stars_amount
    profit_usd = max(0.0, price_usd - cost_price)
    comm_percent = GLOBAL_CONFIG.get("COMMISSION_PERCENT", 10.0)
    commission_usd = round(profit_usd * (comm_percent / 100.0), 4)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, email FROM users WHERE referral_code=?", (ref_code,)) as cur:
            referrer = await cur.fetchone()
        if not referrer:
            return
        async with db.execute(
            "SELECT id FROM referral_earnings WHERE order_id=? AND user_id=?",
            (order["id"], referrer["id"])
        ) as cur:
            if await cur.fetchone():
                return
        now = datetime.utcnow().isoformat()
        await db.execute(
            "INSERT INTO referral_earnings (user_id, order_id, stars_amount, usd_amount, price_at_time, created_at) VALUES (?,?,?,?,?,?)",
            (referrer["id"], order["id"], stars_amount, commission_usd, GLOBAL_CONFIG.get("PRICE_PER_STAR", 0.018), now),
        )
        await db.execute(
            "UPDATE users SET usd_balance = usd_balance + ? WHERE id=?",
            (commission_usd, referrer["id"]),
        )
        await db.commit()
    
    # We still alert via bot
    net = round(profit_usd - commission_usd, 2)
    await send_commission_alert(referrer["email"], commission_usd, order, net)

async def process_pending_referrals(telegram_id: str, new_user_id: int, new_email: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT o.id, o.stars_amount, o.price_usd, pr.id AS pr_id, pr.referrer_code
            FROM orders o
            JOIN pending_referrals pr ON pr.order_id = o.id
            WHERE (o.user_id = ? OR pr.guest_email = ?)
              AND o.status IN ('completed', 'paid')
        """, (telegram_id, new_email)) as cur:
            rows = await cur.fetchall()
        first_code = None
        for row in rows:
            ref_code = row["referrer_code"]
            if not first_code:
                first_code = ref_code
            async with db.execute("SELECT id FROM users WHERE referral_code=?", (ref_code,)) as cur2:
                referrer = await cur2.fetchone()
            if not referrer:
                continue
            
            # CALCULATION
            cost_price = 0.015 * row["stars_amount"]
            profit_usd = max(0.0, row["price_usd"] - cost_price)
            comm_percent = GLOBAL_CONFIG.get("COMMISSION_PERCENT", 10.0)
            commission_usd = round(profit_usd * (comm_percent / 100.0), 4)

            async with db.execute(
                "SELECT id FROM referral_earnings WHERE order_id=? AND user_id=?",
                (row["id"], referrer["id"])
            ) as cur3:
                if await cur3.fetchone():
                    continue
            now = datetime.utcnow().isoformat()
            await db.execute(
                "INSERT INTO referral_earnings (user_id, order_id, stars_amount, usd_amount, price_at_time, created_at) VALUES (?,?,?,?,?,?)",
                (referrer["id"], row["id"], row["stars_amount"], commission_usd, GLOBAL_CONFIG.get("PRICE_PER_STAR", 0.018), now),
            )
            await db.execute(
                "UPDATE users SET usd_balance = usd_balance + ? WHERE id=?",
                (commission_usd, referrer["id"]),
            )
            await db.execute("DELETE FROM pending_referrals WHERE id=?", (row["pr_id"],))
        if first_code:
            await db.execute(
                "UPDATE users SET referred_by=? WHERE id=? AND referred_by IS NULL",
                (first_code, new_user_id),
            )
        await db.commit()

# ═══════════════════════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════════════════════
async def get_order_by_id(order_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def get_order_by_invoice(invoice_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE invoice_id=?", (invoice_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None

async def set_order_status(order_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
        await db.commit()

# ═══════════════════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════════════════
app = FastAPI(title="StarsHub v4")

@app.on_event("startup")
async def startup():
    await init_db()
    await refresh_ton_price()                       # جلب فوري عند الإقلاع
    asyncio.create_task(_ton_price_updater_loop())   # ثم تحديث دوري في الخلفية
    log.info(f"✅ StarsHub v4 جاهز | DB: {DB_PATH}")
    # ── تسجيل Webhook URL لـ CryptoPay تلقائياً ──
    if CRYPTOPAY_API_TOKEN and CRYPTOPAY_WEBHOOK_SECRET:
        webhook_url = f"{SITE_URL}/cryptopay-webhook/{CRYPTOPAY_WEBHOOK_SECRET}"
        log.info(f"🤖 Crypto Pay webhook URL: {webhook_url}")
        try:
            result = cryptopay_api_call("setWebhookUrl", {"url": webhook_url})
            log.info(f"✅ Crypto Pay webhook تم التسجيل بنجاح: {result}")
        except Exception as e:
            log.warning(f"⚠️ فشل تسجيل Crypto Pay webhook: {e} — تأكد من تسجيله يدوياً")
            
    # ── تسجيل Webhook URL لـ Telegram Bot تلقائياً ──
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if tg_token:
        tg_webhook_url = f"{SITE_URL}/tg-webhook"
        try:
            req_lib.post(
                f"https://api.telegram.org/bot{tg_token}/setWebhook",
                json={"url": tg_webhook_url},
                timeout=10
            )
            log.info(f"🤖 Telegram Bot webhook تم التسجيل بنجاح: {tg_webhook_url}")
        except Exception as e:
            log.warning(f"⚠️ فشل تسجيل Telegram Bot webhook: {e}")

app.mount("/static", StaticFiles(directory=os.path.join(_BASE_DIR, "static")), name="static")


@app.get("/robots.txt", response_class=StreamingResponse)
async def robots_txt():
    content = "User-agent: *\nDisallow: /admin\nAllow: /\nSitemap: " + os.getenv("DOMAIN", SITE_URL) + "/sitemap.xml\n"
    return StreamingResponse(io.StringIO(content), media_type="text/plain")

BOT_LANGUAGES = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "ar": "🇸🇦 العربية",
    "fa": "🇮🇷 فارسی",
    "fr": "🇫🇷 Français",
    "de": "🇩🇪 Deutsch",
    "es": "🇪🇸 Español",
    "tr": "🇹🇷 Türkçe"
}

BOT_STRINGS = {
    "en": {
        "welcome": "Welcome to Tele-Starz Store! ⭐\nBuy Telegram Stars instantly at the best prices.",
        "btn_change_lang": "🌐 Language",
        "btn_open_app": "🛒 Open Mini App",
        "btn_buy_bot": "⭐ Buy via Bot",
        "choose_lang": "Please select your language:",
        "select_package": "⭐ Please choose a Stars package:",
        "custom_amount": "✏️ Custom Amount",
        "cancel": "❌ Cancel",
        "enter_custom": "Please enter the number of Stars you want to buy (Minimum 50):",
        "invalid_custom": "⚠️ Invalid amount. Minimum is 50. Please enter a valid number:",
        "enter_username": "Please enter the Telegram ID (@username) to receive the {stars} stars:",
        "invalid_username": "⚠️ Username must start with @. Please try again:",
        "select_payment": "💳 You are buying {stars} stars for {price}$.\nSelect a payment method:",
        "btn_cryptopay": "🤖 CryptoBot Pay",
        "btn_cryptomus": "💳 Cryptomus",
        "btn_ton": "💎 TON Transfer",
        "pay_now": "⭐ Pay Now",
        "ton_instructions": "💎 **TON Payment**\n\nTransfer exactly:\n`{price}` TON\n\nTo Wallet Address:\n`{wallet}`\n\n⚠️ **IMPORTANT**: You MUST include the following comment code exactly:\n`{comment}`\n\nSession expires in 30 minutes.",
        "cancelled": "Operation cancelled. Type /start to return to the main menu.",
        "error_invoice": "❌ Failed to generate invoice. Please try again."
    },
    "ar": {
        "welcome": "مرحباً بك في متجر نجوم تيليجرام (Tele-Starz)! ⭐\nاشترِ النجوم فوراً بأفضل الأسعار.",
        "btn_change_lang": "🌐 اللغة",
        "btn_open_app": "🛒 فتح المتجر السريع",
        "btn_buy_bot": "⭐ شراء عبر المحادثة",
        "choose_lang": "الرجاء اختيار لغتك:",
        "select_package": "⭐ الرجاء اختيار باقة النجوم:",
        "custom_amount": "✏️ كمية مخصصة",
        "cancel": "❌ إلغاء",
        "enter_custom": "الرجاء إدخال عدد النجوم التي ترغب بشرائها (الحد الأدنى 50):",
        "invalid_custom": "⚠️ الكمية غير صالحة. الحد الأدنى هو 50. يرجى إدخال رقم صحيح:",
        "enter_username": "الرجاء إدخال معرّف تيليجرام (@username) الذي سيستقبل {stars} نجمة:",
        "invalid_username": "⚠️ يجب أن يبدأ المعرّف بـ @. يرجى المحاولة مرة أخرى:",
        "select_payment": "💳 أنت تشتري {stars} نجمة بسعر {price}$.\nاختر طريقة الدفع:",
        "btn_cryptopay": "🤖 الدفع عبر CryptoBot",
        "btn_cryptomus": "💳 الدفع ببطاقة (Cryptomus)",
        "btn_ton": "💎 تحويل TON",
        "pay_now": "⭐ ادفع الآن",
        "ton_instructions": "💎 **دفع TON**\n\nقم بتحويل:\n`{price}` TON\n\nإلى عنوان المحفظة:\n`{wallet}`\n\n⚠️ **هام جداً**: يجب أن يتضمن التحويل رمز التعليق (Comment) التالي كما هو:\n`{comment}`\n\nستنتهي صلاحية الجلسة خلال 30 دقيقة.",
        "cancelled": "تم الإلغاء. أرسل /start للعودة للقائمة الرئيسية.",
        "error_invoice": "❌ حدث خطأ أثناء إصدار الفاتورة. يرجى المحاولة مرة أخرى."
    },
    "ru": {
        "welcome": "Добро пожаловать в магазин Tele-Starz! ⭐\nПокупайте звезды Telegram мгновенно по лучшим ценам.",
        "btn_change_lang": "🌐 Язык",
        "btn_open_app": "🛒 Открыть Mini App",
        "btn_buy_bot": "⭐ Купить в боте",
        "choose_lang": "Пожалуйста, выберите ваш язык:",
        "select_package": "⭐ Выберите пакет звезд:",
        "custom_amount": "✏️ Другое количество",
        "cancel": "❌ Отмена",
        "enter_custom": "Введите количество звезд (Минимум 50):",
        "invalid_custom": "⚠️ Неверно. Минимум 50. Введите число:",
        "enter_username": "Введите Telegram ID (@username) для получения {stars} звезд:",
        "invalid_username": "⚠️ ID должен начинаться с @. Попробуйте еще раз:",
        "select_payment": "💳 Вы покупаете {stars} звезд за {price}$.\nВыберите способ оплаты:",
        "btn_cryptopay": "🤖 Оплата CryptoBot",
        "btn_cryptomus": "💳 Cryptomus (Карты)",
        "btn_ton": "💎 Перевод TON",
        "pay_now": "⭐ Оплатить сейчас",
        "ton_instructions": "💎 **Оплата TON**\n\nПереведите ровно:\n`{price}` TON\n\nНа кошелек:\n`{wallet}`\n\n⚠️ **ВАЖНО**: Обязательно укажите этот комментарий (Comment):\n`{comment}`\n\nСессия истекает через 30 минут.",
        "cancelled": "Отменено. Введите /start для главного меню.",
        "error_invoice": "❌ Ошибка при создании счета."
    },
    "fa": {
        "welcome": "به فروشگاه Tele-Starz خوش آمدید! ⭐\nستاره های تلگرام را فورا با بهترین قیمت بخرید.",
        "btn_change_lang": "🌐 زبان",
        "btn_open_app": "🛒 باز کردن مینی اپ",
        "btn_buy_bot": "⭐ خرید از ربات",
        "choose_lang": "لطفا زبان خود را انتخاب کنید:",
        "select_package": "⭐ لطفا بسته ستاره را انتخاب کنید:",
        "custom_amount": "✏️ مقدار دلخواه",
        "cancel": "❌ لغو",
        "enter_custom": "تعداد ستاره های مورد نیاز را وارد کنید (حداقل 50):",
        "invalid_custom": "⚠️ نامعتبر. حداقل 50 است. عدد وارد کنید:",
        "enter_username": "آیدی تلگرام (@username) را برای دریافت {stars} ستاره وارد کنید:",
        "invalid_username": "⚠️ آیدی باید با @ شروع شود:",
        "select_payment": "💳 شما در حال خرید {stars} ستاره به قیمت {price}$ هستید.\nروش پرداخت را انتخاب کنید:",
        "btn_cryptopay": "🤖 پرداخت با CryptoBot",
        "btn_cryptomus": "💳 پرداخت با Cryptomus",
        "btn_ton": "💎 انتقال TON",
        "pay_now": "⭐ پرداخت",
        "ton_instructions": "💎 **پرداخت TON**\n\nدقیقا این مقدار را انتقال دهید:\n`{price}` TON\n\nبه آدرس کیف پول:\n`{wallet}`\n\n⚠️ **مهم**: شما باید کد نظر (Comment) زیر را دقیقا وارد کنید:\n`{comment}`\n\nاین جلسه پس از 30 دقیقه منقضی می شود.",
        "cancelled": "لغو شد. برای بازگشت به منوی اصلی /start را ارسال کنید.",
        "error_invoice": "❌ خطا در ساخت فاکتور."
    },
    "fr": {
        "welcome": "Bienvenue sur Tele-Starz ! ⭐\nAchetez des étoiles Telegram instantanément au meilleur prix.",
        "btn_change_lang": "🌐 Langue",
        "btn_open_app": "🛒 Ouvrir l'App",
        "btn_buy_bot": "⭐ Acheter via Bot",
        "choose_lang": "Veuillez choisir votre langue :",
        "select_package": "⭐ Choisissez un forfait d'étoiles :",
        "custom_amount": "✏️ Montant personnalisé",
        "cancel": "❌ Annuler",
        "enter_custom": "Entrez le nombre d'étoiles (Minimum 50) :",
        "invalid_custom": "⚠️ Invalide. Le minimum est de 50. Entrez un nombre :",
        "enter_username": "Entrez l'ID Telegram (@username) pour recevoir {stars} étoiles :",
        "invalid_username": "⚠️ L'ID doit commencer par @. Réessayez :",
        "select_payment": "💳 Vous achetez {stars} étoiles pour {price}$.\nChoisissez le paiement :",
        "btn_cryptopay": "🤖 CryptoBot Pay",
        "btn_cryptomus": "💳 Cryptomus",
        "btn_ton": "💎 Transfert TON",
        "pay_now": "⭐ Payer maintenant",
        "ton_instructions": "💎 **Paiement TON**\n\nTransférez exactement :\n`{price}` TON\n\nÀ l'adresse :\n`{wallet}`\n\n⚠️ **IMPORTANT** : Vous DEVEZ inclure ce code en commentaire :\n`{comment}`\n\nExpiration dans 30 min.",
        "cancelled": "Annulé. Tapez /start pour le menu principal.",
        "error_invoice": "❌ Échec de la facture."
    },
    "de": {
        "welcome": "Willkommen im Tele-Starz Store! ⭐\nKaufen Sie Telegram-Sterne sofort zu den besten Preisen.",
        "btn_change_lang": "🌐 Sprache",
        "btn_open_app": "🛒 Mini App öffnen",
        "btn_buy_bot": "⭐ Über Bot kaufen",
        "choose_lang": "Bitte wählen Sie Ihre Sprache:",
        "select_package": "⭐ Wählen Sie ein Sternepaket:",
        "custom_amount": "✏️ Benutzerdefinierter Betrag",
        "cancel": "❌ Abbrechen",
        "enter_custom": "Bitte geben Sie die Anzahl der Sterne ein (Minimum 50):",
        "invalid_custom": "⚠️ Ungültig. Minimum ist 50. Bitte geben Sie eine Zahl ein:",
        "enter_username": "Geben Sie die Telegram ID (@username) ein, um {stars} Sterne zu erhalten:",
        "invalid_username": "⚠️ Benutzername muss mit @ beginnen. Versuchen Sie es erneut:",
        "select_payment": "💳 Sie kaufen {stars} Sterne für {price}$.\nWählen Sie eine Zahlungsmethode:",
        "btn_cryptopay": "🤖 CryptoBot Pay",
        "btn_cryptomus": "💳 Cryptomus",
        "btn_ton": "💎 TON Überweisung",
        "pay_now": "⭐ Jetzt bezahlen",
        "ton_instructions": "💎 **TON Zahlung**\n\nÜberweisen Sie genau:\n`{price}` TON\n\nAn Wallet-Adresse:\n`{wallet}`\n\n⚠️ **WICHTIG**: Sie MÜSSEN folgenden Kommentar (Comment) genau angeben:\n`{comment}`\n\nSitzung läuft in 30 Minuten ab.",
        "cancelled": "Abgebrochen. Tippen Sie /start für das Hauptmenü.",
        "error_invoice": "❌ Fehler bei der Rechnungserstellung."
    },
    "es": {
        "welcome": "¡Bienvenido a Tele-Starz! ⭐\nCompra estrellas de Telegram al instante al mejor precio.",
        "btn_change_lang": "🌐 Idioma",
        "btn_open_app": "🛒 Abrir Mini App",
        "btn_buy_bot": "⭐ Comprar en Bot",
        "choose_lang": "Por favor, seleccione su idioma:",
        "select_package": "⭐ Elija un paquete de estrellas:",
        "custom_amount": "✏️ Cantidad personalizada",
        "cancel": "❌ Cancelar",
        "enter_custom": "Ingrese la cantidad de estrellas (Mínimo 50):",
        "invalid_custom": "⚠️ Inválido. El mínimo es 50. Ingrese un número:",
        "enter_username": "Ingrese el Telegram ID (@username) para recibir {stars} estrellas:",
        "invalid_username": "⚠️ El ID debe comenzar con @. Inténtelo de nuevo:",
        "select_payment": "💳 Estás comprando {stars} estrellas por {price}$.\nSeleccione pago:",
        "btn_cryptopay": "🤖 CryptoBot Pay",
        "btn_cryptomus": "💳 Cryptomus",
        "btn_ton": "💎 Transferencia TON",
        "pay_now": "⭐ Pagar ahora",
        "ton_instructions": "💎 **Pago TON**\n\nTransfiere exactamente:\n`{price}` TON\n\nA la dirección:\n`{wallet}`\n\n⚠️ **IMPORTANTE**: DEBE incluir este código en el comentario:\n`{comment}`\n\nLa sesión expira en 30 minutos.",
        "cancelled": "Cancelado. Escriba /start para ir al menú principal.",
        "error_invoice": "❌ Error al generar la factura."
    },
    "tr": {
        "welcome": "Tele-Starz Mağazasına Hoş Geldiniz! ⭐\nTelegram Yıldızlarını anında en iyi fiyatlarla satın alın.",
        "btn_change_lang": "🌐 Dil",
        "btn_open_app": "🛒 Mini Uygulamayı Aç",
        "btn_buy_bot": "⭐ Bot ile Satın Al",
        "choose_lang": "Lütfen dilinizi seçin:",
        "select_package": "⭐ Bir Yıldız paketi seçin:",
        "custom_amount": "✏️ Özel Miktar",
        "cancel": "❌ İptal",
        "enter_custom": "Lütfen yıldız sayısını girin (Minimum 50):",
        "invalid_custom": "⚠️ Geçersiz. Minimum 50'dir. Bir sayı girin:",
        "enter_username": "{stars} yıldız almak için Telegram ID'nizi (@kullaniciadi) girin:",
        "invalid_username": "⚠️ ID @ ile başlamalıdır. Tekrar deneyin:",
        "select_payment": "💳 {price}$ karşılığında {stars} yıldız alıyorsunuz.\nÖdeme yöntemini seçin:",
        "btn_cryptopay": "🤖 CryptoBot Pay",
        "btn_cryptomus": "💳 Cryptomus",
        "btn_ton": "💎 TON Transferi",
        "pay_now": "⭐ Şimdi Öde",
        "ton_instructions": "💎 **TON Ödemesi**\n\nTam olarak şu kadar gönderin:\n`{price}` TON\n\nŞu cüzdana:\n`{wallet}`\n\n⚠️ **ÖNEMLİ**: Yorum (Comment) kısmına bu kodu mutlaka eklemelisiniz:\n`{comment}`\n\nOturum 30 dakika içinde sona erer.",
        "cancelled": "İptal edildi. Ana menü için /start yazın.",
        "error_invoice": "❌ Fatura oluşturulamadı. Lütfen tekrar deneyin."
    }
}

def t_bot(lang: str, key: str) -> str:
    return BOT_STRINGS.get(lang, BOT_STRINGS["en"]).get(key, BOT_STRINGS["en"].get(key, ""))

async def get_bot_user(tg_id: int, tg_lang: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_users WHERE tg_id=?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                lang = tg_lang if tg_lang in BOT_LANGUAGES else "en"
                await db.execute("INSERT INTO bot_users (tg_id, language, state) VALUES (?, ?, 'idle')", (tg_id, lang))
                await db.commit()
                return {"tg_id": tg_id, "language": lang, "state": "idle"}
            return dict(row)

async def set_bot_user_state(tg_id: int, state: str, **kwargs):
    async with aiosqlite.connect(DB_PATH) as db:
        cols = ["state=?"]
        vals = [state]
        for k, v in kwargs.items():
            cols.append(f"{k}=?")
            vals.append(v)
        vals.append(tg_id)
        await db.execute(f"UPDATE bot_users SET {', '.join(cols)} WHERE tg_id=?", tuple(vals))
        await db.commit()

async def send_tg_message(chat_id: int, text: str, reply_markup: dict = None):
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        await asyncio.to_thread(req_lib.post, f"https://api.telegram.org/bot{tg_token}/sendMessage", json=payload, timeout=10)
    except:
        pass

async def edit_tg_message(chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        await asyncio.to_thread(req_lib.post, f"https://api.telegram.org/bot{tg_token}/editMessageText", json=payload, timeout=10)
    except:
        pass

async def answer_callback(callback_id: int):
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    try:
        await asyncio.to_thread(req_lib.post, f"https://api.telegram.org/bot{tg_token}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=5)
    except:
        pass

def bot_menu_keyboard(lang: str):
    return {
        "inline_keyboard": [
            [
                {"text": t_bot(lang, "btn_change_lang"), "callback_data": "change_lang"},
                {"text": t_bot(lang, "btn_open_app"), "web_app": {"url": SITE_URL}}
            ],
            [
                {"text": t_bot(lang, "btn_buy_bot"), "callback_data": "buy_stars"}
            ]
        ]
    }

def bot_lang_keyboard():
    kb = []
    row = []
    for code, name in BOT_LANGUAGES.items():
        row.append({"text": name, "callback_data": f"setlang_{code}"})
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    return {"inline_keyboard": kb}

def bot_packages_keyboard(lang: str):
    return {
        "inline_keyboard": [
            [{"text": "⭐ 50", "callback_data": "pkg_50"}, {"text": "⭐ 100", "callback_data": "pkg_100"}, {"text": "⭐ 200", "callback_data": "pkg_200"}],
            [{"text": "⭐ 300", "callback_data": "pkg_300"}, {"text": "⭐ 500", "callback_data": "pkg_500"}],
            [{"text": t_bot(lang, "custom_amount"), "callback_data": "pkg_custom"}],
            [{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]
        ]
    }

def bot_payment_keyboard(lang: str):
    return {
        "inline_keyboard": [
            [{"text": t_bot(lang, "btn_cryptopay"), "callback_data": "pay_cryptopay"}],
            [{"text": t_bot(lang, "btn_cryptomus"), "callback_data": "pay_cryptomus"}],
            [{"text": t_bot(lang, "btn_ton"), "callback_data": "pay_ton"}],
            [{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]
        ]
    }

@app.post("/tg-webhook")
async def telegram_webhook(update: dict = Body(...)):
    if "callback_query" in update:
        cb = update["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        data = cb["data"]
        tg_id = cb["from"]["id"]
        tg_lang = cb["from"].get("language_code", "en")[:2]
        
        user = await get_bot_user(tg_id, tg_lang)
        lang = user["language"]
        
        await answer_callback(cb["id"])
        
        if data == "change_lang":
            await edit_tg_message(chat_id, msg_id, t_bot(lang, "choose_lang"), bot_lang_keyboard())
            
        elif data.startswith("setlang_"):
            new_lang = data.split("_")[1]
            await set_bot_user_state(tg_id, "idle", language=new_lang)
            await edit_tg_message(chat_id, msg_id, t_bot(new_lang, "welcome"), bot_menu_keyboard(new_lang))
            
        elif data == "buy_stars":
            await set_bot_user_state(tg_id, "idle")
            await edit_tg_message(chat_id, msg_id, t_bot(lang, "select_package"), bot_packages_keyboard(lang))
            
        elif data.startswith("pkg_"):
            if data == "pkg_custom":
                await set_bot_user_state(tg_id, "waiting_custom")
                await edit_tg_message(chat_id, msg_id, t_bot(lang, "enter_custom"), {"inline_keyboard": [[{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]]})
            else:
                stars = int(data.split("_")[1])
                await set_bot_user_state(tg_id, "waiting_username", temp_stars=stars)
                text = t_bot(lang, "enter_username").replace("{stars}", str(stars))
                await edit_tg_message(chat_id, msg_id, text, {"inline_keyboard": [[{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]]})
                
        elif data == "cancel":
            await set_bot_user_state(tg_id, "idle")
            await edit_tg_message(chat_id, msg_id, t_bot(lang, "welcome"), bot_menu_keyboard(lang))
            
        elif data.startswith("pay_"):
            method = data.split("_")[1]
            stars = user.get("temp_stars", 0)
            target = user.get("temp_username", "")
            if not stars or not target:
                return {"ok": True}
                
            price = calc_price(stars)
            await edit_tg_message(chat_id, msg_id, t_bot(lang, "order_created"))
            
            # Create DB Order
            now = datetime.utcnow().isoformat()
            async with aiosqlite.connect(DB_PATH) as db:
                if method == "ton":
                    cur = await db.execute(
                        "INSERT INTO orders (user_id,stars_amount,price_usd,status,payment_method,created_at) "
                        "VALUES (?,?,?,'awaiting_payment','ton_transfer',?)",
                        (target, stars, price, now)
                    )
                else:
                    cur = await db.execute(
                        "INSERT INTO orders (user_id,stars_amount,price_usd,status,payment_method,created_at) "
                        "VALUES (?,?,?,'pending',?,?)",
                        (target, stars, price, method, now)
                    )
                await db.commit()
                order_id = cur.lastrowid
            
            if method == "cryptopay":
                payload_str = json.dumps({"order_id": order_id, "user_id": target})
                try:
                    invoice = await asyncio.to_thread(cryptopay_create_invoice, price, f"⭐ {stars} Stars for {target}", payload_str, order_id)
                    await edit_tg_message(chat_id, msg_id, "✅", {"inline_keyboard": [[{"text": t_bot(lang, "pay_now"), "url": invoice["pay_url"]}]]})
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE orders SET cryptopay_invoice_id=? WHERE id=?", (invoice["invoice_id"], order_id))
                        await db.commit()
                except:
                    await edit_tg_message(chat_id, msg_id, t_bot(lang, "error_invoice"))
                    
            elif method == "cryptomus":
                try:
                    invoice = await asyncio.to_thread(cryptomus_create_invoice, price, "USD", str(order_id))
                    await edit_tg_message(chat_id, msg_id, "✅", {"inline_keyboard": [[{"text": t_bot(lang, "pay_now"), "url": invoice["url"]}]]})
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE orders SET invoice_id=? WHERE id=?", (invoice["uuid"], order_id))
                        await db.commit()
                except:
                    await edit_tg_message(chat_id, msg_id, t_bot(lang, "error_invoice"))
                    
            elif method == "ton":
                ton_usd = get_cached_ton_price()
                ton_amount = round(price / ton_usd, 4) if ton_usd > 0 else 0
                comment_code = f"ORDER-{order_id}"
                
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE orders SET ton_amount=?, ton_comment=? WHERE id=?", (ton_amount, comment_code, order_id))
                    await db.commit()
                    
                msg = t_bot(lang, "ton_instructions").replace("{price}", str(ton_amount)).replace("{wallet}", TON_WALLET_ADDRESS).replace("{comment}", comment_code)
                await edit_tg_message(chat_id, msg_id, msg)
                
            await set_bot_user_state(tg_id, "idle")

    elif "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")
        tg_id = msg["from"]["id"]
        tg_lang = msg["from"].get("language_code", "en")[:2]
        
        user = await get_bot_user(tg_id, tg_lang)
        lang = user["language"]
        state = user["state"]
        
        if text.startswith("/start"):
            await set_bot_user_state(tg_id, "idle")
            await send_tg_message(chat_id, t_bot(lang, "welcome"), bot_menu_keyboard(lang))
            return {"ok": True}
            
        if state == "waiting_custom":
            try:
                stars = int(text)
                if stars < 50:
                    await send_tg_message(chat_id, t_bot(lang, "invalid_custom"), {"inline_keyboard": [[{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]]})
                    return {"ok": True}
            except:
                await send_tg_message(chat_id, t_bot(lang, "invalid_custom"), {"inline_keyboard": [[{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]]})
                return {"ok": True}
                
            await set_bot_user_state(tg_id, "waiting_username", temp_stars=stars)
            txt = t_bot(lang, "enter_username").replace("{stars}", str(stars))
            await send_tg_message(chat_id, txt, {"inline_keyboard": [[{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]]})
            
        elif state == "waiting_username":
            target = text.strip()
            if not target.startswith("@"):
                await send_tg_message(chat_id, t_bot(lang, "invalid_username"), {"inline_keyboard": [[{"text": t_bot(lang, "cancel"), "callback_data": "cancel"}]]})
                return {"ok": True}
                
            stars = user["temp_stars"]
            await set_bot_user_state(tg_id, "idle", temp_username=target)
            
            price = calc_price(stars)
            txt = t_bot(lang, "select_payment").replace("{stars}", str(stars)).replace("{price}", str(price))
            await send_tg_message(chat_id, txt, bot_payment_keyboard(lang))
            
    return {"ok": True}

@app.get("/sitemap.xml", response_class=StreamingResponse)
async def sitemap_xml():
    domain = os.getenv("DOMAIN", SITE_URL).rstrip("/")
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{domain}/</loc>
        <changefreq>daily</changefreq>
        <priority>1.0</priority>
    </url>
</urlset>"""
    return StreamingResponse(io.StringIO(xml_content), media_type="application/xml")

@app.get("/")
async def root():
    return FileResponse(os.path.join(_BASE_DIR, "index.html"))

@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(os.path.join(_BASE_DIR, "dashboard.html"))

@app.get("/admin/referrals-panel")
async def referrals_panel_page():
    return FileResponse(os.path.join(_BASE_DIR, "referrals.html"))

# ─── Models ──────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email: str
    password: str
    telegram_id: Optional[str] = None
    ref_code: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class InvoiceRequest(BaseModel):
    user_id: str
    stars_amount: int
    ref_code: Optional[str] = None
    email: Optional[str] = None

class TonOrderRequest(BaseModel):
    user_id: str
    stars_amount: int
    ref_code: Optional[str] = None
    email: Optional[str] = None

class CheckTonRequest(BaseModel):
    order_id: int

class CryptoPayInvoiceRequest(BaseModel):
    user_id: str
    stars_amount: int
    ref_code: Optional[str] = None
    email: Optional[str] = None
    locale: Optional[str] = "en"

class ResolveUsernameRequest(BaseModel):
    username: str

class WithdrawalRequest(BaseModel):
    recipient_username: str
    stars_amount: int

class ForgotPasswordReq(BaseModel):
    email: str

class ResetPasswordReq(BaseModel):
    token: str
    new_password: str


# ═══════════════════════════════════════════════════════════════════════════
# Auth endpoints
# ═══════════════════════════════════════════════════════════════════════════

async def send_reset_email(email: str, token: str):
    server_addr = os.getenv("SMTP_SERVER", "mail.privateemail.com")
    port = int(os.getenv("SMTP_PORT", 465))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    if not user:
        return
    
    # We build the URL for the frontend.
    site_url = os.getenv("SITE_URL", "https://tele-starz.com").rstrip('/')
    reset_url = f"{site_url}/?reset_token={token}"
    
    msg = EmailMessage()
    msg["Subject"] = "إعادة تعيين كلمة المرور - Tele-Starz"
    msg["From"] = user
    msg["To"] = email
    msg.set_content(f"مرحباً،\n\nلقد طلبت إعادة تعيين كلمة المرور الخاصة بك على Tele-Starz.\nيرجى زيارة الرابط التالي لتعيين كلمة مرور جديدة:\n\n{reset_url}\n\nملاحظة: هذا الرابط صالح لمدة 15 دقيقة فقط.\n\nفريق دعم Tele-Starz")
    
    def _send():
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(server_addr, port, context=context) as server:
            server.login(user, password)
            server.send_message(msg)
            
    try:
        await asyncio.to_thread(_send)
        log.info(f"Reset email sent to {email}")
    except Exception as e:
        log.error(f"Failed to send reset email: {e}")

@app.post("/api/forgot-password")
async def forgot_password(req: ForgotPasswordReq):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM users WHERE email=?", (req.email.lower().strip(),)) as cur:
            user = await cur.fetchone()
    
    if not user:
        return {"ok": True, "message": "إذا كان البريد مسجلاً لدينا، ستصلك رسالة لاستعادة كلمة المرور"}
    
    exp = datetime.utcnow() + timedelta(minutes=15)
    token = jwt.encode({"sub": str(user["id"]), "action": "reset_password", "exp": exp}, JWT_SECRET, algorithm="HS256")
    
    # Send email in background so it doesn't block
    asyncio.create_task(send_reset_email(req.email.lower().strip(), token))
    
    return {"ok": True, "message": "إذا كان البريد مسجلاً لدينا، ستصلك رسالة لاستعادة كلمة المرور"}

@app.post("/api/reset-password")
async def reset_password(req: ResetPasswordReq):
    try:
        payload = jwt.decode(req.token, JWT_SECRET, algorithms=["HS256"])
        if payload.get("action") != "reset_password":
            raise ValueError("Invalid action")
        uid = int(payload["sub"])
    except Exception:
        raise HTTPException(400, "رابط الاستعادة غير صالح أو منتهي الصلاحية")
    
    if len(req.new_password) < 6:
        raise HTTPException(400, "كلمة المرور يجب أن تكون 6 أحرف على الأقل")
        
    hashed = hash_password(req.new_password)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, uid))
        await db.commit()
    return {"ok": True, "message": "تم تغيير كلمة المرور بنجاح"}

@app.post("/api/register")
async def register(req: RegisterRequest):
    if "@" not in req.email or "." not in req.email:
        raise HTTPException(400, "بريد إلكتروني غير صالح")
    if len(req.password) < 6:
        raise HTTPException(400, "كلمة المرور يجب أن تكون 6 أحرف على الأقل")

    email_clean = req.email.lower().strip()
    code        = gen_referral_code()
    referred_by = None

    if req.ref_code:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT id FROM users WHERE referral_code=?", (req.ref_code,)) as cur:
                if await cur.fetchone():
                    referred_by = req.ref_code

    now = datetime.utcnow().isoformat()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "INSERT INTO users (email,password_hash,referral_code,referred_by,telegram_id,created_at)"
                " VALUES (?,?,?,?,?,?)",
                (email_clean, hash_password(req.password), code, referred_by, req.telegram_id, now),
            )
            new_id = cur.lastrowid
            await db.commit()
    except aiosqlite.IntegrityError:
        # فقط خطأ التكرار → البريد مستخدم
        raise HTTPException(409, "البريد الإلكتروني مستخدم مسبقاً")
    except Exception as e:
        log.error(f"Register DB error: {e}")
        raise HTTPException(500, "حدث خطأ داخلي، حاول مجدداً")

    await process_pending_referrals(req.telegram_id or "", new_id, email_clean)
    return {"token": create_token(new_id), "referral_code": code, "message": "تم إنشاء الحساب بنجاح"}

@app.post("/api/login")
async def login(req: LoginRequest):
    email_clean = req.email.lower().strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE email=?", (email_clean,)) as cur:
            user = await cur.fetchone()
    if not user:
        raise HTTPException(401, "البريد الإلكتروني غير مسجل")
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "كلمة المرور غير صحيحة")
    return {"token": create_token(user["id"]), "email": user["email"]}

@app.get("/api/me")
async def get_me(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(stars_amount),0) AS total FROM referral_earnings WHERE user_id=?",
            (user["id"],)
        ) as cur:
            stats = dict(await cur.fetchone())
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE referred_by=?", (user["referral_code"],)
        ) as cur:
            ref_count = (await cur.fetchone())[0]
    return {
        "id": user["id"], "email": user["email"],
        "referral_code": user["referral_code"],
        "referral_url": f"{SITE_URL}/?ref={user['referral_code']}",
        "stars_balance": user["stars_balance"],
        "telegram_id": user["telegram_id"],
        "referrals_count": ref_count,
        "earnings_count": stats["cnt"],
        "total_earned": stats["total"],
    }

@app.get("/api/referrals")
async def get_referrals(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT re.stars_amount AS commission, re.created_at,
                   o.user_id AS buyer, o.stars_amount AS order_stars
            FROM referral_earnings re JOIN orders o ON o.id = re.order_id
            WHERE re.user_id=? ORDER BY re.created_at DESC LIMIT 50
        """, (user["id"],)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

@app.get("/api/withdrawals")
async def get_withdrawals(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM withdrawals WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
            (user["id"],)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

# ═══════════════════════════════════════════════════════════════════════════
# Resolve username
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/api/resolve-username")
async def resolve_username(req: ResolveUsernameRequest):
    username = req.username.lstrip("@").strip()
    if not username or len(username) < 3:
        raise HTTPException(400, "معرف غير صالح")
    
    avatar_url = f"https://t.me/i/userpic/320/{username}.jpg"
    full_name = username
    found = None
    
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    if token:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(
                    f"https://api.telegram.org/bot{token}/getChat",
                    params={"chat_id": f"@{username}"}, timeout=8,
                )
            data = r.json()
            if data.get("ok"):
                chat = data["result"]
                full_name = " ".join(filter(None, [
                    chat.get("first_name",""), chat.get("last_name","")
                ])) or username
                return {"found": True, "username": username,
                        "full_name": full_name, "avatar_url": avatar_url,
                        "is_group": chat.get("type","") != "private"}
        except Exception:
            pass

    try:
        import re
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://t.me/{username}", timeout=5)
            if r.status_code == 200:
                text = r.text
                title_match = re.search(r'<meta property="og:title" content="([^"]+)">', text)
                image_match = re.search(r'<meta property="og:image" content="([^"]+)">', text)
                
                if title_match and image_match:
                    scraped_title = title_match.group(1).replace("Telegram: Contact @", "").strip()
                    scraped_image = image_match.group(1)
                    
                    if scraped_title != username and not scraped_title.startswith("Telegram: Contact"):
                        full_name = scraped_title
                        found = True
                    
                    if "t_logo_2x.png" not in scraped_image:
                        avatar_url = scraped_image
                        found = True
    except Exception:
        pass
        
    return {"found": found, "username": username, "full_name": full_name, "avatar_url": avatar_url}

# ═══════════════════════════════════════════════════════════════════════════
# Cryptomus invoice
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/api/create-invoice")
async def create_invoice(req: InvoiceRequest, user: Optional[dict] = Depends(get_optional_user)):
    if not req.user_id.startswith("@") or len(req.user_id) < 2:
        raise HTTPException(400, "المعرف يجب أن يبدأ بـ @")
    if req.stars_amount < MIN_STARS:
        raise HTTPException(400, f"الحد الأدنى {MIN_STARS} نجوم")
    price       = calc_price(req.stars_amount)
    guest_email = user["email"] if user else ((req.email or "").strip().lower() or None)
    if guest_email and ("@" not in guest_email or "." not in guest_email):
        guest_email = None
    ref_code  = req.ref_code or (user.get("referred_by") if user else None)
    order_uid = f"{req.user_id}_{req.stars_amount}_{int(datetime.utcnow().timestamp())}"
    invoice   = await asyncio.to_thread(cryptomus_create_invoice, price, order_uid)
    inv_id    = invoice["uuid"]
    now       = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders (user_id,stars_amount,price_usd,status,invoice_id,"
            "referrer_code,account_id,email,payment_method,created_at) VALUES (?,?,?,'pending',?,?,?,?,'cryptomus',?)",
            (req.user_id, req.stars_amount, price, inv_id,
             ref_code, user["id"] if user else None, guest_email, now),
        )
        await db.commit()
    await send_new_order_alert({"user_id": req.user_id, "stars_amount": req.stars_amount,
                                "price_usd": price, "referrer_code": ref_code,
                                "email": guest_email, "created_at": now, "payment_method": "cryptomus"})
    return {"invoice_url": invoice["url"], "invoice_id": inv_id,
            "amount_usd": price, "stars_amount": req.stars_amount}

# ═══════════════════════════════════════════════════════════════════════════
# Crypto Pay invoice
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/api/create-cryptopay-invoice")
async def create_cryptopay_invoice(req: CryptoPayInvoiceRequest, user: Optional[dict] = Depends(get_optional_user)):
    if not CRYPTOPAY_API_TOKEN:
        raise HTTPException(503, "Crypto Pay is not configured")
    if not req.user_id.startswith("@") or len(req.user_id) < 2:
        raise HTTPException(400, "المعرف يجب أن يبدأ بـ @")
    if req.stars_amount < MIN_STARS:
        raise HTTPException(400, f"الحد الأدنى {MIN_STARS} نجوم")
    price = calc_price(req.stars_amount)
    guest_email = user["email"] if user else ((req.email or "").strip().lower() or None)
    if guest_email and ("@" not in guest_email or "." not in guest_email):
        guest_email = None
    ref_code = req.ref_code or (user.get("referred_by") if user else None)
    
    # Generate random order UUID for internal tracking
    order_uid = f"{req.user_id}_{req.stars_amount}_{int(datetime.utcnow().timestamp())}"
    
    # Localized description
    desc_map = {
        "en": f"Purchase of {req.stars_amount} Telegram Stars",
        "ar": f"شراء {req.stars_amount} نجمة تيليجرام",
        "ru": f"Покупка {req.stars_amount} звезд Telegram",
        "fr": f"Achat de {req.stars_amount} étoiles Telegram",
        "de": f"Kauf von {req.stars_amount} Telegram-Sternen",
        "es": f"Compra de {req.stars_amount} estrellas de Telegram",
        "tr": f"{req.stars_amount} Telegram Yıldızı Satın Alma",
    }
    desc = desc_map.get(req.locale or "en", desc_map["en"])
    
    # DB Insertion first to get order ID
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders (user_id,stars_amount,price_usd,status,invoice_id,"
            "referrer_code,account_id,email,payment_method,created_at) VALUES (?,?,?,'pending',?,?,?,?,'cryptopay',?)",
            (req.user_id, req.stars_amount, price, order_uid,
             ref_code, user["id"] if user else None, guest_email, now),
        )
        order_id = cur.lastrowid
        await db.commit()
    
    payload_str = json.dumps({"order_id": order_id, "user_id": req.user_id, "locale": req.locale})
    try:
        invoice = await asyncio.to_thread(cryptopay_create_invoice, price, f"{desc} — Order #{order_id}", payload_str, order_id)
    except Exception as e:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE orders SET status='failed' WHERE id=?", (order_id,))
            await db.commit()
        raise e
        
    cp_invoice_id = invoice["invoice_id"]
    pay_url = invoice["bot_invoice_url"]
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET cryptopay_invoice_id=? WHERE id=?", (cp_invoice_id, order_id))
        await db.commit()
        
    await send_new_order_alert({"user_id": req.user_id, "stars_amount": req.stars_amount,
                                "price_usd": price, "referrer_code": ref_code,
                                "email": guest_email, "created_at": now, "payment_method": "cryptopay"})
    return {"pay_url": pay_url, "invoice_id": cp_invoice_id, "order_id": order_id,
            "amount_usd": price, "stars_amount": req.stars_amount}

@app.post("/api/check-cryptopay-payment")
async def check_cryptopay_payment(req: CheckTonRequest):
    order = await get_order_by_id(req.order_id)
    if not order:
        raise HTTPException(404, "الطلب غير موجود")
    if order["status"] in ("completed", "paid"):
        return {"success": True, "status": "completed"}
    if order["status"] == "failed":
        return {"success": False, "status": "failed"}
        
    cp_invoice_id = order.get("cryptopay_invoice_id")
    if not cp_invoice_id:
        log.warning(f"🔍 CryptoPay check order #{req.order_id}: لا يوجد cryptopay_invoice_id")
        return {"success": False, "status": "not_found"}
        
    try:
        invoice = await asyncio.to_thread(cryptopay_get_invoice, cp_invoice_id)
    except Exception as e:
        log.error(f"🔍 CryptoPay check order #{req.order_id}: خطأ API: {e}")
        return {"success": False, "status": "error"}
        
    if not invoice:
        log.warning(f"🔍 CryptoPay check order #{req.order_id}: الفاتورة غير موجودة (invoice_id={cp_invoice_id})")
        return {"success": False, "status": "not_found"}
        
    inv_status = invoice.get("status")
    log.info(f"🔍 CryptoPay check order #{req.order_id}: invoice status = {inv_status}")
    
    if inv_status == "expired":
        await set_order_status(order["id"], "expired")
        return {"success": False, "status": "expired", "expired": True}
        
    if inv_status != "paid":
        return {"success": False, "status": "pending"}
        
    # Payment confirmed
    await set_order_status(order["id"], "paid")
    success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
    if not success:
        await asyncio.sleep(5)
        success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
    final = "completed" if success else "failed"
    await set_order_status(order["id"], final)
    
    if success:
        updated = await get_order_by_id(order["id"])
        if updated and updated.get("referrer_code"):
            if updated.get("account_id"):
                await process_referral_commission(updated)
            else:
                commission = calc_commission(order["stars_amount"])
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO pending_referrals (referrer_code,order_id,stars_amount,guest_identifier,guest_email,created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (updated["referrer_code"], order["id"], commission,
                         order["user_id"], order.get("email"), datetime.utcnow().isoformat()),
                    )
                    await db.commit()
        await send_success_alert(order)
        return {"success": True, "status": "completed"}
    else:
        await send_failure_alert(order)
        return {"success": False, "status": "failed"}

# ═══════════════════════════════════════════════════════════════════════════
# TON order
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/api/create-ton-order")
async def create_ton_order(req: TonOrderRequest, user: Optional[dict] = Depends(get_optional_user)):
    if not req.user_id.startswith("@") or len(req.user_id) < 2:
        raise HTTPException(400, "المعرف يجب أن يبدأ بـ @")
    if req.stars_amount < MIN_STARS:
        raise HTTPException(400, f"الحد الأدنى {MIN_STARS} نجوم")
    if not TON_WALLET_ADDRESS:
        raise HTTPException(503, "الدفع بـ TON غير مفعّل حالياً")
    price       = calc_price(req.stars_amount)
    ton_amount  = usd_to_ton(price)
    ton_nano    = ton_to_nano(ton_amount)
    comment     = gen_comment()
    expires_at  = (datetime.utcnow() + timedelta(minutes=TON_INVOICE_EXPIRY_MIN)).isoformat()
    guest_email = user["email"] if user else ((req.email or "").strip().lower() or None)
    ref_code    = req.ref_code or (user.get("referred_by") if user else None)
    now         = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders (user_id,stars_amount,price_usd,status,"
            "referrer_code,account_id,email,payment_method,"
            "ton_amount,ton_amount_nano,comment,expires_at,created_at)"
            " VALUES (?,?,?,'awaiting_payment',?,?,?,'ton_transfer',?,?,?,?,?)",
            (req.user_id, req.stars_amount, price,
             ref_code, user["id"] if user else None, guest_email,
             ton_amount, ton_nano, comment, expires_at, now),
        )
        order_id = cur.lastrowid
        await db.commit()
    await send_new_order_alert({"user_id": req.user_id, "stars_amount": req.stars_amount,
                                "price_usd": price, "referrer_code": ref_code,
                                "email": guest_email, "created_at": now, "payment_method": "ton_transfer"})
    return {"order_id": order_id, "wallet_address": TON_WALLET_ADDRESS,
            "ton_amount": ton_amount, "ton_amount_nano": ton_nano,
            "comment": comment, "expires_at": expires_at,
            "price_usd": price, "stars_amount": req.stars_amount}

# ═══════════════════════════════════════════════════════════════════════════
# TON wallet QR code
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/api/ton-wallet-qr")
async def get_ton_wallet_qr(order_id: Optional[int] = None):
    """
    يولّد صورة QR code لعنوان محفظة TON لتسهيل النسخ بالتصوير بين الأجهزة.
    - بدون order_id: QR لعنوان المحفظة فقط.
    - مع order_id: QR برابط TON عميق (ton://transfer/...) يضمّن المبلغ الدقيق
      والتعليق (comment) الخاص بالطلب، بحيث تفتح محفظة المستخدم (مثل Tonkeeper)
      المعاملة جاهزة مسبقًا عند مسح الكود.
    """
    if not TON_WALLET_ADDRESS:
        raise HTTPException(503, "الدفع بـ TON غير مفعّل حالياً")

    payload = TON_WALLET_ADDRESS
    if order_id is not None:
        order = await get_order_by_id(order_id)
        if not order:
            raise HTTPException(404, "الطلب غير موجود")
        payload = (
            f"ton://transfer/{TON_WALLET_ADDRESS}"
            f"?amount={order['ton_amount_nano']}&text={order['comment']}"
        )

    img = qrcode.make(payload, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# ═══════════════════════════════════════════════════════════════════════════
# Check TON payment
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/api/check-ton-payment")
async def check_ton_payment(req: CheckTonRequest):
    order = await get_order_by_id(req.order_id)
    if not order:
        raise HTTPException(404, "الطلب غير موجود")
    if order["status"] in ("completed", "paid"):
        return {"success": True, "status": "completed"}
    if order["status"] == "failed":
        return {"success": False, "status": "failed", "testnet": bool(order.get("testnet_flagged"))}
    if order.get("expires_at"):
        if datetime.utcnow() > datetime.fromisoformat(order["expires_at"]):
            await set_order_status(order["id"], "expired")
            return {"success": False, "status": "expired", "expired": True}
    tx = await find_ton_payment(order["comment"], order["ton_amount_nano"])
    if not tx["found"]:
        return {"success": False, "status": "not_found"}
    amount_nano = tx["amount_nano"]
    sender      = tx["sender"]
    ton_rcvd    = amount_nano / TON_NANO
    if tx["is_testnet"]:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE orders SET status='failed',testnet_flagged=1 WHERE id=?", (order["id"],))
            await db.commit()
        await send_testnet_fraud_alert(order, ton_rcvd, sender)
        return {"success": False, "status": "testnet_fraud",
                "message": "تم الرفض — شبكة اختبار (Testnet). استخدم عملات حقيقية على Mainnet."}
    if not tx["sufficient"]:
        missing = round((order["ton_amount_nano"] - amount_nano) / TON_NANO, 6)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE orders SET underpayment_flagged=1,underpaid_amount=?,underpaid_detected_at=? WHERE id=?",
                (ton_rcvd, datetime.utcnow().isoformat(), order["id"])
            )
            await db.commit()
        await send_underpaid_alert(order, amount_nano)
        return {"success": False, "status": "underpaid",
                "received": ton_rcvd, "expected": order["ton_amount"], "missing": missing}
    if order["status"] != "awaiting_payment":
        return {"success": True, "status": order["status"]}
    await set_order_status(order["id"], "paid")
    success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
    if not success:
        await asyncio.sleep(5)
        success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
    final = "completed" if success else "failed"
    await set_order_status(order["id"], final)
    if success:
        updated = await get_order_by_id(order["id"])
        if updated and updated.get("referrer_code"):
            if updated.get("account_id"):
                await process_referral_commission(updated)
            else:
                commission = calc_commission(order["stars_amount"])
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO pending_referrals (referrer_code,order_id,stars_amount,guest_identifier,guest_email,created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (updated["referrer_code"], order["id"], commission,
                         order["user_id"], order.get("email"), datetime.utcnow().isoformat()),
                    )
                    await db.commit()
        await send_ton_success_alert(order, ton_rcvd)
        return {"success": True, "status": "completed"}
    else:
        await send_failure_alert(order)
        return {"success": False, "status": "failed"}

# ═══════════════════════════════════════════════════════════════════════════
# Cryptomus webhook
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/cryptomus-webhook")
async def cryptomus_webhook(request: Request):
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    if not verify_cryptomus_webhook(body, body.get("sign", "")):
        log.warning("Cryptomus webhook signature mismatch")
        raise HTTPException(403, "Invalid signature")
    if body.get("update_type") != "invoice_paid":
        return {"ok": True}
    invoice    = body["payload"]
    invoice_id = invoice["uuid"]
    if invoice.get("status") not in ("paid", "paid_over"):
        return {"ok": True}
    order = await get_order_by_invoice(invoice_id)
    if not order or order["status"] == "completed":
        return {"ok": True}
    success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
    if not success:
        await asyncio.sleep(5)
        success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
    if success:
        await set_order_status(order["id"], "completed")
        if order.get("referrer_code"):
            if order.get("account_id"):
                await process_referral_commission(order)
            else:
                commission = calc_commission(order["stars_amount"])
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO pending_referrals (referrer_code,order_id,stars_amount,guest_identifier,guest_email,created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (order["referrer_code"], order["id"], commission,
                         order["user_id"], order.get("email"), datetime.utcnow().isoformat()),
                    )
                    await db.commit()
        await send_success_alert(order)
    else:
        await set_order_status(order["id"], "failed")
        await send_failure_alert(order)
    return {"ok": True}

# ═══════════════════════════════════════════════════════════════════════════
# Crypto Pay webhook
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/cryptopay-webhook/{secret}")
async def cryptopay_webhook(secret: str, request: Request):
    if not CRYPTOPAY_WEBHOOK_SECRET or secret != CRYPTOPAY_WEBHOOK_SECRET:
        raise HTTPException(403, "Invalid webhook secret path")
        
    body_bytes = await request.body()
    signature = request.headers.get("crypto-pay-api-signature", "")
    
    if not cryptopay_verify_webhook(body_bytes, signature):
        log.warning("Crypto Pay webhook signature mismatch")
        raise HTTPException(403, "Invalid signature")
        
    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON")
        
    if body.get("update_type") != "invoice_paid":
        return {"ok": True}
        
    invoice = body.get("payload", {})
    cp_invoice_id = invoice.get("invoice_id")
    if not cp_invoice_id or invoice.get("status") != "paid":
        return {"ok": True}
        
    # Find order
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE cryptopay_invoice_id=?", (cp_invoice_id,)) as cur:
            row = await cur.fetchone()
            
    if not row:
        return {"ok": True}
    order = dict(row)
    
    if order["status"] == "completed":
        return {"ok": True}
        
    # Try fulfill
    success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
    if not success:
        await asyncio.sleep(5)
        success = await buy_stars_fragment(order["user_id"], order["stars_amount"], order["id"])
        
    if success:
        await set_order_status(order["id"], "completed")
        if order.get("referrer_code"):
            if order.get("account_id"):
                await process_referral_commission(order)
            else:
                commission = calc_commission(order["stars_amount"])
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO pending_referrals (referrer_code,order_id,stars_amount,guest_identifier,guest_email,created_at)"
                        " VALUES (?,?,?,?,?,?)",
                        (order["referrer_code"], order["id"], commission,
                         order["user_id"], order.get("email"), datetime.utcnow().isoformat()),
                    )
                    await db.commit()
        await send_success_alert(order)
    else:
        await set_order_status(order["id"], "failed")
        await send_failure_alert(order)
        
    return {"ok": True}

# ═══════════════════════════════════════════════════════════════════════════
# Withdrawal
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/api/request-withdrawal")
async def request_withdrawal(req: WithdrawalRequest, user: dict = Depends(get_current_user)):
    if req.amount_usd < 2.0:
        raise HTTPException(400, "الحد الأدنى للسحب هو 2 دولار")
    if user["usd_balance"] < req.amount_usd:
        raise HTTPException(400, "رصيدك غير كافٍ")
    if req.method == "stars" and not req.recipient_username.startswith("@"):
        raise HTTPException(400, "المعرف يجب أن يبدأ بـ @ للسحب كنجوم")
    if req.method in ["usdt", "ton"] and not req.wallet_address:
        raise HTTPException(400, "عنوان المحفظة مطلوب")
        
    now = datetime.utcnow().isoformat()
    stars_amount = 0
    if req.method == "stars":
        stars_amount = int(req.amount_usd / 0.015)
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET usd_balance = usd_balance - ? WHERE id=?",
                         (req.amount_usd, user["id"]))
        cur = await db.execute(
            "INSERT INTO withdrawals (user_id,stars_amount,recipient_username,method,wallet_address,usd_amount,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user["id"], stars_amount, req.recipient_username, req.method, req.wallet_address, req.amount_usd, "pending", now),
        )
        wid = cur.lastrowid
        await db.commit()
    
    target = req.recipient_username if req.method == "stars" else req.wallet_address
    await notify_owner(f"💸 طلب سحب جديد ({req.method}): {req.amount_usd}$ ({target}) من {user['email']}")
    return {"status": "pending", "message": "طلب السحب قيد المعالجة (خلال 24 ساعة) ✅"}

# ═══════════════════════════════════════════════════════════════════════════
# Recent Orders (public)
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/api/recent-orders")
async def recent_orders(filter: str = "last_24h", sort: str = "newest", limit: int = 20):
    limit = max(1, min(limit, 100))
    now   = datetime.utcnow()
    time_map = {"last_hour": now - timedelta(hours=1), "last_24h": now - timedelta(hours=24),
                "last_week": now - timedelta(days=7), "last_month": now - timedelta(days=30)}
    sort_map  = {"newest": "created_at DESC", "price_desc": "price_usd DESC",
                 "price_asc": "price_usd ASC", "qty_desc": "stars_amount DESC", "qty_asc": "stars_amount ASC"}
    where  = "status IN ('completed','paid')"
    params: list = []
    if filter in time_map:
        where += " AND created_at >= ?"
        params.append(time_map[filter].isoformat())
    params.append(limit)
    query = (f"SELECT user_id,stars_amount,price_usd,created_at,payment_method "
             f"FROM orders WHERE {where} ORDER BY {sort_map.get(sort,'created_at DESC')} LIMIT ?")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

# ═══════════════════════════════════════════════════════════════════════════
# Config (public)
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/api/config")
async def public_config():
    return {"telegram_channel_url": TELEGRAM_CHANNEL_URL, "price_per_star": GLOBAL_CONFIG["PRICE_PER_STAR"]}

# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# New Admin JWT Auth
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/admin/login")
async def admin_login_api(req: Request):
    data = await req.json()
    email = data.get("email")
    password = data.get("password")
    captcha = data.get("captcha")
    
    if not captcha:
        raise HTTPException(400, "الرجاء حل الكابتشا")
        
    secret_key = "6Lfx020tAAAAABnlMtH9YIW6hPoz03Ic1aMgY4_d"
    async with httpx.AsyncClient() as client:
        res = await client.post("https://www.google.com/recaptcha/api/siteverify", data={
            "secret": secret_key,
            "response": captcha
        })
        if not res.json().get("success"):
            raise HTTPException(400, "فشل التحقق من الكابتشا")
            
    if email == os.getenv("ADMIN_EMAIL", "admin@stars-hub.com") and password == os.getenv("ADMIN_PASSWORD", "strongpassword123"):
        token = jwt.encode({"role": "admin", "exp": datetime.utcnow() + timedelta(days=7)}, ADMIN_SECRET, algorithm="HS256")
        return {"token": token}
    raise HTTPException(401, "Invalid credentials")

async def verify_admin(req: Request):
    auth = req.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(401, "Unauthorized")
    token = auth.split(" ")[1]
    try:
        payload = jwt.decode(token, ADMIN_SECRET, algorithms=["HS256"])
        if payload.get("role") != "admin":
            raise HTTPException(403, "Forbidden")
    except Exception:
        raise HTTPException(401, "Invalid token")

@app.post("/api/admin/commission")
async def admin_update_commission(req: Request):
    await verify_admin(req)
    data = await req.json()
    new_comm = float(data.get("commission", 10.0))
    if new_comm < 10.0 or new_comm > 40.0:
        raise HTTPException(400, "العمولة يجب أن تكون بين 10% و 40%")
    GLOBAL_CONFIG["COMMISSION_PERCENT"] = new_comm
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", ("COMMISSION_PERCENT", str(new_comm)))
        await db.commit()
    return {"ok": True, "commission": new_comm}

@app.get("/api/admin/withdrawals")
async def admin_get_withdrawals(req: Request):
    await verify_admin(req)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT w.*, u.email FROM withdrawals w
            JOIN users u ON u.id = w.user_id
            WHERE w.status = 'pending' ORDER BY w.created_at DESC
        """) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

@app.post("/api/admin/withdrawals/{wid}/pay")
async def admin_pay_withdrawal(wid: int, req: Request):
    await verify_admin(req)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE withdrawals SET status = 'completed' WHERE id=?", (wid,))
        await db.commit()
    return {"ok": True}

@app.post("/api/admin/price")
async def admin_update_price(req: Request):
    await verify_admin(req)
    data = await req.json()
    new_price = float(data.get("price", 0))
    # Enforce +/- 10% limit from base price
    base = BASE_PRICE_PER_STAR
    if new_price < base * 0.9 or new_price > base * 1.1:
        raise HTTPException(400, f"السعر يجب أن يكون بين {base * 0.9:.4f} و {base * 1.1:.4f}")
    
    GLOBAL_CONFIG["PRICE_PER_STAR"] = new_price
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", ("PRICE_PER_STAR", str(new_price)))
        await db.commit()
    return {"status": "success", "new_price": new_price}


# Admin — Orders & Withdrawals
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/admin/dashboard")
async def admin_dashboard_page():
    return FileResponse(os.path.join(_BASE_DIR, "admin_dashboard.html"))

@app.get("/admin/orders")
async def admin_orders(req: Request, status: str = "", search: str = "", limit: int = 200):
    await verify_admin(req)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM orders"
        params = []
        conditions = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if search:
            conditions.append("(user_id LIKE ? OR CAST(id AS TEXT) LIKE ? OR comment LIKE ?)")
            s = f"%{search}%"
            params.extend([s, s, s])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

@app.get("/admin/withdrawals")
async def admin_withdrawals(req: Request):
    await verify_admin(req)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT w.*, u.email FROM withdrawals w
            JOIN users u ON u.id = w.user_id ORDER BY w.id DESC
        """) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]

@app.post("/admin/approve-withdrawal/{wid}")
async def admin_approve(req: Request, wid: int):
    await verify_admin(req)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)) as cur:
            w = await cur.fetchone()
    if not w:
        raise HTTPException(404, "طلب غير موجود")
    w = dict(w)
    if w["status"] != "pending":
        raise HTTPException(400, f"الطلب في حالة '{w['status']}'")
    success = await buy_stars_fragment(w["recipient_username"], w["stars_amount"])
    status  = "completed" if success else "failed"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (status, wid))
        if not success:
            await db.execute("UPDATE users SET stars_balance = stars_balance + ? WHERE id=?",
                             (w["stars_amount"], w["user_id"]))
        await db.commit()
    return {"status": status}

# ═══════════════════════════════════════════════════════════════════════════
# Admin — Referrals Panel API
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/api/admin/referrals")
async def admin_referrals_data(req: Request):
    await verify_admin(req)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # إحصائيات عامة
        async with db.execute("""
            SELECT COUNT(DISTINCT user_id) AS total_referrers,
                   COUNT(*) AS total_transactions,
                   COALESCE(SUM(stars_amount),0) AS total_stars_paid
            FROM referral_earnings
        """) as cur:
            stats = dict(await cur.fetchone())
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM pending_referrals"
        ) as cur:
            stats["pending_count"] = (await cur.fetchone())[0]
        async with db.execute("""
            SELECT COUNT(DISTINCT id) AS total_users,
                   COUNT(CASE WHEN referred_by IS NOT NULL THEN 1 END) AS referred_users
            FROM users
        """) as cur:
            us = dict(await cur.fetchone())
        stats.update(us)
        # أفضل المُحيلين
        async with db.execute("""
            SELECT u.email, u.referral_code, u.stars_balance,
                   COUNT(re.id) AS tx_count,
                   COALESCE(SUM(re.stars_amount),0) AS total_earned,
                   (SELECT COUNT(*) FROM users u2 WHERE u2.referred_by = u.referral_code) AS referred_count
            FROM users u
            LEFT JOIN referral_earnings re ON re.user_id = u.id
            GROUP BY u.id
            HAVING total_earned > 0
            ORDER BY total_earned DESC
            LIMIT 50
        """) as cur:
            top_referrers = [dict(r) for r in await cur.fetchall()]
        # آخر العمولات
        async with db.execute("""
            SELECT re.stars_amount AS commission, re.created_at,
                   u.email AS referrer_email,
                   o.user_id AS buyer, o.stars_amount AS order_stars
            FROM referral_earnings re
            JOIN users u ON u.id = re.user_id
            JOIN orders o ON o.id = re.order_id
            ORDER BY re.created_at DESC LIMIT 100
        """) as cur:
            recent_earnings = [dict(r) for r in await cur.fetchall()]
        # إحالات معلقة
        async with db.execute("""
            SELECT pr.*, o.user_id AS buyer, o.stars_amount AS order_stars, o.status AS order_status
            FROM pending_referrals pr
            LEFT JOIN orders o ON o.id = pr.order_id
            ORDER BY pr.created_at DESC LIMIT 50
        """) as cur:
            pending = [dict(r) for r in await cur.fetchall()]
    return {
        "stats": stats,
        "top_referrers": top_referrers,
        "recent_earnings": recent_earnings,
        "pending_referrals": pending,
    }

# ═══════════════════════════════════════════════════════════════════════════
# Admin — Telegram Bot
# ═══════════════════════════════════════════════════════════════════════════
_pending_search: dict = {}

def _admin_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "📊 آخر ساعة",  "callback_data": "r_hour"},
         {"text": "📅 اليوم",     "callback_data": "r_today"}],
        [{"text": "📆 الأسبوع",   "callback_data": "r_week"},
         {"text": "🗓 الشهر",     "callback_data": "r_month"}],
        [{"text": "🔍 بحث @username",  "callback_data": "s_user"},
         {"text": "🔢 بحث رمز TON",    "callback_data": "s_comment"}],
    ]}

async def _bot_send(chat_id: str, text: str, markup: dict = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        payload["reply_markup"] = json.dumps(markup)
    try:
        async with httpx.AsyncClient() as c:
            await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         json=payload, timeout=10)
    except Exception:
        pass

async def _bot_answer_cb(cb_id: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    try:
        async with httpx.AsyncClient() as c:
            await c.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                         json={"callback_query_id": cb_id}, timeout=5)
    except Exception:
        pass

async def _build_report(period: str) -> str:
    now = datetime.utcnow()
    since_map = {"hour": now - timedelta(hours=1), "today": now.replace(hour=0,minute=0,second=0,microsecond=0),
                 "week": now - timedelta(days=7), "month": now - timedelta(days=30)}
    label_map  = {"hour": "آخر ساعة", "today": "اليوم", "week": "الأسبوع", "month": "الشهر"}
    since = since_map.get(period, now - timedelta(hours=24))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT COUNT(*) AS cnt,
                   COALESCE(SUM(stars_amount),0) AS stars,
                   COALESCE(SUM(price_usd),0)    AS usd
            FROM orders WHERE status IN ('completed','paid') AND created_at >= ?
        """, (since.isoformat(),)) as cur:
            r = dict(await cur.fetchone())
    profit = round(r["stars"] / 1000 * 3.0, 2)
    return (f"📊 <b>تقرير {label_map.get(period,'')}</b>\n\n"
            f"🛒 العمليات: <b>{r['cnt']}</b>\n"
            f"⭐ النجوم: <b>{int(r['stars']):,}</b>\n"
            f"💵 الإيرادات: <b>${r['usd']:.2f}</b>\n"
            f"💰 الربح التقريبي: <b>${profit:.2f}</b>")

async def _search_by_username(username: str) -> str:
    uid = username.strip()
    if not uid.startswith("@"):
        uid = "@" + uid
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id,stars_amount,price_usd,status,payment_method,created_at"
            " FROM orders WHERE user_id=? ORDER BY created_at DESC",
            (uid,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    if not rows:
        return f"❌ لا توجد عمليات للمعرف <b>{uid}</b>"
    icons = {"completed":"✅","paid":"✅","failed":"❌","pending":"⏳","awaiting_payment":"⏳","expired":"⌛"}
    lines = [f"🔍 <b>جميع عمليات {uid}</b> ({len(rows)} عملية)\n"]
    for r in rows:
        pm = "💎 TON" if r["payment_method"] == "ton_transfer" else "💳 Crypto"
        lines.append(f"{icons.get(r['status'],'❓')} #{r['id']} | {r['stars_amount']:,}⭐ | "
                     f"${r['price_usd']:.2f} | {pm} | {r['created_at'][:16].replace('T',' ')}")
    return "\n".join(lines)

async def _search_by_comment(comment: str) -> str:
    comment = comment.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM orders WHERE comment=?", (comment,)) as cur:
            row = await cur.fetchone()
    if not row:
        return f"❌ لا توجد عملية بالرمز <code>{comment}</code>"
    r      = dict(row)
    icons  = {"completed":"✅","paid":"✅","failed":"❌","pending":"⏳","awaiting_payment":"⏳","expired":"⌛"}
    flags  = []
    if r.get("testnet_flagged"):      flags.append("🚨 Testnet fraud")
    if r.get("underpayment_flagged"): flags.append(f"⚠️ دفع ناقص ({r.get('underpaid_amount',0):.4f} TON)")
    return (f"🔢 <b>عملية TON — رمز {r['comment']}</b>\n\n"
            f"👤 {r['user_id']}\n⭐ {r['stars_amount']:,} | 💵 ${r['price_usd']:.2f}\n"
            f"🪙 TON: {r.get('ton_amount') or '—'}\n"
            f"{icons.get(r['status'],'❓')} الحالة: <b>{r['status']}</b>\n"
            f"🚩 {' | '.join(flags) if flags else '—'}\n"
            f"🕐 {r['created_at'][:16].replace('T',' ')}")

async def handle_admin_commands(update: dict):
    owner = str(os.getenv("OWNER_CHAT_ID", ""))
    if "message" in update:
        msg     = update["message"]
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "").strip()
        if chat_id != owner:
            await _bot_send(chat_id, "🚫 غير مصرح.")
            return
        if chat_id in _pending_search:
            mode = _pending_search.pop(chat_id)
            result = await (_search_by_username(text) if mode == "user" else _search_by_comment(text))
            await _bot_send(chat_id, result, _admin_keyboard())
            return
        if text in ("/admin", "/start"):
            await _bot_send(chat_id, "👋 <b>لوحة تحكم StarsHub</b>\nاختر:", _admin_keyboard())
        else:
            await _bot_send(chat_id, "اكتب /admin لفتح لوحة التحكم.")
    elif "callback_query" in update:
        cb      = update["callback_query"]
        chat_id = str(cb.get("from", {}).get("id", ""))
        data    = cb.get("data", "")
        cb_id   = cb.get("id", "")
        await _bot_answer_cb(cb_id)
        if chat_id != owner:
            return
        report_map = {"r_hour": "hour", "r_today": "today", "r_week": "week", "r_month": "month"}
        if data in report_map:
            await _bot_send(chat_id, await _build_report(report_map[data]), _admin_keyboard())
        elif data == "s_user":
            _pending_search[chat_id] = "user"
            await _bot_send(chat_id, "✏️ أرسل المعرف (@username):")
        elif data == "s_comment":
            _pending_search[chat_id] = "comment"
            await _bot_send(chat_id, "✏️ أرسل رمز التعليق (9 أرقام):")

@app.post("/bot-webhook")
async def bot_webhook(request: Request):
    try:
        update = await request.json()
        await handle_admin_commands(update)
    except Exception as e:
        log.warning(f"bot-webhook error: {e}")
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
