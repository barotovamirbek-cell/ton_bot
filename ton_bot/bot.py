import asyncio
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# --- Токен бота ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Пользователи и кошельки ---
user_wallets = {}  # user_id -> wallet address
last_tx = {}       # user_id -> last transaction hash

# --- Клавиатура ---
def main_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("💰 Баланс"), KeyboardButton("📜 История")],
            [KeyboardButton("🔄 Сменить адрес")]
        ],
        resize_keyboard=True
    )
    return kb

# --- Получение баланса и токенов ---
def get_wallet_info(address):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={address}"
    try:
        r = requests.get(url).json()
        if not r.get("ok"):
            return None
        res = r["result"]
        balance = int(res.get("balance", 0)) / 1e9  # TON

        tokens_list = []
        for t in res.get("tokens", []):
            symbol = t.get("name") or t.get("symbol") or "TOKEN"
            decimals = int(t.get("decimals", 9))
            amt = int(t.get("balance", 0)) / (10 ** decimals)
            tokens_list.append(f"{symbol}: {amt}")

        for t in res.get("jettons", []):
            symbol = t.get("name") or t.get("symbol") or "TOKEN"
            decimals = int(t.get("decimals", 9))
            amt = int(t.get("balance", 0)) / (10 ** decimals)
            tokens_list.append(f"{symbol}: {amt}")

        return balance, tokens_list
    except:
        return None

# --- Получение транзакций ---
def get_wallet_transactions(address, limit=5):
    url = f"https://toncenter.com/api/v2/getTransactions?address={address}&limit={limit}"
    try:
        r = requests.get(url).json()
        if not r.get("ok"):
            return []
        return r["result"]["transactions"]
    except:
        return []

def parse_tokens_from_tx(tx):
    lines = []
    in_msg = tx.get("in_msg", {})
    ton_value = int(in_msg.get("value", 0)) / 1e9
    if ton_value != 0:
        lines.append(f"TON: {ton_value}")

    for token in tx.get("token_balances", []):
        symbol = token.get("symbol") or token.get("name") or "TOKEN"
        decimals = int(token.get("decimals", 9))
        amt = int(token.get("balance", 0)) / (10 ** decimals)
        lines.append(f"{symbol}: {amt}")

    for jetton in tx.get("jettons", []):
        symbol = jetton.get("name") or jetton.get("symbol") or "TOKEN"
        decimals = int(jetton.get("decimals", 9))
        amt = int(jetton.get("balance", 0)) / (10 ** decimals)
        lines.append(f"{symbol}: {amt}")

    return "\n".join(lines) if lines else "Нет данных"

# --- Команды ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь TON адрес для отслеживания.\n"
        "Бот будет уведомлять о новых транзакциях и показывать баланс/историю.",
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
        info = get_wallet_info(user_wallets[uid])
        if not info:
            return await message.answer("Ошибка получения баланса.")
        balance, tokens = info
        txt = f"💰 TON: {balance}\n"
        if tokens:
            txt += "\n".join(tokens)
        return await message.answer(txt)

    if text == "📜 История":
        if uid not in user_wallets:
            return await message.answer("Сначала отправьте адрес.")
        txs = get_wallet_transactions(user_wallets[uid])
        if not txs:
            return await message.answer("История пустая.")
        txt = "📜 Последние транзакции:\n\n"
        for tx in txs[:5]:
            sender = tx.get("in_msg", {}).get("source", "Unknown")
            tokens_info = parse_tokens_from_tx(tx)
            txt += f"От: {sender}\n{tokens_info}\n\n"
        return await message.answer(txt.strip())

    # Если это адрес TON
    if text.startswith("UQ") or text.startswith("EQ"):
        user_wallets[uid] = text
        last_tx[uid] = None
        return await message.answer("Адрес сохранён! Теперь бот будет уведомлять о новых транзакциях.")

    await message.answer("Не понял. Отправьте TON адрес или используйте кнопки.")

# --- Проверка новых транзакций ---
async def check_new_transactions():
    while True:
        for uid, wallet in user_wallets.items():
            try:
                txs = get_wallet_transactions(wallet, limit=1)
                if not txs:
                    continue
                tx = txs[0]
                tx_hash = tx["hash"]
                if last_tx.get(uid) != tx_hash:
                    last_tx[uid] = tx_hash
                    sender = tx.get("in_msg", {}).get("source", "Unknown")
                    tokens_info = parse_tokens_from_tx(tx)
                    await bot.send_message(
                        uid,
                        f"🔥 <b>Новая транзакция!</b>\n"
                        f"👤 От: <code>{sender}</code>\n"
                        f"{tokens_info}",
                        parse_mode="HTML"
                    )
            except:
                pass
        await asyncio.sleep(10)

# --- Запуск ---
async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

