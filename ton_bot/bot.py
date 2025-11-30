import os
import asyncio
import logging
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

wallets = {}       # chat_id -> wallet_address
notifications = {} # chat_id -> True/False
last_tx = {}       # chat_id -> set of tx_hash

TONCENTER_API_KEY = ""  # можно оставить пустым

def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton("💰 Баланс", callback_data="balance"),
                InlineKeyboardButton("📜 История", callback_data="history")
            ],
            [
                InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="toggle")
            ]
        ]
    )

def get_balance(wallet):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={wallet}&api_key={TONCENTER_API_KEY}"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            res = r["result"]
            balance = int(res.get("balance", 0)) / 1e9
            tokens = []
            for t in res.get("tokens", []):
                sym = t.get("name") or t.get("symbol") or "TOKEN"
                amount = int(t.get("balance",0)) / (10**int(t.get("decimals",9)))
                tokens.append(f"{sym}: {amount}")
            return balance, tokens
    except:
        pass
    return 0, []

def get_transactions(wallet):
    url = f"https://toncenter.com/api/v2/getTransactions?address={wallet}&limit=10&api_key={TONCENTER_API_KEY}"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            return r["result"]["transactions"]
    except:
        pass
    return []

def tokens_from_tx(tx):
    text = ""
    in_msg = tx.get("in_msg", {})
    value = int(in_msg.get("value",0))/1e9
    text += f"TON: {value}\n"
    for t in tx.get("token_balances", []):
        sym = t.get("symbol") or t.get("name") or "TOKEN"
        amount = int(t.get("balance",0)) / (10**int(t.get("decimals",9)))
        text += f"{sym}: {amount}\n"
    return text.strip()

async def check_new_transactions():
    while True:
        for chat_id, wallet in wallets.items():
            txs = get_transactions(wallet)
            new_txs = [tx for tx in txs if tx["hash"] not in last_tx.get(chat_id,set())]
            for tx in new_txs:
                if notifications.get(chat_id, True):
                    sender = tx.get("in_msg", {}).get("source","Unknown")
                    text = f"📥 Новая транзакция\nОт: {sender}\n{tokens_from_tx(tx)}"
                    try:
                        await bot.send_message(chat_id, text)
                    except:
                        pass
                last_tx.setdefault(chat_id,set()).add(tx["hash"])
        await asyncio.sleep(10)

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    chat_id = message.chat.id
    notifications[chat_id] = True
    last_tx.setdefault(chat_id,set())
    await message.answer(
        "Бот активирован. Установите кошелек командой /setwallet <адрес_кошелька>.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("setwallet"))
async def setwallet_cmd(message: types.Message):
    chat_id = message.chat.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Используй: /setwallet <адрес_кошелька>")
        return
    wallets[chat_id] = args[1].strip()
    last_tx[chat_id] = set()
    await message.answer(f"Адрес установлен: {args[1].strip()}")

@dp.callback_query()
async def callbacks(call: types.CallbackQuery):
    chat_id = call.message.chat.id
    wallet = wallets.get(chat_id)
    if not wallet:
        await call.message.answer("Сначала установите кошелек командой /setwallet")
        return

    if call.data == "balance":
        bal, tokens = get_balance(wallet)
        text = f"Баланс: {bal} TON\n"
        if tokens:
            text += "\n" + "\n".join(tokens)
        await call.message.answer(text)
    elif call.data == "history":
        txs = get_transactions(wallet)
        text = "Последние транзакции:\n"
        for tx in txs[:5]:
            sender = tx.get("in_msg", {}).get("source","Unknown")
            text += f"От: {sender}\n{tokens_from_tx(tx)}\n\n"
        await call.message.answer(text.strip())
    elif call.data == "toggle":
        notifications[chat_id] = not notifications.get(chat_id, True)
        state = "включены" if notifications[chat_id] else "выключены"
        await call.message.answer(f"Уведомления {state}.")

async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

