# bot.py
import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import TON_API_KEY
import httpx

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ----------------------
# Хранилище кошельков и последних транзакций
wallets = {}  # user_id -> wallet address
last_txs = {}  # user_id -> set of tx_ids

# ----------------------
# Кнопки
def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
            [InlineKeyboardButton(text="📜 История", callback_data="history")]
        ]
    )

# ----------------------
# Получение баланса через TON API
async def get_wallet_balance(address):
    url = f"https://tonapi.io/v1/blockchain/getAccount?account={address}"
    headers = {"Authorization": f"Bearer {TON_API_KEY}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        balances = {}
        if "balance" in data:
            balances["TON"] = data["balance"]
        if "tokens" in data:
            for t in data["tokens"]:
                balances[t["symbol"]] = t["balance"]
        return balances

# ----------------------
# Получение последних транзакций через TON API
async def get_wallet_txs(address):
    url = f"https://tonapi.io/v1/blockchain/getTransactions?account={address}&limit=10"
    headers = {"Authorization": f"Bearer {TON_API_KEY}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("transactions", [])

# ----------------------
# Отправка уведомления
async def notify_transaction(user_id, tx):
    msg = f"Новая транзакция\n"
    if tx["from"] == wallets.get(user_id):
        msg += f"Кому: {tx['to']}\n"
    else:
        msg += f"От: {tx['from']}\n"
    msg += f"Валюта: {tx['symbol']}\n"
    msg += f"Количество: {tx['amount']}"
    await bot.send_message(user_id, msg)

# ----------------------
# Проверка новых транзакций
async def monitor_wallets():
    while True:
        for user_id, address in wallets.items():
            txs = await get_wallet_txs(address)
            if user_id not in last_txs:
                last_txs[user_id] = set(tx["id"] for tx in txs)
                continue
            for tx in txs:
                if tx["id"] not in last_txs[user_id]:
                    await notify_transaction(user_id, tx)
                    last_txs[user_id].add(tx["id"])
        await asyncio.sleep(10)  # проверяем каждые 10 секунд

# ----------------------
# Команды
@dp.message()
async def start_cmd(message: types.Message):
    await message.answer(
        "Бот активирован. Установите кошелек командой /setwallet <адрес_кошелька>.",
        reply_markup=main_keyboard()
    )

@dp.message()
async def setwallet_cmd(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Используйте: /setwallet <адрес кошелька>")
        return
    wallets[message.from_user.id] = args[1]
    last_txs[message.from_user.id] = set()  # очищаем историю для нового кошелька
    await message.answer(f"Кошелек установлен: {args[1]}")

# ----------------------
# Обработка кнопок
@dp.callback_query()
async def callbacks_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in wallets:
        await callback.message.answer("Сначала установите кошелек командой /setwallet")
        return
    address = wallets[user_id]

    if callback.data == "balance":
        balances = await get_wallet_balance(address)
        msg = "Баланс:\n"
        for sym, amount in balances.items():
            msg += f"{sym}: {amount}\n"
        await callback.message.answer(msg)
    elif callback.data == "history":
        await callback.message.answer("История транзакций пока не реализована.")

# ----------------------
# Запуск бота и мониторинга
if __name__ == "__main__":
    import asyncio
    from aiogram import F

    dp.message.register(start_cmd, F.text == "/start")
    dp.message.register(setwallet_cmd, F.text.startswith("/setwallet"))
    dp.callback_query.register(callbacks_handler)

    loop = asyncio.get_event_loop()
    loop.create_task(monitor_wallets())
    loop.run_until_complete(dp.start_polling(bot))
