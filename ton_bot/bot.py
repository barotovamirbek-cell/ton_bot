# bot.py
import asyncio
import json
import time
from typing import Optional, List

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.filters import Command

# -------------------------
# Загрузка конфигурации
# -------------------------
with open("config.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

TELEGRAM_TOKEN = CONFIG.get("telegram_token")
TON_API_KEY = CONFIG.get("ton_api_key", "")
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
    except:
        return {}

def save_state(state: dict):
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()
if "chat_monitors" not in state:
    state["chat_monitors"] = {}

# -------------------------
# Toncenter API
# -------------------------
TONCENTER_BASE = "https://toncenter.com/api/v2"
HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

async def http_get(session: aiohttp.ClientSession, path: str, params: dict = None) -> dict:
    url = f"{TONCENTER_BASE}/{path}"
    async with session.get(url, params=params, headers=HEADERS, timeout=20) as resp:
        return await resp.json()

async def get_transactions(session: aiohttp.ClientSession, address: str, limit: int = 20, to_lt: Optional[str] = None) -> List[dict]:
    params = {"address": address, "limit": limit}
    if to_lt:
        params["to_lt"] = to_lt
    try:
        res = await http_get(session, "getTransactions", params)
        return res.get("result", []) if res.get("ok") else []
    except:
        return []

def nanotons_to_ton(nano: int) -> float:
    return nano / 1_000_000_000

def fmt_amount(nano: int) -> str:
    return f"{nanotons_to_ton(nano):,.9f} TON".rstrip("0").rstrip(".")

def fmt_time(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

# -------------------------
# Мониторинг чатов
# -------------------------
def get_monitor(chat_id: int) -> dict:
    return state["chat_monitors"].get(str(chat_id))

def set_monitor(chat_id: int, address: str, last_lt: Optional[str]):
    state["chat_monitors"][str(chat_id)] = {"address": address, "last_lt": last_lt}
    save_state(state)

# -------------------------
# Бот
# -------------------------
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

@dp.message(Command(commands=["start"]))
async def cmd_start(msg: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="/balance")
    kb.button(text="/transactions")
    kb.button(text="/monitor_start")
    kb.button(text="/monitor_stop")
    await msg.answer(
        "Привет! Я бот для отслеживания баланса и транзакций TON.\n\n"
        "Доступные команды:\n"
        "/balance - показать баланс\n"
        "/transactions [N] - показать последние N транзакций\n"
        "/setaddr <address> - установить адрес для этого чата\n"
        "/monitor_start - включить уведомления о новых транзакциях\n"
        "/monitor_stop - отключить уведомления\n",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.as_markup(resize_keyboard=True)
    )

@dp.message(Command(commands=["setaddr"]))
async def cmd_setaddr(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Использование: /setaddr <TON address>")
        return
    addr = parts[1].strip()
    mon = get_monitor(msg.chat.id)
    last_lt = mon["last_lt"] if mon else None
    set_monitor(msg.chat.id, addr, last_lt)
    await msg.answer(f"Адрес для этого чата установлен: <code>{addr}</code>", parse_mode=ParseMode.HTML)

# -------------------------
# Loop мониторинга транзакций
# -------------------------
async def poll_loop():
    async with aiohttp.ClientSession() as session:
        while True:
            for chat_id_str, info in state["chat_monitors"].items():
                chat_id = int(chat_id_str)
                address = info.get("address")
                last_lt = info.get("last_lt")
                if not address:
                    continue
                try:
                    txs = await get_transactions(session, address, limit=20)
                    if not txs:
                        continue
                    newest_lt = txs[0].get("in_msg", {}).get("lt") or txs[0].get("lt")
                    if not last_lt:
                        state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
                        save_state(state)
                        continue
                    new_items = [tx for tx in txs if int(tx.get("in_msg", {}).get("lt") or tx.get("lt") or 0) > int(last_lt)]
                    new_items = sorted(new_items, key=lambda t: int(t.get("in_msg", {}).get("lt") or t.get("lt") or 0))
                    for tx in new_items:
                        in_msg = tx.get("in_msg") or {}
                        src = in_msg.get("source") or "?"
                        dst = in_msg.get("destination") or "?"
                        await bot.send_message(
                            chat_id,
                            f"🔔 <b>Новая транзакция</b>\nАдрес: <code>{address}</code>\n"
                            f"LT: {in_msg.get('lt') or tx.get('lt')}\nFrom: <code>{src}</code>\nTo: <code>{dst}</code>",
                            parse_mode=ParseMode.HTML
                        )
                    if new_items:
                        state["chat_monitors"][chat_id_str]["last_lt"] = new_items[-1].get("in_msg", {}).get("lt") or new_items[-1].get("lt")
                        save_state(state)
                except Exception as e:
                    print("Poll error:", e)
            await asyncio.sleep(POLL_INTERVAL)

# -------------------------
# Запуск
# -------------------------
async def main():
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
