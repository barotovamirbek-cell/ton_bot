import os
import asyncio
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

# ==========================
#   SECURITY FIX — HTML ESCAPE
# ==========================
def escape_html(text: str) -> str:
    if not isinstance(text, str):
        return text
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

# ==========================
#   CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Храним включение/выключение мониторинга
monitoring_enabled = {}

# ==========================
#   TON API — баланс
# ==========================
async def get_balance(address):
    url = f"https://toncenter.com/api/v2/getAddressBalance?address={address}&api_key={TONCENTER_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        balance = int(data.get("result", 0)) / 1e9
        return balance
    except:
        return None


# ==========================
#   TON API — токены (Jettons)
# ==========================
async def get_tokens(address):
    url = f"https://toncenter.com/api/v3/jetton/getBalances?account={address}&api_key={TONCENTER_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        out = []
        for t in data.get("balances", []):
            jetton = t.get("jetton", {})
            metadata = jetton.get("metadata", {})
            name = metadata.get("name", "Unknown")
            symbol = metadata.get("symbol", "???")
            decimals = int(metadata.get("decimals", 9))
            balance = int(t.get("balance", 0)) / (10 ** decimals)

            out.append(f"{name} ({symbol}) — {balance}")

        return out
    except:
        return ["Ошибка получения токенов"]


# ==========================
#   TON API — транзакции (фикс)
# ==========================
async def get_transactions(address, limit=10):
    url = f"https://toncenter.com/api/v2/getTransactions?address={address}&limit={limit}&api_key={TONCENTER_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

        txs = data.get("result", [])
        parsed = []

        for tx in txs:
            lt = tx.get("transaction_id", {}).get("lt", "N/A")
            ts = tx.get("utime", 0)
            dt_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

            in_msg = tx.get("in_msg")
            out_msgs = tx.get("out_msgs", [])

            sender = "Unknown"
            receiver = "Unknown"
            amount = 0

            if in_msg:
                sender = escape_html(in_msg.get("source", "Unknown"))
                amount = int(in_msg.get("value", 0)) / 1e9

            if out_msgs:
                receiver = escape_html(out_msgs[0].get("destination", "Unknown"))
                amount = int(out_msgs[0].get("value", 0)) / 1e9

            parsed.append(
                f"LT={lt} | {dt_str} | {sender} → {receiver} | {amount:.6f} TON"
            )

        return parsed

    except Exception as e:
        return [f"Ошибка истории: {e}"]


# ==========================
#   /start
# ==========================
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    monitoring_enabled[msg.from_user.id] = False

    await msg.answer(
        "👋 Бот активирован!\n\n"
        "Команды:\n"
        "/start — включить бота\n"
        "/stop — выключить бота\n"
        "/balance <адрес>\n"
        "/tokens <адрес>\n"
        "/history <адрес>\n"
        "/monitor_on <адрес>\n"
        "/monitor_off\n"
    )


# ==========================
#   /stop
# ==========================
@dp.message(Command("stop"))
async def cmd_stop(msg: Message):
    monitoring_enabled[msg.from_user.id] = False
    await msg.answer("🔴 Бот выключен.")


# ==========================
#   /balance
# ==========================
@dp.message(Command("balance"))
async def cmd_balance(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /balance <TON адрес>")

    address = args[1]
    balance = await get_balance(address)

    if balance is None:
        return await msg.answer("Ошибка получения баланса.")

    await msg.answer(f"💰 Баланс: {balance} TON")


# ==========================
#   /tokens
# ==========================
@dp.message(Command("tokens"))
async def cmd_tokens(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /tokens <TON адрес>")

    address = args[1]
    tokens = await get_tokens(address)

    await msg.answer("🪙 Токены:\n" + "\n".join(tokens))


# ==========================
#   /history
# ==========================
@dp.message(Command("history"))
async def cmd_history(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /history <TON адрес>")

    address = args[1]
    txs = await get_transactions(address)

    await msg.answer("📜 Последние транзакции:\n\n" + "\n".join(txs))


# ==========================
#   Мониторинг (вкл)
# ==========================
@dp.message(Command("monitor_on"))
async def cmd_monitor_on(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /monitor_on <TON адрес>")

    user = msg.from_user.id
    address = args[1]

    monitoring_enabled[user] = True
    await msg.answer(f"🟢 Мониторинг включен для:\n{address}")

    asyncio.create_task(monitor_loop(msg, address))


# ==========================
#   Мониторинг (выкл)
# ==========================
@dp.message(Command("monitor_off"))
async def cmd_monitor_off(msg: Message):
    monitoring_enabled[msg.from_user.id] = False
    await msg.answer("🔴 Мониторинг отключен.")


# ==========================
#   Основной цикл мониторинга
# ==========================
async def monitor_loop(msg: Message, address: str):
    user = msg.from_user.id

    last_lt = None

    while monitoring_enabled.get(user, False):
        txs = await get_transactions(address, limit=1)

        if txs and "LT=" in txs[0]:
            lt_new = txs[0].split(" | ")[0].replace("LT=", "")
            if last_lt is None:
                last_lt = lt_new

            elif lt_new != last_lt:
                last_lt = lt_new
                await msg.answer("🆕 Новая транзакция:\n" + txs[0])

        await asyncio.sleep(5)


# ==========================
#   RUN
# ==========================
if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
