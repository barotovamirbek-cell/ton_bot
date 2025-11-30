import asyncio
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

API_TOKEN = os.getenv("API_TOKEN")
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# user_id → wallet_address
user_wallets = {}
# user_id → last_tx_hash
last_tx = {}


# ---------- Кнопки ----------
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📜 История")],
            [KeyboardButton(text="🔄 Сменить адрес")],
        ],
        resize_keyboard=True
    )


# ---------- Баланс ----------
async def get_wallet_balance(address):
    try:
        r = requests.get(f"https://tonapi.io/v2/accounts/{address}").json()
        balance = r.get("balance", 0) / 1e9
        tokens = r.get("jettons", [])
        txt = f"💰 TON: {balance}\n"
        for t in tokens:
            name = t["jetton"]["name"]
            decimals = t["jetton"]["decimals"]
            amt = float(t["balance"]) / (10 ** decimals)
            txt += f"🪙 {name}: {amt}\n"
        return txt
    except:
        return "Ошибка получения баланса."


# ---------- История ----------
async def get_wallet_history(address):
    try:
        r = requests.get(f"https://tonapi.io/v2/accounts/{address}/transactions").json()
        txs = r.get("transactions", [])
        if not txs:
            return "История пустая."
        txt = "📜 Последние транзакции:\n\n"
        for tx in txs[:5]:
            amt = tx.get("in_msg", {}).get("value", 0) / 1e9
            txt += f"👉 {amt} TON\n"
        return txt
    except:
        return "Ошибка получения истории."


# ---------- Парсинг токенов из транзакции ----------
def parse_tokens_from_tx(tx):
    lines = []
    in_msg = tx.get("in_msg", {})
    ton = int(in_msg.get("value", 0)) / 1e9
    if ton != 0:
        lines.append(f"TON: {ton}")
    jettons = in_msg.get("jettons", [])
    for j in jettons:
        name = j.get("name") or j.get("symbol") or "TOKEN"
        amt = int(j.get("amount", 0)) / (10 ** j.get("decimals", 9))
        lines.append(f"{name}: {amt}")
    return "\n".join(lines) if lines else "Нет данных"


# ---------- Команды ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь TON адрес для отслеживания.\n"
        "После этого бот будет уведомлять о новых транзакциях и показывать баланс/историю.",
        reply_markup=main_keyboard()
    )


@dp.message()
async def handler(message: types.Message):
    uid = message.from_user.id
    text = message.text

    if text == "🔄 Сменить адрес":
        user_wallets.pop(uid, None)
        last_tx.pop(uid, None)
        return await message.answer("Введите новый TON адрес:")

    if text == "💰 Баланс":
        if uid not in user_wallets:
            return await message.answer("Сначала отправьте адрес.")
        return await message.answer(await get_wallet_balance(user_wallets[uid]))

    if text == "📜 История":
        if uid not in user_wallets:
            return await message.answer("Сначала отправьте адрес.")
        return await message.answer(await get_wallet_history(user_wallets[uid]))

    # если текст выглядит как TON адрес
    if text.startswith("UQ") or text.startswith("EQ"):
        user_wallets[uid] = text
        last_tx[uid] = None
        return await message.answer("Адрес сохранён! Теперь бот будет уведомлять о новых транзакциях.")

    await message.answer("Не понял. Отправьте TON адрес или используйте кнопки.")


# ---------- Фоновый проверщик новых транзакций ----------
async def check_new_transactions():
    while True:
        for uid, wallet in user_wallets.items():
            try:
                r = requests.get(f"https://tonapi.io/v2/accounts/{wallet}/transactions?limit=1").json()
                txs = r.get("transactions", [])
                if not txs:
                    continue
                tx = txs[0]
                tx_hash = tx["hash"]

                # Если новая транзакция
                if last_tx.get(uid) != tx_hash:
                    last_tx[uid] = tx_hash
                    tokens_info = parse_tokens_from_tx(tx)
                    sender = tx.get("in_msg", {}).get("source", "Unknown")

                    await bot.send_message(
                        uid,
                        f"🔥 <b>Новая транзакция!</b>\n"
                        f"👤 От: <code>{sender}</code>\n"
                        f"{tokens_info}",
                        parse_mode="HTML"
                    )
            except:
                pass
        await asyncio.sleep(5)  # проверяем каждые 5 секунд


# ---------- Запуск ----------
async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
