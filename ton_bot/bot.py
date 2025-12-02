import os
import asyncio
import requests
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command

import config

# =======================
#  НАСТРОЙКИ
# =======================

bot = Bot(
    token=config.BOT_TOKEN,
    timeout=30  # увеличенный таймаут (фикс TelegramNetworkError)
)

dp = Dispatcher()

# Сохраняем кошельки пользователей
user_wallets: Dict[int, str] = {}

# Сохраняем последние хэши транзакций для каждого пользователя
user_last_tx: Dict[int, str] = {}

# История транзакций
user_history: Dict[int, List[str]] = {}


# =======================
#  ФУНКЦИЯ ЗАПРОСА ТРАНЗАКЦИЙ
# =======================

def get_transactions(wallet: str):
    url = f"https://toncenter.com/api/v3/addressTransactions?address={wallet}&limit=20"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None


# =======================
#  КОМАНДЫ
# =======================

@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    await msg.answer(
        "👋 Бот запущен!\n\n"
        "Команды:\n"
        "/setwallet — установить кошелек\n"
        "/wallet — показать текущий\n"
        "/history — история транзакций\n"
        "/check — проверить вручную\n"
    )


@dp.message(Command("setwallet"))
async def setwallet_cmd(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        return await msg.answer("⚠ Введите кошелек: /setwallet <адрес>")

    wallet = parts[1].strip()
    user_wallets[msg.from_user.id] = wallet
    user_last_tx[msg.from_user.id] = ""
    user_history[msg.from_user.id] = []

    await msg.answer(f"✅ Кошелек обновлён!\n\n<b>{wallet}</b>")


@dp.message(Command("wallet"))
async def show_wallet(msg: types.Message):
    w = user_wallets.get(msg.from_user.id)
    if not w:
        return await msg.answer("⚠ Кошелек не установлен.\nИспользуй: /setwallet <адрес>")
    await msg.answer(f"🔑 Твой кошелек:\n<b>{w}</b>")


@dp.message(Command("history"))
async def history_cmd(msg: types.Message):
    h = user_history.get(msg.from_user.id, [])
    if not h:
        return await msg.answer("📭 История пуста.")

    text = "📜 <b>История транзакций</b>:\n\n" + "\n".join(h[-20:])
    await msg.answer(text)


@dp.message(Command("check"))
async def manual_check(msg: types.Message):
    await check_user(msg.from_user.id)
    await msg.answer("🔍 Проверка выполнена!")


# =======================
#  ПРОВЕРКА ТРАНЗАКЦИЙ
# =======================

async def check_user(user_id: int):
    wallet = user_wallets.get(user_id)
    if not wallet:
        return

    data = get_transactions(wallet)
    if not data or "transactions" not in data:
        return

    txs = data["transactions"]
    if not txs:
        return

    last = user_last_tx.get(user_id)

    for tx in reversed(txs):  # от старых → к новым
        tx_hash = tx.get("hash")

        if tx_hash == last:
            continue

        user_last_tx[user_id] = tx_hash

        value = tx.get("value", 0)
        value_ton = value / 1_000_000_000

        from_addr = tx.get("from", "unknown")
        to_addr = tx.get("to", "unknown")

        # определяем входящая/исходящая
        if to_addr.lower() == wallet.lower():
            direction = "🟢 Входящая"
        else:
            direction = "🔴 Исходящая"

        text = (
            f"{direction} транзакция\n"
            f"💎 Сумма: <b>{value_ton} TON</b>\n"
            f"➡ From: <code>{from_addr}</code>\n"
            f"⬅ To: <code>{to_addr}</code>\n"
            f"🆔 Hash: <code>{tx_hash}</code>"
        )

        # сохраняем в историю
        user_history[user_id].append(text)

        # отправляем пользователю
        try:
            await bot.send_message(user_id, text)
        except:
            pass


# =======================
#  ЦИКЛ ФОНОВОЙ ПРОВЕРКИ
# =======================

async def background_checker():
    while True:
        for user_id in list(user_wallets.keys()):
            await check_user(user_id)

        await asyncio.sleep(10)  # проверка каждые 10 сек


# =======================
#  ЗАПУСК
# =======================

async def main():
    asyncio.create_task(background_checker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
