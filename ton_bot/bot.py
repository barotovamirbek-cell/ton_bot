import asyncio
import time
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ParseMode

# ------------------- Вставь свои данные сюда -------------------
TELEGRAM_TOKEN = 8454402175:AAFzsZAiv5vAAIJalByXXVxi8Wf2WDMGvZI
TON_API_KEY = 
DEFAULT_ADDRESS = UQDBu4MwbignVmSAd9Io5o99Db9d5d9jF9nNFj8Zsik7XKgF
POLL_INTERVAL = 8  # интервал проверки в секундах
# --------------------------------------------------------------

TONCENTER_BASE = "https://toncenter.com/api/v2"
HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
state = {"chat_monitors": {}}

# ------------------- HTTP helpers -------------------
async def http_get(session, path, params=None):
    url = f"{TONCENTER_BASE}/{path}"
    async with session.get(url, params=params, headers=HEADERS, timeout=20) as resp:
        return await resp.json()

async def get_balance(session, address: str):
    try:
        res = await http_get(session, "getAddressInformation", {"address": address})
        if res.get("ok"):
            return int(res["result"].get("balance", 0))
    except:
        return None

async def get_transactions(session, address: str, limit=20, to_lt=None):
    params = {"address": address, "limit": limit}
    if to_lt:
        params["to_lt"] = to_lt
    try:
        res = await http_get(session, "getTransactions", params)
        if res.get("ok"):
            return res.get("result", [])
    except:
        return []
    return []

def fmt_amount(nano):
    return f"{nano/1_000_000_000:.9f} TON".rstrip("0").rstrip(".")

def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

def analyze_transaction(tx, address):
    incoming = 0
    outgoing = 0
    in_msg = tx.get("in_msg") or {}
    out_msgs = tx.get("out_msgs") or []

    # входящие
    if in_msg.get("destination", "").lower() == address.lower():
        incoming += int(in_msg.get("value", 0) or 0)
    if in_msg.get("source", "").lower() == address.lower():
        outgoing += int(in_msg.get("value", 0) or 0)

    # исходящие
    for m in out_msgs:
        if m.get("destination", "").lower() == address.lower():
            incoming += int(m.get("value", 0) or 0)
        if m.get("source", "").lower() == address.lower():
            outgoing += int(m.get("value", 0) or 0)

    net = incoming - outgoing
    direction = "incoming" if net > 0 else ("outgoing" if net < 0 else "self/none")
    return net, direction

def tx_summary(tx, address):
    lt = tx.get("in_msg", {}).get("lt") or tx.get("lt")
    utime = tx.get("utime") or int(time.time())
    net, direction = analyze_transaction(tx, address)
    return f"LT={lt} | {fmt_time(utime)} | {direction.upper()} | {fmt_amount(abs(net))}"

# ------------------- Telegram commands -------------------
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("/balance", "/transactions", "/monitor_start", "/monitor_stop")
    await msg.answer(
        "Привет! Я бот для отслеживания TON.\n"
        "Команды:\n"
        "/balance\n/transactions [N]\n/setaddr <address>\n/monitor_start\n/monitor_stop",
        reply_markup=kb
    )

@dp.message(Command("balance"))
async def cmd_balance(msg: types.Message):
    monitor = state["chat_monitors"].get(str(msg.chat.id))
    address = monitor["address"] if monitor else DEFAULT_ADDRESS
    async with aiohttp.ClientSession() as sess:
        bal = await get_balance(sess, address)
    if bal is None:
        await msg.answer("Не удалось получить баланс.")
        return
    await msg.answer(f"Адрес: <code>{address}</code>\nБаланс: <b>{fmt_amount(bal)}</b>")

@dp.message(Command("transactions"))
async def cmd_transactions(msg: types.Message):
    parts = msg.text.split()
    n = int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 10
    monitor = state["chat_monitors"].get(str(msg.chat.id))
    address = monitor["address"] if monitor else DEFAULT_ADDRESS
    async with aiohttp.ClientSession() as sess:
        txs = await get_transactions(sess, address, limit=n)
    if not txs:
        await msg.answer("Транзакций нет.")
        return
    texts = [tx_summary(tx, address) for tx in txs]
    await msg.answer("\n".join(texts))

@dp.message(Command("setaddr"))
async def cmd_setaddr(msg: types.Message):
    parts = msg.text.split()
    if len(parts)<2:
        await msg.answer("Использование: /setaddr <address>")
        return
    addr = parts[1].strip()
    state["chat_monitors"][str(msg.chat.id)] = {"address": addr, "last_lt": None}
    await msg.answer(f"Адрес установлен: <code>{addr}</code>")

@dp.message(Command("monitor_start"))
async def cmd_monitor_start(msg: types.Message):
    monitor = state["chat_monitors"].get(str(msg.chat.id))
    address = monitor["address"] if monitor else DEFAULT_ADDRESS
    async with aiohttp.ClientSession() as sess:
        txs = await get_transactions(sess, address, limit=1)
    last_lt = txs[0].get("in_msg", {}).get("lt") if txs else None
    if not monitor:
        state["chat_monitors"][str(msg.chat.id)] = {"address": address, "last_lt": last_lt}
    else:
        state["chat_monitors"][str(msg.chat.id)]["last_lt"] = last_lt
    await msg.answer(f"Мониторинг включен для {address}")

@dp.message(Command("monitor_stop"))
async def cmd_monitor_stop(msg: types.Message):
    state["chat_monitors"].pop(str(msg.chat.id), None)
    await msg.answer("Мониторинг остановлен.")

# ------------------- Background polling -------------------
async def poll_loop():
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as sess:
        while True:
            for chat_id_str, info in state["chat_monitors"].items():
                chat_id = int(chat_id_str)
                address = info["address"]
                last_lt = info.get("last_lt")
                txs = await get_transactions(sess, address, limit=20)
                new_items = []
                for tx in txs:
                    lt = tx.get("in_msg", {}).get("lt") or tx.get("lt")
                    if not lt:
                        continue
                    if not last_lt or int(lt) > int(last_lt):
                        new_items.append(tx)
                new_items.sort(key=lambda t: int(t.get("in_msg", {}).get("lt") or t.get("lt") or 0))
                for tx in new_items:
                    summary = tx_summary(tx, address)
                    in_msg = tx.get("in_msg") or {}
                    src = in_msg.get("source") or "?"
                    dst = in_msg.get("destination") or "?"
                    await bot.send_message(chat_id, f"🔔 <b>Новая транзакция</b>\nАдрес: <code>{address}</code>\n{summary}\nFrom: <code>{src}</code>\nTo: <code>{dst}</code>")
                if new_items:
                    newest_lt = new_items[-1].get("in_msg", {}).get("lt") or new_items[-1].get("lt")
                    state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
            await asyncio.sleep(POLL_INTERVAL)

# ------------------- Main -------------------
async def main():
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
