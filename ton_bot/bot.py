# bot.py
import asyncio
import os
import json
import time
from typing import Optional, List

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# -------------------------
# Конфигурация через системные переменные
# -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise SystemExit("Укажите TELEGRAM_BOT_TOKEN как системную переменную")

TON_API_KEY = os.getenv("TON_API_KEY", "")
DEFAULT_ADDRESS = os.getenv("DEFAULT_ADDRESS", "").strip()
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 8))
STORAGE_FILE = os.getenv("STORAGE_FILE", "state.json")

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

def get_monitor(chat_id: int) -> dict:
    return state["chat_monitors"].get(str(chat_id))

def set_monitor(chat_id: int, address: str, last_lt: Optional[str] = None):
    state["chat_monitors"][str(chat_id)] = {"address": address, "last_lt": last_lt}
    save_state(state)

def clear_monitor(chat_id: int):
    if str(chat_id) in state["chat_monitors"]:
        del state["chat_monitors"][str(chat_id)]
        save_state(state)

# -------------------------
# Toncenter API
# -------------------------
TONCENTER_BASE = "https://toncenter.com/api/v2"
HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

async def http_get(session: aiohttp.ClientSession, path: str, params: dict = None) -> dict:
    url = f"{TONCENTER_BASE}/{path}"
    async with session.get(url, params=params, headers=HEADERS, timeout=20) as resp:
        return await resp.json()

async def get_balance(session: aiohttp.ClientSession, address: str) -> Optional[int]:
    try:
        res = await http_get(session, "getAddressInformation", {"address": address})
        if res.get("ok"):
            balance = res.get("result", {}).get("balance")
            if isinstance(balance, str):
                return int(balance)
            return balance
    except:
        return None

async def get_transactions(session: aiohttp.ClientSession, address: str, limit: int = 5) -> List[dict]:
    try:
        res = await http_get(session, "getTransactions", {"address": address, "limit": limit})
        return res.get("result", []) if res.get("ok") else []
    except:
        return []

def nanotons_to_ton(nano: int) -> float:
    return nano / 1_000_000_000.0

def fmt_amount(nano: int) -> str:
    return f"{nanotons_to_ton(nano):,.9f} TON".rstrip("0").rstrip(".")

# -------------------------
# Бот
# -------------------------
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# /start
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="/balance")
    kb.button(text="/transactions")
    kb.button(text="/setaddr")
    kb.button(text="/monitor_start")
    kb.button(text="/monitor_stop")
    await msg.answer(
        "Привет! Я бот для отслеживания баланса и транзакций TON.\n\n"
        "Доступные команды:\n"
        "/balance - показать баланс\n"
        "/transactions - показать последние 5 транзакций\n"
        "/setaddr <address> - установить адрес\n"
        "/monitor_start - включить уведомления\n"
        "/monitor_stop - отключить уведомления",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.as_markup(resize_keyboard=True)
    )

# /setaddr
@dp.message(Command("setaddr"))
async def cmd_setaddr(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Использование: /setaddr <TON address>")
        return
    addr = parts[1]
    mon = get_monitor(msg.chat.id)
    last_lt = mon["last_lt"] if mon else None
    set_monitor(msg.chat.id, addr, last_lt)
    await msg.answer(f"Адрес установлен: <code>{addr}</code>", parse_mode=ParseMode.HTML)

# /balance
@dp.message(Command("balance"))
async def cmd_balance(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала установите адрес с помощью /setaddr")
        return
    async with aiohttp.ClientSession() as sess:
        bal = await get_balance(sess, mon["address"])
    if bal is None:
        await msg.answer("Не удалось получить баланс")
    else:
        await msg.answer(f"Баланс адреса <code>{mon['address']}</code>: {fmt_amount(bal)}", parse_mode=ParseMode.HTML)

# /transactions
@dp.message(Command("transactions"))
async def cmd_transactions(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала установите адрес с помощью /setaddr")
        return
    async with aiohttp.ClientSession() as sess:
        txs = await get_transactions(sess, mon["address"])
    if not txs:
        await msg.answer("Транзакций нет")
        return
    text = ""
    for tx in txs:
        val = int(tx.get("in_msg", {}).get("value", 0) or 0)
        text += f"From: {tx.get('in_msg', {}).get('source','?')} To: {tx.get('in_msg', {}).get('destination','?')} Amount: {fmt_amount(val)}\n"
    await msg.answer(text or "Транзакций нет")

# /monitor_start
@dp.message(Command("monitor_start"))
async def cmd_monitor_start(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала установите адрес с помощью /setaddr")
        return
    await msg.answer(f"Уведомления включены для адреса: {mon['address']}")

# /monitor_stop
@dp.message(Command("monitor_stop"))
async def cmd_monitor_stop(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if mon:
        clear_monitor(msg.chat.id)
        await msg.answer("Уведомления отключены")
    else:
        await msg.answer("Уведомления не были включены")

# -------------------------
# Poll loop
# -------------------------
async def poll_loop():
    async with aiohttp.ClientSession() as sess:
        while True:
            monitors = dict(state.get("chat_monitors", {}))
            for chat_id_str, info in monitors.items():
                chat_id = int(chat_id_str)
                address = info.get("address")
                last_lt = info.get("last_lt")
                if not address:
                    continue
                try:
                    txs = await get_transactions(sess, address)
                    if not txs:
                        continue
                    newest_lt = txs[0].get("in_msg", {}).get("lt") or txs[0].get("lt")
                    if not last_lt:
                        state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
                        save_state(state)
                        continue
                    new_items = [tx for tx in txs if int(tx.get("in_msg", {}).get("lt") or tx.get("lt") or 0) > int(last_lt)]
                    for tx in new_items:
                        val = int(tx.get("in_msg", {}).get("value", 0) or 0)
                        text = f"Новая транзакция!\nFrom: {tx.get('in_msg', {}).get('source','?')} To: {tx.get('in_msg', {}).get('destination','?')} Amount: {fmt_amount(val)}"
                        await bot.send_message(chat_id, text)
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
