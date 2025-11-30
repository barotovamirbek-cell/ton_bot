import os
import asyncio
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import config

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

users_wallets = {}  # хранение кошельков пользователей в памяти
last_tx_hash = {}   # хранение последней транзакции для уведомлений

# Кнопки
def main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💰 Баланс", callback_data="balance"),
        InlineKeyboardButton("📜 История", callback_data="history")
    )
    return kb

# Баланс
async def fetch_balance(wallet: str):
    url = f"{config.TON_API_URL}/account/balances?account={wallet}"
    headers = {"X-API-Key": config.TON_API_KEY}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        data = resp.json()
    balances = {}
    for item in data.get("balances", []):
        token_name = item.get("symbol") or "TON"
        amount = item.get("balance")
        balances[token_name] = amount
    return balances

# История транзакций
async def fetch_transactions(wallet: str):
    url = f"{config.TON_API_URL}/account/transactions?account={wallet}&limit=50"
    headers = {"X-API-Key": config.TON_API_KEY}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        data = resp.json()
    transactions = []
    for tx in data.get("transactions", []):
        transactions.append({
            "hash": tx.get("hash"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "token": tx.get("token_symbol") or "TON",
            "amount": tx.get("amount")
        })
    return transactions

# Команда /start
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer(
        "Бот активирован. Установите кошелек командой /setwallet <адрес_кошелька>.",
        reply_markup=main_keyboard()
    )

# Установка кошелька
@dp.message_handler(commands=["setwallet"])
async def set_wallet_cmd(message: types.Message):
    args = message.get_args()
    if not args:
        await message.answer("Используйте: /setwallet <адрес_кошелька>")
        return
    users_wallets[message.from_user.id] = args.strip()
    await message.answer(f"Кошелек установлен: {args.strip()}")

# Баланс
@dp.callback_query_handler(lambda c: c.data == "balance")
async def show_balance(call: types.CallbackQuery):
    wallet = users_wallets.get(call.from_user.id)
    if not wallet:
        await call.message.answer("Кошелек не установлен. Используйте /setwallet")
        return
    balances = await fetch_balance(wallet)
    text = "💰 Баланс:\n"
    for token, amount in balances.items():
        text += f"{token}: {amount}\n"
    await call.message.answer(text.strip())

# История
@dp.callback_query_handler(lambda c: c.data == "history")
async def show_history(call: types.CallbackQuery):
    wallet = users_wallets.get(call.from_user.id)
    if not wallet:
        await call.message.answer("Кошелек не установлен. Используйте /setwallet")
        return
    transactions = await fetch_transactions(wallet)
    if not transactions:
        await call.message.answer("Транзакций нет")
        return
    text = "📜 История транзакций:\n"
    for tx in transactions[:10]:
        direction = "От" if tx["to"] == wallet else "Кому"
        address = tx["from"] if direction == "От" else tx["to"]
        text += f"{direction}: {address}\n"
        text += f"Токен: {tx['token']}\n"
        text += f"Количество: {tx['amount']}\n\n"
    await call.message.answer(text.strip())

# Проверка новых транзакций
async def monitor_transactions():
    while True:
        for user_id, wallet in users_wallets.items():
            txs = await fetch_transactions(wallet)
            if not txs:
                continue
            last_hash = last_tx_hash.get(user_id)
            new_tx = txs[0]
            if new_tx["hash"] != last_hash:
                last_tx_hash[user_id] = new_tx["hash"]
                direction = "От" if new_tx["to"] == wallet else "Кому"
                address = new_tx["from"] if direction == "От" else new_tx["to"]
                text = f"💸 Новая транзакция\n{direction}: {address}\nТокен: {new_tx['token']}\nКоличество: {new_tx['amount']}"
                await bot.send_message(user_id, text)
        await asyncio.sleep(20)

# Запуск
async def on_startup(dp):
    asyncio.create_task(monitor_transactions())

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
