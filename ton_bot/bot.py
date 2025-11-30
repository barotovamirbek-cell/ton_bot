import os
import json
import time
import asyncio
import requests
from aiogram import Bot, Dispatcher, types, executor

API_TOKEN = os.getenv("API_TOKEN")  # токен из переменных окружения
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

DATA_FILE = "wallets.json"


# ======================== ХРАНИЛИЩЕ ========================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    return json.load(open(DATA_FILE, "r"))

def save_data(data):
    json.dump(data, open(DATA_FILE, "w"), indent=2)

data = load_data()
last_tx = {}   # user: last_tx_hash


# ======================== /start ============================
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    uid = str(message.chat.id)

    data.setdefault(uid, {"wallet": None})
    save_data(data)

    await message.answer("👋 Отправь TON-адрес. Старый адрес будет удалён и заменён новым.")


# =================== ПОЛЬЗОВАТЕЛЬ ОТПРАВИЛ АДРЕС =================
@dp.message_handler()
async def set_wallet(message: types.Message):
    uid = str(message.chat.id)
    wallet = message.text.strip()

    if len(wallet) < 40:
        return await message.answer("❌ Это не TON-адрес. Отправь корректный адрес.")

    data[uid] = {"wallet": wallet}
    save_data(data)

    await message.answer(f"✅ Адрес обновлён.\nТеперь слежу за: {wallet}")


# ===================== ОТПРАВКА СООБЩЕНИЯ ======================
async def notify(uid, text):
    try:
        await bot.send_message(uid, text)
    except:
        pass


# ===================== ПАРСИНГ ТОКЕНОВ =========================
def parse_tokens(tx):
    text = ""

    # TON
    in_msg = tx.get("in_msg", {})
    value = int(in_msg.get("value", 0)) / 1e9
    if value:
        text += f"TON: {value}\n"

    # Jettons
    tokens = tx.get("in_msg", {}).get("jettons", [])
    for t in tokens:
        name = t.get("name") or t.get("symbol") or "TOKEN"
        amount = int(t.get("amount", 0)) / (10 ** t.get("decimals", 9))
        text += f"{name}: {amount}\n"

    return text.strip() if text else "Нет данных"


# ===================== ЧЕКЕР ТРАНЗАКЦИЙ =========================
async def checker():
    global last_tx
    await asyncio.sleep(2)

    while True:
        for uid, info in data.items():
            wallet = info.get("wallet")
            if not wallet:
                continue

            try:
                url = f"https://tonapi.io/v2/explorer/getTransactions?address={wallet}"
                r = requests.get(url, timeout=5).json()

                if "transactions" not in r:
                    continue

                tx = r["transactions"][0]
                tx_hash = tx["hash"]

                if last_tx.get(uid) != tx_hash:
                    last_tx[uid] = tx_hash

                    tokens = parse_tokens(tx)
                    await notify(
                        uid,
                        f"🔥 Новая транзакция!\n\n"
                        f"👜 Адрес: {wallet}\n"
                        f"🔗 TX: {tx_hash}\n\n"
                        f"{tokens}"
                    )

            except Exception as e:
                print("ERR:", e)

        await asyncio.sleep(1)


# ========================= START ===============================
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(checker())
    executor.start_polling(dp, skip_updates=True)
