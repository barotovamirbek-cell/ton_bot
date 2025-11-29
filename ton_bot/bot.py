import os
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.enums import ParseMode

# --- Настройки ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN не задана!")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

TONCENTER_BASE = "https://toncenter.com/api/v2"
POLL_INTERVAL = 8  # проверка новых транзакций
chat_data = {}  # chat_id -> {"address": str, "monitor": bool, "last_lt": str}


# --- Утилиты ---
async def http_get(session, path, params=None):
    url = f"{TONCENTER_BASE}/{path}"
    async with session.get(url, params=params, timeout=20) as resp:
        return await resp.json()


async def get_balance(session, address):
    try:
        res = await http_get(session, "getAddressInformation", {"address": address})
        if res.get("ok"):
            bal = res.get("result", {}).get("balance", 0)
            return int(bal)
    except:
        return 0


async def get_transactions(session, address, limit=20):
    try:
        res = await http_get(session, "getTransactions", {"address": address, "limit": limit})
        return res.get("result", []) if res.get("ok") else []
    except:
        return []


def nanotons_to_ton(nano: int) -> float:
    return nano / 1_000_000_000.0


def fmt_amount(nano: int) -> str:
    return f"{nanotons_to_ton(nano):,.9f} TON".rstrip("0").rstrip(".")


# --- Команды ---
@dp.message(Command(commands=["start"]))
async def cmd_start(msg: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="/balance")
    kb.button(text="/transactions")
    kb.button(text="/monitor_start")
    kb.button(text="/monitor_stop")
    await msg.answer(
        "Привет! Бот для отслеживания TON.\n"
        "Команды:\n"
        "/balance - показать баланс\n"
        "/transactions [N] - последние N транзакций\n"
        "/setaddr <адрес> - установить адрес для чата\n"
        "/monitor_start - уведомления о новых транзакциях\n"
        "/monitor_stop - остановить уведомления",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.as_markup(resize_keyboard=True)
    )


@dp.message(Command(commands=["setaddr"]))
async def cmd_setaddr(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Использование: /setaddr <адрес>")
        return
    chat_data[msg.chat.id] = {"address": parts[1], "monitor": False, "last_lt": None}
    await msg.answer(f"Адрес для чата установлен: <code>{parts[1]}</code>", parse_mode=ParseMode.HTML)


@dp.message(Command(commands=["balance"]))
async def cmd_balance(msg: types.Message):
    chat = chat_data.get(msg.chat.id)
    if not chat or not chat.get("address"):
        await msg.answer("Сначала установите адрес через /setaddr")
        return
    async with aiohttp.ClientSession() as sess:
        bal = await get_balance(sess, chat["address"])
    await msg.answer(f"Баланс <code>{chat['address']}</code>: {fmt_amount(bal)}", parse_mode=ParseMode.HTML)


@dp.message(Command(commands=["transactions"]))
async def cmd_transactions(msg: types.Message):
    chat = chat_data.get(msg.chat.id)
    if not chat or not chat.get("address"):
        await msg.answer("Сначала установите адрес через /setaddr")
        return
    limit = 5
    parts = msg.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        limit = int(parts[1])
    async with aiohttp.ClientSession() as sess:
        txs = await get_transactions(sess, chat["address"], limit=limit)
    if not txs:
        await msg.answer("Транзакций нет")
        return
    msgs = []
    for tx in txs:
        lt = tx.get("in_msg", {}).get("lt") or tx.get("lt")
        val = int(tx.get("in_msg", {}).get("value") or 0)
        direction = "IN" if tx.get("in_msg", {}).get("destination", "").lower() == chat["address"].lower() else "OUT"
        msgs.append(f"LT={lt} | {direction} | {fmt_amount(val)}")
    await msg.answer("\n".join(msgs))


@dp.message(Command(commands=["monitor_start"]))
async def cmd_monitor_start(msg: types.Message):
    chat = chat_data.get(msg.chat.id)
    if not chat or not chat.get("address"):
        await msg.answer("Сначала установите адрес через /setaddr")
        return
    chat["monitor"] = True
    await msg.answer("Мониторинг включен ✅")


@dp.message(Command(commands=["monitor_stop"]))
async def cmd_monitor_stop(msg: types.Message):
    chat = chat_data.get(msg.chat.id)
    if chat:
        chat["monitor"] = False
    await msg.answer("Мониторинг выключен ❌")


# --- Фоновый мониторинг ---
async def poll_loop():
    async with aiohttp.ClientSession() as sess:
        while True:
            for chat_id, data in chat_data.items():
                if not data.get("monitor") or not data.get("address"):
                    continue
                txs = await get_transactions(sess, data["address"], limit=5)
                if not txs:
                    continue
                last_lt = data.get("last_lt")
                new_txs = [tx for tx in txs if int(tx.get("in_msg", {}).get("lt") or tx.get("lt") or 0) > (int(last_lt) if last_lt else 0)]
                for tx in reversed(new_txs):
                    lt = tx.get("in_msg", {}).get("lt") or tx.get("lt")
                    val = int(tx.get("in_msg", {}).get("value") or 0)
                    direction = "IN" if tx.get("in_msg", {}).get("destination", "").lower() == data["address"].lower() else "OUT"
                    text = f"🔔 Новая транзакция!\nАдрес: <code>{data['address']}</code>\nLT={lt} | {direction} | {fmt_amount(val)}"
                    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                if txs:
                    data["last_lt"] = txs[0].get("in_msg", {}).get("lt") or txs[0].get("lt")
            await asyncio.sleep(POLL_INTERVAL)


# --- Запуск ---
async def main():
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
