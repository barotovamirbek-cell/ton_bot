# bot.py
import asyncio
import json
import time
import os
from typing import Optional, List

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.filters import Command

# -------------------------
# Загрузка конфигурации
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

TELEGRAM_TOKEN = CONFIG.get("telegram_token")
TON_API_KEY = CONFIG.get("ton_api_key", "")
DEFAULT_ADDRESS = CONFIG.get("address", "").strip()
POLL_INTERVAL = float(CONFIG.get("poll_interval", 8))
STORAGE_FILE = CONFIG.get("storage_file", "state.json")

if not TELEGRAM_TOKEN:
    raise SystemExit("Укажите TELEGRAM_TOKEN в config.json")

# -------------------------
# Persistent storage
# -------------------------
def load_state() -> dict:
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict):
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()
if "chat_monitors" not in state:
    state["chat_monitors"] = {}

# -------------------------
# HTTP Client для Toncenter
# -------------------------
TONCENTER_BASE = "https://toncenter.com/api/v2"
HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

async def http_get(session: aiohttp.ClientSession, path: str, params: dict = None) -> dict:
    url = f"{TONCENTER_BASE}/{path}"
    async with session.get(url, params=params, headers=HEADERS, timeout=20) as resp:
        resp.raise_for_status()
        return await resp.json()

async def get_balance(session: aiohttp.ClientSession, address: str) -> Optional[int]:
    try:
        res = await http_get(session, "getAddressInformation", {"address": address})
        if res.get("ok"):
            info = res.get("result", {})
            balance = info.get("balance")
            return int(balance) if balance else None
        return None
    except Exception as e:
        print("get_balance error:", e)
        return None

async def get_transactions(session: aiohttp.ClientSession, address: str, limit: int = 20, to_lt: Optional[str] = None) -> List[dict]:
    params = {"address": address, "limit": limit}
    if to_lt:
        params["to_lt"] = to_lt
    try:
        res = await http_get(session, "getTransactions", params)
        return res.get("result", []) if res.get("ok") else []
    except Exception as e:
        print("get_transactions error:", e)
        return []

# -------------------------
# Утилиты
# -------------------------
def nanotons_to_ton(nano: int) -> float:
    return nano / 1_000_000_000.0

def fmt_amount(nano: int) -> str:
    return f"{nanotons_to_ton(nano):,.9f} TON".rstrip("0").rstrip(".")

def fmt_time(ts: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)

def analyze_transaction_for_address(tx: dict, address: str) -> dict:
    incoming = 0
    outgoing = 0
    in_msg = tx.get("in_msg")
    if in_msg:
        src = in_msg.get("source")
        dest = in_msg.get("destination")
        val = int(in_msg.get("value", 0) or 0)
        if dest and dest.lower() == address.lower():
            incoming += val
        if src and src.lower() == address.lower():
            outgoing += val
    out_msgs = tx.get("out_msgs") or []
    for m in out_msgs:
        src = m.get("source")
        dest = m.get("destination")
        val = int(m.get("value", 0) or 0)
        if dest and dest.lower() == address.lower():
            incoming += val
        if src and src.lower() == address.lower():
            outgoing += val
    net = incoming - outgoing
    direction = "incoming" if net > 0 else ("outgoing" if net < 0 else "self/none")
    return {"incoming": incoming, "outgoing": outgoing, "net": net, "direction": direction}

def tx_summary(tx: dict, address: str) -> str:
    lt = tx.get("in_msg", {}).get("lt") or tx.get("lt") or ""
    utime = tx.get("utime") or tx.get("created_at") or int(time.time())
    analysis = analyze_transaction_for_address(tx, address)
    net = analysis["net"]
    dirc = analysis["direction"]
    note = "(body present)" if tx.get("in_msg", {}).get("body") else ""
    return f"LT={lt} | {fmt_time(utime)} | {dirc.upper()} | {fmt_amount(abs(net))} {note}"

# -------------------------
# Инициализация бота v3
# -------------------------
bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# -------------------------
# Команды бота
# -------------------------
@dp.message(Command(commands=["start"]))
async def cmd_start(msg: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="/balance")
    kb.button(text="/transactions")
    kb.button(text="/monitor_start")
    kb.button(text="/monitor_stop")
await msg.answer(f"Адрес для этого чата установлен: <code>{addr}</code>")
        "Привет! Я бот для отслеживания баланса и транзакций TON.\n\n"
        "Доступные команды:\n"
        "/balance - показать баланс\n"
        "/transactions [N] - показать последние N транзакций\n"
        "/setaddr <address> - установить адрес для этого чата\n"
        "/monitor_start - включить уведомления о новых транзакциях\n"
        "/monitor_stop - отключить уведомления\n",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )

# -------------------------
# Мониторинг транзакций
# -------------------------
async def poll_loop():
    async with aiohttp.ClientSession() as sess:
        while True:
            monitors = dict(state.get("chat_monitors", {}))
            for chat_id_str, info in monitors.items():
                chat_id = int(chat_id_str)
                address = info.get("address") or DEFAULT_ADDRESS
                last_lt = info.get("last_lt")
                if not address:
                    continue
                try:
                    txs = await get_transactions(sess, address, limit=20)
                    if not txs:
                        continue
                    newest_lt = txs[0].get("in_msg", {}).get("lt") or txs[0].get("lt")
                    if not last_lt:
                        state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
                        save_state(state)
                        continue
                    new_items = [tx for tx in txs if int(tx.get("in_msg", {}).get("lt") or tx.get("lt") or "0") > int(last_lt)]
                    new_items.sort(key=lambda t: int((t.get("in_msg", {}).get("lt") or t.get("lt") or "0")))
                    for tx in new_items:
                        summary = tx_summary(tx, address)
                        in_msg = tx.get("in_msg") or {}
                        src = in_msg.get("source") or "?"
                        dst = in_msg.get("destination") or "?"
                        text = (f"🔔 <b>Новая транзакция</b>\nАдрес: <code>{address}</code>\n"
                                f"{summary}\nFrom: <code>{src}</code>\nTo: <code>{dst}</code>\nLT: {in_msg.get('lt') or tx.get('lt')}")
                        await bot.send_message(chat_id, text)
                    if new_items:
                        newest = new_items[-1]
                        newest_lt = newest.get("in_msg", {}).get("lt") or newest.get("lt")
                        state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
                        save_state(state)
                except Exception as e:
                    print("poll error for", address, e)
            await asyncio.sleep(POLL_INTERVAL)

# -------------------------
# Запуск бота
# -------------------------
async def main():
    print("Бот запускается...")
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
