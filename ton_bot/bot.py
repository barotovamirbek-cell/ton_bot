import os
import asyncio
import logging
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)

# ===== Переменная токена бота =====
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ===== Состояние =====
wallets = {}  # user_id -> wallet_address
last_txs = {}  # user_id -> set(hash)
notifications_enabled = {}  # user_id -> bool
history_store = {}  # user_id -> list последних tx

# ===== Кнопки =====
def main_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("💰 Баланс", callback_data="balance"),
            InlineKeyboardButton("📜 История", callback_data="history")
        ],
        [
            InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="toggle_notifications")
        ]
    ])
    return kb

# ===== TON API =====
TONCENTER_API_KEY = ""  # вставь свой ключ, если есть

def get_balance(address):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={address}&api_key={TONCENTER_API_KEY}"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            result = r["result"]
            balance = int(result.get("balance", 0)) / 1e9
            tokens = []
            for t in result.get("tokens", []):
                symbol = t.get("name") or t.get("symbol") or "TOKEN"
                amount = int(t.get("balance", 0)) / (10 ** int(t.get("decimals", 9)))
                tokens.append(f"{symbol}: {amount}")
            return balance, tokens
    except:
        pass
    return 0, []

def get_transactions(address):
    url = f"https://toncenter.com/api/v2/getTransactions?address={address}&limit=10&api_key={TONCENTER_API_KEY}"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            return r["result"]["transactions"]
    except:
        pass
    return []

def format_tx(tx):
    in_msg = tx.get("in_msg", {})
    out_msg = tx.get("out_msgs", [{}])[0]

    is_incoming = int(in_msg.get("value", 0)) > 0
    if is_incoming:
        direction = "От"
        counterparty = in_msg.get("source", "Unknown")
        amount_ton = int(in_msg.get("value", 0)) / 1e9
    else:
        direction = "Кому"
        counterparty = out_msg.get("destination", "Unknown")
        amount_ton = int(out_msg.get("value", 0)) / 1e9

    # Токены
    token_lines = []
    for tok in tx.get("token_balances", []):
        symbol = tok.get("symbol") or tok.get("name") or "TOKEN"
        amount = int(tok.get("balance", 0)) / (10 ** int(tok.get("decimals", 9)))
        token_lines.append(f"{symbol}: {amount}")

    token_text = "\n".join(token_lines) if token_lines else "TON"
    text_amount = f"{amount_ton}" if token_text == "TON" else ""

    text = (
        f"📥 Новая транзакция\n"
        f"{direction}: {counterparty}\n"
        f"Валюта: {token_text}\n"
        f"Количество: {text_amount}"
    )
    return text

# ===== Проверка новых транзакций =====
async def check_new_transactions():
    while True:
        for user_id, wallet in wallets.items():
            txs = get_transactions(wallet)
            if user_id not in last_txs:
                last_txs[user_id] = set()
            if user_id not in history_store:
                history_store[user_id] = []

            new_txs = [tx for tx in txs if tx["hash"] not in last_txs[user_id]]
            for tx in new_txs:
                # Добавляем в историю
                history_store[user_id].insert(0, tx)
                if len(history_store[user_id]) > 20:  # храним максимум 20 последних
                    history_store[user_id].pop()

                # Уведомление
                if notifications_enabled.get(user_id, True):
                    try:
                        await bot.send_message(user_id, format_tx(tx))
                    except:
                        pass
                last_txs[user_id].add(tx["hash"])
        await asyncio.sleep(10)

# ===== Хендлеры =====
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.chat.id
    if user_id not in notifications_enabled:
        notifications_enabled[user_id] = True
    await message.answer(
        "Бот активирован. Установите кошелек командой /setwallet <адрес_кошелька>.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("setwallet"))
async def setwallet_cmd(message: types.Message):
    user_id = message.chat.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Используй команду: /setwallet <адрес_кошелька>")
        return
    wallet_address = args[1].strip()
    wallets[user_id] = wallet_address
    last_txs[user_id] = set()
    history_store[user_id] = []
    await message.answer(f"Адрес кошелька установлен: {wallet_address}")

@dp.callback_query()
async def callbacks(call: types.CallbackQuery):
    user_id = call.message.chat.id
    if user_id not in wallets:
        await call.message.answer("Сначала установите адрес кошелька командой /setwallet")
        return
    wallet = wallets[user_id]

    if call.data == "balance":
        bal, tokens = get_balance(wallet)
        text = f"Баланс: {bal} TON"
        if tokens:
            text += "\n" + "\n".join(tokens)
        await call.message.answer(text)

    elif call.data == "history":
        txs = history_store.get(user_id, [])
        if not txs:
            await call.message.answer("История транзакций пуста.")
            return
        text = "Последние транзакции:\n"
        for tx in txs[:5]:
            text += format_tx(tx) + "\n\n"
        await call.message.answer(text.strip())

    elif call.data == "toggle_notifications":
        notifications_enabled[user_id] = not notifications_enabled.get(user_id, True)
        state = "включены" if notifications_enabled[user_id] else "выключены"
        await call.message.answer(f"Уведомления {state}.")

# ===== Запуск =====
async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
