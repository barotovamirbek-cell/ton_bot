import asyncio
import aiohttp
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# ------------------------
# Конфигурация
# ------------------------
TOKEN = "ВАШ_ТОКЕН_ТЕЛЕГРАМ"  # вставь токен сюда
TON_API_KEY = ""  # если есть ключ TonCenter
POLL_INTERVAL = 8.0
STORAGE_FILE = "state.json"

import json
try:
    with open(STORAGE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
except:
    state = {"chat_monitors": {}}

def save_state():
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ------------------------
# HTTP для TonCenter
# ------------------------
TONCENTER_BASE = "https://toncenter.com/api/v2"
HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

async def http_get(session, path, params=None):
    url = f"{TONCENTER_BASE}/{path}"
    async with session.get(url, params=params, headers=HEADERS, timeout=20) as resp:
        return await resp.json()

async def get_balance(session, address):
    try:
        res = await http_get(session, "getAddressInformation", {"address": address})
        if res.get("ok"):
            b = res.get("result", {}).get("balance")
            return int(b) if b else 0
    except:
        return 0

async def get_transactions(session, address, limit=20, to_lt=None):
    params = {"address": address, "limit": limit}
    if to_lt:
        params["to_lt"] = to_lt
    try:
        res = await http_get(session, "getTransactions", params)
        return res.get("result", []) if res.get("ok") else []
    except:
        return []

def fmt_amount(nano):
    return f"{nano/1_000_000_000:.9f} TON".rstrip("0").rstrip(".")

# ------------------------
# Мониторинг
# ------------------------
def get_monitor(chat_id):
    return state["chat_monitors"].get(str(chat_id))

def set_monitor(chat_id, address, last_lt=None):
    state["chat_monitors"][str(chat_id)] = {"address": address, "last_lt": last_lt}
    save_state()

# ------------------------
# Бот
# ------------------------
bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="/balance")
    kb.button(text="/transactions")
    kb.button(text="/monitor_start")
    kb.button(text="/monitor_stop")
    await msg.answer(
        "Привет! Я бот для отслеживания TON.\n\n"
        "Команды:\n"
        "/balance - баланс\n"
        "/transactions [N] - последние N транзакций\n"
        "/setaddr <address> - установить адрес\n"
        "/monitor_start - уведомления\n"
        "/monitor_stop - отключить уведомления",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )

@dp.message(Command("setaddr"))
async def cmd_setaddr(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Использование: /setaddr <TON address>")
        return
    addr = parts[1].strip()
    mon = get_monitor(msg.chat.id)
    last_lt = mon["last_lt"] if mon else None
    set_monitor(msg.chat.id, addr, last_lt)
    await msg.answer(f"Адрес установлен: {addr}")

# ------------------------
# Poll loop
# ------------------------
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
                        save_state()
                        continue
                    new_items = [tx for tx in txs if int(tx.get("in_msg", {}).get("lt") or tx.get("lt") or 0) > int(last_lt)]
                    for tx in new_items:
                        await bot.send_message(chat_id, f"Новая транзакция:\nLT={tx.get('in_msg', {}).get('lt') or tx.get('lt')}")
                    if new_items:
                        state["chat_monitors"][chat_id_str]["last_lt"] = new_items[-1].get("in_msg", {}).get("lt") or new_items[-1].get("lt")
                        save_state()
                except Exception as e:
                    print("poll error:", e)
            await asyncio.sleep(POLL_INTERVAL)

# ------------------------
# Запуск
# ------------------------
async def main():
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
