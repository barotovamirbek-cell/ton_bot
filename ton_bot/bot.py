import os
import asyncio
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp

BOT_TOKEN = os.getenv("BOT_TOKEN")
TONAPI_KEY = os.getenv("TONAPI_KEY")
TONCENTER_KEY = os.getenv("TONCENTER_KEY")

bot = AsyncTeleBot(BOT_TOKEN)

chat_wallets = {}
chat_notif = {}
last_seen = {}
MIN_AMOUNT = 0.0001

def main_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Баланс 💰", callback_data="balance"))
    kb.add(InlineKeyboardButton("История 📜", callback_data="transactions"))
    kb.add(InlineKeyboardButton("Вкл уведомления 🔔", callback_data="notif_on"))
    kb.add(InlineKeyboardButton("Выкл уведомления 🔕", callback_data="notif_off"))
    return kb

# Универсальный запрос к API
async def fetch_json(url, headers=None, params=None):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except:
        return None

# Получение баланса с fallback между API
async def get_balance(wallet):
    # 1. TONAPI
    url = f"https://tonapi.io/v1/wallet/{wallet}/balance"
    headers = {"X-API-Key": TONAPI_KEY}
    data = await fetch_json(url, headers)
    if data:
        result = {}
        result['TON'] = float(data.get("balance", 0)) / 1e9
        for jt in data.get("jettons", []):
            result[jt["symbol"]] = float(jt["balance"])
        return result

    # 2. TONCENTER
    url2 = f"https://toncenter.com/api/v2/getWalletBalance?wallet={wallet}&api_key={TONCENTER_KEY}"
    data2 = await fetch_json(url2)
    if data2 and data2.get("ok"):
        result = {}
        result['TON'] = float(data2["result"]["balance"]) / 1e9
        for jt in data2["result"].get("jettons", []):
            result[jt["symbol"]] = float(jt["balance"])
        return result
    return None

# Получение истории с fallback
async def get_transactions(wallet):
    # 1. TONAPI
    url = f"https://tonapi.io/v1/wallet/{wallet}/transactions?limit=20"
    headers = {"X-API-Key": TONAPI_KEY}
    data = await fetch_json(url, headers)
    txs = []
    if data and "transactions" in data:
        for tx in data["transactions"]:
            amount = float(tx.get("amount", 0)) / 1e9
            if amount < MIN_AMOUNT:
                continue
            txs.append({
                "hash": tx.get("hash"),
                "from": tx.get("from"),
                "to": tx.get("to"),
                "token": tx.get("token_symbol", "TON"),
                "amount": amount
            })
        return txs

    # 2. TONCENTER
    url2 = f"https://toncenter.com/api/v2/getTransactions?wallet={wallet}&api_key={TONCENTER_KEY}&limit=20"
    data2 = await fetch_json(url2)
    if data2 and data2.get("ok"):
        for tx in data2["result"]:
            amount = float(tx.get("amount", 0)) / 1e9
            if amount < MIN_AMOUNT:
                continue
            txs.append({
                "hash": tx.get("hash"),
                "from": tx.get("from"),
                "to": tx.get("to"),
                "token": tx.get("token_symbol", "TON"),
                "amount": amount
            })
        return txs

    return []

# Отправка баланса
async def send_balance(chat_id):
    wallet = chat_wallets.get(chat_id)
    if not wallet:
        await bot.send_message(chat_id, "Кошелек не установлен. Используй /setwallet")
        return
    bal = await get_balance(wallet)
    if not bal:
        await bot.send_message(chat_id, "Баланс недоступен")
        return
    msg = f"💰 Баланс кошелька {wallet} 💰\n\n"
    for token, amount in bal.items():
        msg += f"🔹 {token}: {amount}\n"
    await bot.send_message(chat_id, msg)

# Отправка истории
async def send_transactions(chat_id):
    wallet = chat_wallets.get(chat_id)
    if not wallet:
        await bot.send_message(chat_id, "Кошелек не установлен. Используй /setwallet")
        return
    txs = await get_transactions(wallet)
    if not txs:
        await bot.send_message(chat_id, "Транзакций нет")
        return
    msg = ""
    for i, tx in enumerate(txs, 1):
        msg += f"{i}. 📝 Hash: {tx['hash']}\n"
        msg += f"   🔹 From: {tx['from']}\n"
        msg += f"   🔹 To: {tx['to']}\n"
        msg += f"   Токен: {tx['token']}\n"
        msg += f"   Количество: {tx['amount']}\n\n"
    await bot.send_message(chat_id, msg)

# Команды
@bot.message_handler(commands=["start"])
async def start(msg):
    await bot.send_message(msg.chat.id, "Привет! Я Шакалинг кошелька 2.0", reply_markup=main_keyboard())

@bot.message_handler(commands=["setwallet"])
async def setwallet(msg):
    try:
        wallet = msg.text.split()[1]
        chat_wallets[msg.chat.id] = wallet
        chat_notif[msg.chat.id] = True
        await bot.send_message(msg.chat.id, f"Кошелек установлен: {wallet}")
    except IndexError:
        await bot.send_message(msg.chat.id, "Используй: /setwallet <адрес кошелька>")

# Кнопки
@bot.callback_query_handler(func=lambda c: True)
async def callback(call):
    chat_id = call.message.chat.id
    if call.data == "balance":
        await send_balance(chat_id)
    elif call.data == "transactions":
        await send_transactions(chat_id)
    elif call.data == "notif_on":
        chat_notif[chat_id] = True
        await bot.send_message(chat_id, "Уведомления включены")
    elif call.data == "notif_off":
        chat_notif[chat_id] = False
        await bot.send_message(chat_id, "Уведомления выключены")

# Проверка новых транзакций
async def check_new_transactions():
    while True:
        for chat_id, wallet in chat_wallets.items():
            if not chat_notif.get(chat_id, True):
                continue
            txs = await get_transactions(wallet)
            if not txs:
                continue
            last_hash = last_seen.get(chat_id)
            for tx in reversed(txs):
                if tx["hash"] == last_hash:
                    break
                msg = f"💥 Новая транзакция!\n🔹 From: {tx['from']}\n🔹 To: {tx['to']}\n"
                msg += f"Токен: {tx['token']}\nКоличество: {tx['amount']}\n"
                await bot.send_message(chat_id, msg)
            if txs:
                last_seen[chat_id] = txs[0]["hash"]
        await asyncio.sleep(15)

async def main():
    asyncio.create_task(check_new_transactions())
    await bot.infinity_polling()

if __name__ == "__main__":
    asyncio.run(main())
