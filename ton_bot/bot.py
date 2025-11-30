import os
import json
import time
import asyncio
import requests
from aiogram import Bot, Dispatcher, types, executor

API_TOKEN = os.getenv("API_TOKEN")  # Токен берём из переменной окружения!!
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

DATA_FILE = "wallets.json"

# =====================================================
#               ХРАНЕНИЕ ДАННЫХ
# =====================================================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    return json.load(open(DATA_FILE, "r"))


def save_data(data):
    json.dump(data, open(DATA_FILE, "w"), indent=2)


data = load_data()
last_tx = {}  # Последняя транза для каждого юзера


# =====================================================
#               КНОПКИ
# =====================================================

def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        types.KeyboardButton("💰 Баланс"),
        types.KeyboardButton("📜 История"),
    )
    kb.add(types.KeyboardButton("🔄 Сменить адрес"))
    return kb


# =====================================================
#               КОМАНДА /start
# =====================================================

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    uid = str(message.chat.id)

    data.setdefault(uid, {"wallet": None})
    save_data(data)

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Этот бот уведомляет о <b>новых транзакциях TON</b> и показывает:\n"
        "• 💰 Баланс (TON + все токены)\n"
        "• 📜 Историю транзакций\n"
        "• 🔔 Авто-уведомления о новых переводах\n\n"
        "👉 Просто отправь адрес TON для начала.\n\n",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# =====================================================
#          ПОЛЬЗОВАТЕЛЬ ВВЁЛ АДРЕС КОШЕЛЬКА
# =====================================================

@dp.message_handler(lambda m: m.text == "🔄 Сменить адрес")
async def change_wallet(message: types.Message):
    await message.answer("Введите новый адрес TON…")


@dp.message_handler()
async def set_wallet(message: types.Message):
    uid = str(message.chat.id)
    text = message.text.strip()

    if len(text) < 40:
        return await message.answer("❌ Это не похоже на TON адрес.")

    data[uid] = {"wallet": text}
    save_data(data)

    await message.answer(
        f"✅ Адрес обновлён!\n\n"
        f"Теперь слежу за:\n<b>{text}</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# =====================================================
#          ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ТОКЕНОВ
# =====================================================

def parse_tokens_info(r):
    out = []

    # TON
    ton_balance = int(r.get("balance", 0)) / 1e9
    out.append(f"TON: {ton_balance}")

    # Jettons
    jets = r.get("jettons", [])
    for j in jets:
        name = j.get("name") or j.get("symbol") or "TOKEN"
        amount = int(j.get("balance", 0)) / (10 ** j.get("decimals", 9))
        out.append(f"{name}: {amount}")

    return "\n".join(out)


def parse_tokens_from_tx(tx):
    lines = []

    # TON
    in_msg = tx.get("in_msg", {})
    ton = int(in_msg.get("value", 0)) / 1e9
    if ton != 0:
        lines.append(f"TON: {ton}")

    # Jettons
    jets = in_msg.get("jettons", [])
    for j in jets:
        name = j.get("name") or j.get("symbol") or "TOKEN"
        amount = int(j.get("amount", 0)) / (10 ** j.get("decimals", 9))
        lines.append(f"{name}: {amount}")

    return "\n".join(lines) if lines else "Нет данных"


# =====================================================
#                КНОПКА "БАЛАНС"
# =====================================================

@dp.message_handler(lambda m: m.text == "💰 Баланс")
async def balance_button(message: types.Message):
    await balance_cmd(message)


@dp.message_handler(commands=['balance'])
async def balance_cmd(message: types.Message):
    uid = str(message.chat.id)
    wallet = data.get(uid, {}).get("wallet")

    if not wallet:
        return await message.answer("❌ Сначала отправьте адрес кошелька.")

    try:
        r = requests.get(f"https://tonapi.io/v2/accounts/{wallet}").json()
        tokens = parse_tokens_info(r)

        await message.answer(
            f"💰 <b>Баланс кошелька:</b>\n\n<code>{tokens}</code>",
            parse_mode="HTML"
        )
    except:
        await message.answer("⚠ Ошибка при получении баланса.")


# =====================================================
#                КНОПКА "ИСТОРИЯ"
# =====================================================

@dp.message_handler(lambda m: m.text == "📜 История")
async def history_button(message: types.Message):
    await history_cmd(message)


@dp.message_handler(commands=['history'])
async def history_cmd(message: types.Message):
    uid = str(message.chat.id)
    wallet = data.get(uid, {}).get("wallet")

    if not wallet:
        return await message.answer("❌ Сначала отправьте адрес кошелька.")

    try:
        r = requests.get(
            f"https://tonapi.io/v2/explorer/getTransactions?address={wallet}&limit=5"
        ).json()

        txs = r.get("transactions", [])
        if not txs:
            return await message.answer("Нет транзакций.")

        text = "📜 <b>Последние транзакции:</b>\n\n"

        for tx in txs:
            sender = tx.get("in_msg", {}).get("source", "Unknown")
            tokens = parse_tokens_from_tx(tx)

            text += (
                f"👤 От: <code>{sender}</code>\n"
                f"{tokens}\n\n"
            )

        await message.answer(text, parse_mode="HTML")

    except:
        await message.answer("⚠ Ошибка при получении истории.")


# =====================================================
#            ФОНОВЫЙ ЧЕКЕР ТРАНЗАКЦИЙ
# =====================================================

async def checker():
    global last_tx
    await asyncio.sleep(2)

    while True:
        for uid, info in data.items():
            wallet = info.get("wallet")
            if not wallet:
                continue

            try:
                r = requests.get(
                    f"https://tonapi.io/v2/explorer/getTransactions?address={wallet}&limit=1"
                ).json()

                if "transactions" not in r:
                    continue

                tx = r["transactions"][0]
                tx_hash = tx["hash"]

                # Новая транзакция?
                if last_tx.get(uid) != tx_hash:
                    last_tx[uid] = tx_hash

                    tokens = parse_tokens_from_tx(tx)
                    sender = tx.get("in_msg", {}).get("source", "Unknown")

                    await bot.send_message(
                        uid,
                        f"🔥 <b>Новая транзакция!</b>\n\n"
                        f"👤 От: <code>{sender}</code>\n"
                        f"{tokens}",
                        parse_mode="HTML"
                    )

            except:
                pass

        await asyncio.sleep(2)


# =====================================================
#                   ЗАПУСК БОТА
# =====================================================

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(checker())
    executor.start_polling(dp, skip_updates=True)
