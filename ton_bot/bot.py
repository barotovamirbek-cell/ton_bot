import asyncio
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

API_TOKEN = os.getenv("API_TOKEN")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Храним данные: user_id → wallet
user_wallets = {}

def keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        types.KeyboardButton("💰 Баланс"),
        types.KeyboardButton("📜 История")
    )
    kb.add(types.KeyboardButton("🔄 Сменить адрес"))
    return kb


async def get_wallet_balance(address):
    try:
        url = f"https://tonapi.io/v2/accounts/{address}"
        r = requests.get(url).json()
        balance = r.get("balance", 0) / 1e9
        tokens = r.get("jettons", [])
        txt = f"💰 TON: {balance}\n"
        for t in tokens:
            name = t["jetton"]["name"]
            amt = float(t["balance"]) / (10 ** t["jetton"]["decimals"])
            txt += f"🪙 {name}: {amt}\n"
        return txt
    except:
        return "Ошибка при получении баланса."


async def get_wallet_history(address):
    try:
        url = f"https://tonapi.io/v2/accounts/{address}/transactions"
        r = requests.get(url).json()
        txs = r.get("transactions", [])
        if not txs:
            return "История пустая."

        txt = "📜 Последние транзакции:\n\n"
        for tx in txs[:5]:
            amt = tx.get("in_msg", {}).get("value", 0) / 1e9
            txt += f"👉 {amt} TON\n"

        return txt
    except:
        return "Ошибка при получении истории."


@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь TON адрес для отслеживания.",
        reply_markup=keyboard()
    )


@dp.message()
async def handler(message: types.Message):
    text = message.text
    uid = message.from_user.id

    # кнопка смены
    if text == "🔄 Сменить адрес":
        user_wallets.pop(uid, None)
        return await message.answer("Введите новый TON адрес:")

    # баланс
    if text == "💰 Баланс":
        if uid not in user_wallets:
            return await message.answer("Сначала отправьте адрес.")
        return await message.answer(await get_wallet_balance(user_wallets[uid]))

    # история
    if text == "📜 История":
        if uid not in user_wallets:
            return await message.answer("Сначала отправьте адрес.")
        return await message.answer(await get_wallet_history(user_wallets[uid]))

    # если это адрес
    if text.startswith("UQ") or text.startswith("EQ"):
        user_wallets[uid] = text
        return await message.answer("Адрес сохранён! Теперь вы можете смотреть баланс и историю.")

    await message.answer("Я не понял. Отправьте адрес или используйте кнопки.")


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
