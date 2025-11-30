from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
import os
import asyncio
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ================== Данные ==================
wallets = {}  # chat_id: wallet_address
notifications_enabled = {}  # chat_id: True/False
last_tx = {}  # chat_id: set(tx_hash)

# ================== Клавиатура ==================
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📜 История")],
            [KeyboardButton(text="🔔 Вкл/Выкл уведомления")]
        ],
        resize_keyboard=True
    )

# ================== TON ==================
TONCENTER_API_KEY = "ВАШ_ТОНЦЕНТЕР_API_КЛЮЧ"

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

def get_tokens_from_tx(tx):
    text = ""
    in_msg = tx.get("in_msg", {})
    value = int(in_msg.get("value",0))/1e9
    text += f"TON: {value}\n"
    for token in tx.get("token_balances", []):
        symbol = token.get("symbol") or token.get("name") or "TOKEN"
        amount = int(token.get("balance",0)) / (10**int(token.get("decimals",9)))
        text += f"{symbol}: {amount}\n"
    return text.strip()

# ================== Проверка новых транзакций ==================
async def check_new_transactions():
    while True:
        for chat_id, addr in wallets.items():
            txs = get_transactions(addr)
            if chat_id not in last_tx:
                last_tx[chat_id] = set()
            new_txs = [tx for tx in txs if tx["hash"] not in last_tx[chat_id]]
            for tx in new_txs:
                if notifications_enabled.get(chat_id, True):
                    sender = tx.get("in_msg", {}).get("source", "Unknown")
                    tokens_info = get_tokens_from_tx(tx)
                    text = f"📥 Новая транзакция\nОт: {sender}\n{tokens_info}"
                    try:
                        await bot.send_message(chat_id, text)
                    except:
                        pass
                last_tx[chat_id].add(tx["hash"])
        await asyncio.sleep(10)

# ================== Хендлеры ==================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    chat_id = message.chat.id
    wallets.pop(chat_id, None)
    notifications_enabled[chat_id] = True
    last_tx[chat_id] = set()
    await message.answer("Бот активирован. Установите кошелек командой /setwallet <адрес_кошелька>", reply_markup=main_keyboard())

@dp.message(Command("setwallet"))
async def cmd_setwallet(message: types.Message):
    args = message.get_args()
    if not args:
        await message.answer("Используй: /setwallet <адрес_кошелька>")
        return
    wallets[message.chat.id] = args.strip()
    last_tx[message.chat.id] = set()
    await message.answer(f"Адрес установлен: {args.strip()}")

@dp.message()
async def handle_buttons(message: types.Message):
    chat_id = message.chat.id
    addr = wallets.get(chat_id)
    if not addr:
        await message.answer("Сначала установите адрес кошелька командой /setwallet")
        return

    if message.text == "💰 Баланс":
        bal, tokens = get_balance(addr)
        text = f"Баланс: {bal} TON\n"
        if tokens:
            text += "\n".join(tokens)
        await message.answer(text)

    elif message.text == "📜 История":
        txs = get_transactions(addr)
        text = ""
        for tx in txs[:5]:
            sender = tx.get("in_msg", {}).get("source", "Unknown")
            tokens_info = get_tokens_from_tx(tx)
            text += f"От: {sender}\n{tokens_info}\n\n"
        if not text:
            text = "Транзакций нет"
        await message.answer(text.strip())

    elif message.text == "🔔 Вкл/Выкл уведомления":
        notifications_enabled[chat_id] = not notifications_enabled.get(chat_id, True)
        state = "включены" if notifications_enabled[chat_id] else "выключены"
        await message.answer(f"Уведомления {state}")

# ================== Запуск ==================
async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
