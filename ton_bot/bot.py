import os
import asyncio
import logging
import requests

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)

API_TOKEN = os.getenv("API_TOKEN")  # БЕРЕТСЯ ТОЛЬКО ИЗ ПЕРЕМЕННОЙ
if not API_TOKEN:
    raise ValueError("API_TOKEN не установлен в переменных окружения!")

bot = Bot(API_TOKEN)
dp = Dispatcher()

# ---- ХРАНИЛКА ----
wallet_address = None
notifications_on = True
users = set()
last_tx_hashes = set()

# ---- КЛАВИАТУРА ----
def kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Баланс", callback_data="balance"),
            InlineKeyboardButton(text="История", callback_data="history")
        ],
        [
            InlineKeyboardButton(text="Вкл/Выкл уведомления", callback_data="toggle")
        ]
    ])

# ---- TON API ----
TON_API_KEY = ""   # Если нет – оставь пустым

def get_balance(addr):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={addr}&api_key={TON_API_KEY}"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            res = r["result"]
            ton = int(res.get("balance", 0)) / 1e9

            tokens = []
            if "tokens" in res:
                for t in res["tokens"]:
                    name = t.get("symbol", "TOKEN")
                    amount = int(t.get("balance", 0)) / (10 ** t.get("decimals", 9))
                    tokens.append(f"{name}: {amount}")
            return ton, tokens
    except:
        pass
    return 0, []

def get_tx(addr):
    url = f"https://toncenter.com/api/v2/getTransactions?address={addr}&limit=10&api_key={TON_API_KEY}"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            return r["result"]["transactions"]
    except:
        pass
    return []

def parse_tokens(tx):
    text = ""
    val = int(tx.get("in_msg", {}).get("value", 0)) / 1e9
    text += f"TON: {val}\n"
    for t in tx.get("token_balances", []):
        name = t.get("symbol", "TOKEN")
        amount = int(t.get("balance", 0)) / (10 ** t.get("decimals", 9))
        text += f"{name}: {amount}\n"
    return text.strip()

# ---- ФОН ПРОВЕРКА ----
async def checker():
    global last_tx_hashes
    while True:
        if wallet_address:
            txs = get_tx(wallet_address)

            for tx in txs:
                h = tx["hash"]
                if h not in last_tx_hashes:
                    last_tx_hashes.add(h)

                    if notifications_on:
                        sender = tx.get("in_msg", {}).get("source", "Unknown")
                        token_info = parse_tokens(tx)
                        msg = f"📥 Новая транзакция\nОт: {sender}\n{token_info}"

                        for u in users:
                            try:
                                await bot.send_message(u, msg)
                            except:
                                pass

        await asyncio.sleep(10)

# ---- КОМАНДЫ ----
@dp.message(Command("start"))
async def start(message: types.Message):
    users.add(message.chat.id)
    await message.answer("Бот включён. Установи кошелёк: /setwallet <адрес>", reply_markup=kb())

@dp.message(Command("setwallet"))
async def setwallet(message: types.Message):
    global wallet_address
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /setwallet <адрес>")
        return

    wallet_address = args[1]
    await message.answer(f"Кошелёк установлен: {wallet_address}")

@dp.callback_query()
async def cb(call: types.CallbackQuery):
    global notifications_on

    if not wallet_address:
        await call.message.answer("Сначала установи кошелёк: /setwallet <адрес>")
        return

    if call.data == "balance":
        ton, tokens = get_balance(wallet_address)
        msg = f"Баланс: {ton} TON"
        if tokens:
            msg += "\n" + "\n".join(tokens)
        await call.message.answer(msg)

    elif call.data == "history":
        txs = get_tx(wallet_address)
        txt = "Последние транзакции:\n\n"
        for tx in txs[:5]:
            sender = tx.get("in_msg", {}).get("source", "Unknown")
            token_info = parse_tokens(tx)
            txt += f"От: {sender}\n{token_info}\n\n"
        await call.message.answer(txt)

    elif call.data == "toggle":
        notifications_on = not notifications_on
        st = "включены" if notifications_on else "выключены"
        await call.message.answer(f"Уведомления {st}")

# ---- СТАРТ ----
async def main():
    asyncio.create_task(checker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
