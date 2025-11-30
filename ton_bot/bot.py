import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import requests

API_TOKEN = os.getenv("API_TOKEN")  # берём из переменных окружения
if not API_TOKEN:
    raise ValueError("Не задан API_TOKEN в переменных окружения!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Состояние бота
notifications_enabled = True
last_transactions = set()
wallet_address = None  # адрес по умолчанию

# Кнопки
def main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Баланс", callback_data="balance"),
        InlineKeyboardButton("История", callback_data="history"),
        InlineKeyboardButton("Вкл/Выкл уведомления", callback_data="toggle_notifications")
    )
    return keyboard

# Получаем баланс TON и токенов
def get_balance(address):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={address}&api_key=YOUR_TONCENTER_API_KEY"
    resp = requests.get(url).json()
    if resp.get("ok"):
        result = resp["result"]
        balance = int(result.get("balance", 0)) / 1e9
        tokens = result.get("tokens", [])
        token_info = []
        for t in tokens:
            symbol = t.get("name") or t.get("symbol") or "TOKEN"
            amount = int(t.get("balance", 0)) / (10 ** int(t.get("decimals", 9)))
            token_info.append(f"{symbol}: {amount}")
        return balance, token_info
    return 0, []

# Получаем последние транзакции
def get_transactions(address):
    url = f"https://toncenter.com/api/v2/getTransactions?address={address}&limit=10&api_key=YOUR_TONCENTER_API_KEY"
    resp = requests.get(url).json()
    if resp.get("ok"):
        return resp["result"]["transactions"]
    return []

# Получаем токены из транзакции
def get_tokens_from_tx(tx):
    # В TONCenter API токены могут быть в in_msg['decoded'] или 'token_balances'
    tokens_text = ""
    in_msg = tx.get("in_msg", {})
    # Основная сумма TON
    value = int(in_msg.get("value", 0)) / 1e9
    tokens_text += f"TON: {value}\n"
    # Проверка токенов
    for token in tx.get("token_balances", []):
        symbol = token.get("symbol") or token.get("name") or "TOKEN"
        amount = int(token.get("balance", 0)) / (10 ** int(token.get("decimals", 9)))
        tokens_text += f"{symbol}: {amount}\n"
    return tokens_text.strip()

# Уведомления о новых транзакциях
async def check_new_transactions():
    global last_transactions
    while True:
        if wallet_address:
            txs = get_transactions(wallet_address)
            new_txs = [tx for tx in txs if tx["hash"] not in last_transactions]
            for tx in new_txs:
                if notifications_enabled:
                    sender = tx.get("in_msg", {}).get("source", "Unknown")
                    tokens_info = get_tokens_from_tx(tx)
                    text = f"📥 Новая транзакция\nОт: {sender}\n{tokens_info}"
                    await bot.send_message(chat_id=YOUR_CHAT_ID, text=text)
                last_transactions.add(tx["hash"])
        await asyncio.sleep(15)

# Команды бота
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer(
        "Привет! Я уведомляю о новых транзакциях TON и токенов.\n"
        "Используй /setwallet чтобы установить адрес кошелька.",
        reply_markup=main_keyboard()
    )

# Установка адреса кошелька
@dp.message_handler(commands=["setwallet"])
async def set_wallet(message: types.Message):
    global wallet_address
    args = message.get_args()
    if not args:
        await message.answer("Используй команду: /setwallet <адрес_кошелька>")
        return
    wallet_address = args.strip()
    await message.answer(f"Адрес кошелька установлен: {wallet_address}")

# Обработка кнопок
@dp.callback_query_handler()
async def callbacks(call: types.CallbackQuery):
    global notifications_enabled
    if not wallet_address:
        await call.message.answer("Сначала установите адрес кошелька командой /setwallet")
        return

    if call.data == "balance":
        balance, tokens = get_balance(wallet_address)
        text = f"Баланс: {balance} TON\n"
        if tokens:
            text += "\n" + "\n".join(tokens)
        await call.message.answer(text)
    elif call.data == "history":
        txs = get_transactions(wallet_address)
        text = "Последние транзакции:\n"
        for tx in txs[:5]:
            sender = tx.get("in_msg", {}).get("source", "Unknown")
            tokens_info = get_tokens_from_tx(tx)
            text += f"От: {sender}\n{tokens_info}\n\n"
        await call.message.answer(text.strip())
    elif call.data == "toggle_notifications":
        notifications_enabled = not notifications_enabled
        state = "включены" if notifications_enabled else "выключены"
        await call.message.answer(f"Уведомления {state}.")

# Запуск бота
async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
