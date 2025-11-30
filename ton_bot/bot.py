import os
import asyncio
import logging
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config  # ключи для TonCenter/TonAPI

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

wallet_address = None
notifications_enabled = True
last_transactions = set()
users = set()

# ====== Кнопки ======
def main_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("💰 Баланс", callback_data="balance"),
         InlineKeyboardButton("📜 История", callback_data="history")],
        [InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="toggle_notifications")]
    ])
    return kb

# ====== Баланс TON + токены ======
def get_balance(wallet):
    try:
        r = requests.get(
            "https://toncenter.com/api/v2/getAddressInformation",
            params={"address": wallet, "api_key": config.TONCENTER_API_KEY}
        ).json()
        if r.get("ok"):
            bal = int(r["result"].get("balance", 0))
            tokens_list = []
            for t in r["result"].get("tokens", []):
                symbol = t.get("symbol") or t.get("name") or "TOKEN"
                amount = int(t.get("balance", 0)) / (10 ** int(t.get("decimals", 9)))
                tokens_list.append(f"{symbol}: {amount}")
            return bal / 1e9, tokens_list
    except:
        pass
    return 0, []

# ====== История транзакций ======
def get_transactions(wallet):
    try:
        r = requests.get(
            "https://toncenter.com/api/v2/getTransactions",
            params={"address": wallet, "limit": 10, "api_key": config.TONCENTER_API_KEY}
        ).json()
        if r.get("ok"):
            return r["result"].get("transactions", [])
    except:
        pass
    return []

# ====== Форматирование транзакции ======
def format_transaction(tx):
    text_lines = []

    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", [])
    token_balances = tx.get("token_balances", [])

    # Определяем направление
    if in_msg.get("destination") == wallet_address:
        sender = in_msg.get("source", "Unknown")
        value = int(in_msg.get("value", 0)) / 1e9
        text_lines.append(f"От: {sender}")
        text_lines.append(f"Валюта: TON")
        text_lines.append(f"Количество: {value}")
    else:
        dest = out_msgs[0].get("destination") if out_msgs else "Unknown"
        value = int(in_msg.get("value", 0)) / 1e9
        text_lines.append(f"Кому: {dest}")
        text_lines.append(f"Валюта: TON")
        text_lines.append(f"Количество: {value}")

    # Добавляем токены, если есть
    for token in token_balances:
        symbol = token.get("symbol") or token.get("name") or "TOKEN"
        amount = int(token.get("balance", 0)) / (10 ** int(token.get("decimals", 9)))
        text_lines.append(f"Валюта: {symbol}")
        text_lines.append(f"Количество: {amount}")

    return "\n".join(text_lines)

# ====== Проверка новых транзакций ======
async def check_new_transactions():
    global last_transactions
    while True:
        if wallet_address:
            txs = get_transactions(wallet_address)
            new_txs = [tx for tx in txs if tx["hash"] not in last_transactions]
            for tx in new_txs:
                if notifications_enabled:
                    text = f"📥 Новая транзакция\n{format_transaction(tx)}"
                    for uid in users:
                        try:
                            await bot.send_message(uid, text)
                        except:
                            pass
                last_transactions.add(tx["hash"])
        await asyncio.sleep(10)

# ====== Хендлеры ======
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    users.add(message.chat.id)
    await message.answer(
        "Бот активирован. Установите кошелек командой /setwallet <адрес_кошелька>.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("setwallet"))
async def setwallet_cmd(message: types.Message):
    global wallet_address
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Используй команду: /setwallet <адрес_кошелька>")
        return
    wallet_address = args[1].strip()
    last_transactions.clear()
    await message.answer(f"Адрес кошелька установлен: {wallet_address}")

@dp.callback_query()
async def callbacks(call: types.CallbackQuery):
    global notifications_enabled
    if not wallet_address:
        await call.message.answer("Сначала установите адрес кошелька командой /setwallet")
        return

    if call.data == "balance":
        bal, tokens = get_balance(wallet_address)
        text = f"Баланс: {bal} TON"
        if tokens:
            text += "\n" + "\n".join(tokens)
        await call.message.answer(text)
    elif call.data == "history":
        txs = get_transactions(wallet_address)
        if not txs:
            await call.message.answer("Транзакций пока нет.")
            return
        text = "Последние транзакции:\n"
        for tx in txs[:5]:
            text += f"{format_transaction(tx)}\n---\n"
        await call.message.answer(text)
    elif call.data == "toggle_notifications":
        notifications_enabled = not notifications_enabled
        state = "включены" if notifications_enabled else "выключены"
        await call.message.answer(f"Уведомления {state}.")

# ====== Запуск ======
async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
