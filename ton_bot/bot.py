import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import config
import aiohttp

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

wallets = {}      # Словарь: user_id -> wallet
histories = {}    # Словарь: user_id -> список транзакций

# Клавиатура
def main_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("📜 История", callback_data="history")]
    ])
    return kb

# Команда старт
@dp.message()
async def start_cmd(message: types.Message):
    await message.answer(
        "Бот активирован.\nУстановите кошелек командой:\n/setwallet <адрес_кошелька>",
        reply_markup=main_keyboard()
    )

# Установка кошелька
@dp.message()
async def set_wallet(message: types.Message):
    if message.text.startswith("/setwallet"):
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("Используйте: /setwallet <адрес>")
            return
        wallet_address = args[1]
        wallets[message.from_user.id] = wallet_address
        histories[message.from_user.id] = []  # сбрасываем историю
        await message.answer(f"Кошелек установлен: {wallet_address}")

# Колбэк кнопок
@dp.callback_query()
async def callbacks(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    wallet = wallets.get(user_id)
    if not wallet:
        await callback.message.answer("Сначала установите кошелек /setwallet <адрес>")
        return

    if callback.data == "balance":
        bal_text = await get_balance(wallet)
        await callback.message.answer(bal_text)
    elif callback.data == "history":
        history_list = histories.get(user_id, [])
        if not history_list:
            await callback.message.answer("История пуста")
        else:
            await callback.message.answer("\n\n".join(history_list))

# Получаем баланс всех токенов
async def get_balance(wallet):
    async with aiohttp.ClientSession() as session:
        headers = {"X-API-Key": config.TON_API_KEY}
        url = f"https://tonapi.io/v1/wallets/{wallet}/tokens"
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()

    lines = []
    for token in data.get("tokens", []):
        name = token.get("name", "TON")
        amount = token.get("balance", "0")
        lines.append(f"{name}: {amount}")
    return "Баланс:\n" + "\n".join(lines)

# Функция для уведомлений о новых транзакциях
async def poll_transactions():
    while True:
        for user_id, wallet in wallets.items():
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": config.TON_API_KEY}
                url = f"https://tonapi.io/v1/wallets/{wallet}/transactions?limit=10"
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()

            for tx in reversed(data.get("transactions", [])):
                tx_id = tx.get("id")
                # Проверяем, есть ли уже в истории
                if tx_id in [t.get("id") for t in histories[user_id]]:
                    continue

                # Формируем сообщение
                direction = "Приход" if tx.get("incoming") else "Отправка"
                other = tx.get("from") if tx.get("incoming") else tx.get("to")
                currency = tx.get("token_name", "TON")
                amount = tx.get("amount", "0")
                msg = f"Новая транзакция\n{direction}: {other}\nВалюта: {currency}\nКоличество: {amount}"
                await bot.send_message(user_id, msg)

                # Сохраняем в истории
                histories[user_id].append({"id": tx_id, "msg": msg})

        await asyncio.sleep(10)  # проверка каждые 10 секунд

# Запуск бота
async def main():
    asyncio.create_task(poll_transactions())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

