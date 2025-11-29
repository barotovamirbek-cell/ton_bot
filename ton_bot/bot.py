# bot.py
import os
import asyncio
import json
import time
from typing import Optional, List

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# -------------------------
# Конфигурация через переменные окружения
# -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise SystemExit("Укажите TELEGRAM_BOT_TOKEN в системных переменных")

# Настройки
POLL_INTERVAL = 8.0  # интервал мониторинга
STORAGE_FILE = "state.json"

# -------------------------
# Хранилище состояния
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

def set_monitor(chat_id: int, address: str, last_lt: Optional[str]):
    state["chat_monitors"][str(chat_id)] = {"address": address, "last_lt": last_lt}
    save_state(state)

def clear_monitor(chat_id: int):
    if str(chat_id) in state["chat_monitors"]:
        del state["chat_monitors"][str(chat_id)]
        save_state(state)

# -------------------------
# Утилиты
# -------------------------
def fmt_amount(nano: int) -> str:
    return f"{nano / 1_000_000_000:.9f} TON".rstrip("0").rstrip(".")

def fmt_time(ts: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except:
        return str(ts)

# -------------------------
# HTTP клиент для Toncenter
# -------------------------
TONCENTER_BASE = "https://toncenter.com/api/v2"
HEADERS = {}  # если есть API_KEY, можно добавить {"X-API-Key": TON_API_KEY}

async def http_get(session: aiohttp.ClientSession, path: str, params: dict = None) -> dict:
    url = f"{TONCENTER_BASE}/{path}"
    async with session.get(url, params=params, headers=HEADERS, timeout=20) as resp:
        return await resp.json()

async def get_balance(session: aiohttp.ClientSession, address: str) -> dict:
    try:
        res = await http_get(session, "getAddressInformation", {"address": address})
        if not res.get("ok"):
            return {}
        result = res.get("result", {})
        balances = {"TON": int(result.get("balance", 0))}
        for tok in result.get("tokens", []):
            symbol = tok.get("symbol") or tok.get("name") or "UNKNOWN"
            balances[symbol] = int(tok.get("balance", 0))
        return balances
    except:
        return {}

async def get_transactions(session: aiohttp.ClientSession, address: str, limit: int = 20, to_lt: Optional[str] = None) -> List[dict]:
    params = {"address": address, "limit": limit}
    if to_lt:
        params["to_lt"] = to_lt
    try:
        res = await http_get(session, "getTransactions", params)
        return res.get("result", []) if res.get("ok") else []
    except:
        return []

def tx_summary(tx: dict, address: str) -> str:
    in_msg = tx.get("in_msg") or {}
    out_msgs = tx.get("out_msgs") or []
    summary_lines = []

    # TON
    ton_val = int(in_msg.get("value", 0) or 0)
    if ton_val:
        direction = "incoming" if in_msg.get("destination","").lower() == address.lower() else "outgoing"
        summary_lines.append(f"TON {direction}: {fmt_amount(ton_val)}")

    # Токены TIP-3
    for m in [in_msg] + out_msgs:
        for tok in m.get("token_balances", []) or []:
            sym = tok.get("symbol") or "UNKNOWN"
            val = int(tok.get("value",0))
            if val:
                summary_lines.append(f"{sym}: {val}")

    ts = tx.get("utime") or int(time.time())
    summary_lines.append(f"LT: {in_msg.get('lt') or tx.get('lt')} | {fmt_time(ts)}")
    return "\n".join(summary_lines)

# -------------------------
# Бот
# -------------------------
bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# /start
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
        reply_markup=kb.as_markup(resize_keyboard=True)
    )

# /setaddr
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
    await msg.answer(f"Адрес для этого чата установлен: <code>{addr}</code>")

# /balance
@dp.message(Command(commands=["balance"]))
async def cmd_balance(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала установите адрес с помощью /setaddr")
        return
    addr = mon["address"]
    async with aiohttp.ClientSession() as sess:
        balances = await get_balance(sess, addr)
    if not balances:
        await msg.answer("Не удалось получить баланс")
        return
    text = f"<b>Баланс для {addr}</b>\n"
    for k, v in balances.items():
        if k == "TON":
            text += f"TON: {v / 1_000_000_000:.9f}\n"
        else:
            text += f"{k}: {v}\n"
    await msg.answer(text)

# /transactions
@dp.message(Command(commands=["transactions"]))
async def cmd_transactions(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала установите адрес с помощью /setaddr")
        return
    addr = mon["address"]
    limit = 5
    parts = msg.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        limit = min(int(parts[1]), 20)
    async with aiohttp.ClientSession() as sess:
        txs = await get_transactions(sess, addr, limit=limit)
    if not txs:
        await msg.answer("Транзакций нет")
        return
    text = "\n\n".join(tx_summary(tx, addr) for tx in txs)
    await msg.answer(text)

# /monitor_start
@dp.message(Command(commands=["monitor_start"]))
async def cmd_monitor_start(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала установите адрес с помощью /setaddr")
        return
    await msg.answer("Мониторинг транзакций включен ✅")

# /monitor_stop
@dp.message(Command(commands=["monitor_stop"]))
async def cmd_monitor_stop(msg: types.Message):
    clear_monitor(msg.chat.id)
    await msg.answer("Мониторинг транзакций выключен ❌")

# -------------------------
# Background poll loop
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
                    txs = await get_transactions(sess, address, limit=20)
                    if not txs:
                        continue
                    newest_lt = txs[0].get("in_msg", {}).get("lt") or txs[0].get("lt")
                    if not last_lt:
                        state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
                        save_state(state)
                        continue
                    new_items = [tx for tx in txs if int(tx.get("in_msg", {}).get("lt") or tx.get("lt") or 0) > int(last_lt)]
                    new_items = sorted(new_items, key=lambda t: int((t.get("in_msg", {}).get("lt") or t.get("lt") or 0)))
                    for tx in new_items:
                        summary = tx_summary(tx, address)
                        await bot.send_message(chat_id, f"🔔 <b>Новая транзакция</b>\n{summary}")
                    if new_items:
                        state["chat_monitors"][chat_id_str]["last_lt"] = new_items[-1].get("in_msg", {}).get("lt") or new_items[-1].get("lt")
                        save_state(state)
                except Exception as e:
                    print("poll error for", address, e)
            await asyncio.sleep(POLL_INTERVAL)

# -------------------------
# Запуск
# -------------------------
async def main():
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
