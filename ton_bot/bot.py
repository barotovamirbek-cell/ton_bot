import os
import asyncio
from telebot.async_telebot import AsyncTeleBot
import requests

# Токен бота и chat id из переменных окружения
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

bot = AsyncTeleBot(BOT_TOKEN)

# Переменные
notifications_enabled = True
history = []
wallet_address = os.getenv("TON_WALLET_ADDRESS")  # Начальный кошелек
last_hash = None

# Получение баланса TON
def get_ton_balance(wallet):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={wallet}"
    response = requests.get(url)
    data = response.json()
    if data.get("ok"):
        return int(data["result"]["balance"]) / 1e9
    return 0

# Получение списка jettons и их балансов
def get_jettons(wallet):
    url = f"https://toncenter.com/api/v2/getJettons?account={wallet}"
    response = requests.get(url)
    data = response.json()
    jettons = []
    if data.get("ok"):
        for jetton in data["result"]:
            jettons.append({
                "name": jetton.get("name", "Unknown"),
                "symbol": jetton.get("symbol", "JET"),
                "balance": int(jetton.get("balance", 0)) / (10 ** int(jetton.get("decimals", 0)))
            })
    return jettons

# Получение последних транзакций
def get_transactions(wallet):
    url = f"https://toncenter.com/api/v2/getTransactions?address={wallet}&limit=10"
    response = requests.get(url)
    data = response.json()
    txs = []
    if data.get("ok"):
        for tx in data["result"]["transactions"]:
            txs.append({
                "hash": tx["hash"],
                "amount": int(tx["in_msg"]["value"]) / 1e9 if "in_msg" in tx else 0,
                "from": tx["in_msg"]["source"] if "in_msg" in tx else "",
                "to": tx["out_msgs"][0]["destination"] if "out_msgs" in tx else "",
            })
    return txs

# Команды бота
@bot.message_handler(commands=["start"])
async def start(message):
    ton_balance = get_ton_balance(wallet_address)
    jettons = get_jettons(wallet_address)
    jetton_text = "\n".join([f"{j['symbol']}: {j['balance']}" for j in jettons]) or "Нет токенов"
    await bot.send_message(message.chat.id,
                           f"Бот запущен!\nКошелек: {wallet_address}\nБаланс TON: {ton_balance}\nТокены:\n{jetton_text}")

@bot.message_handler(commands=["history"])
async def show_history(message):
    if history:
        text = "\n".join([f"{tx['hash']} | {tx['amount']} TON" for tx in history])
    else:
        text = "История пуста"
    await bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["transactions"])
async def show_transactions(message):
    txs = get_transactions(wallet_address)
    if txs:
        text = "\n\n".join([f"Hash: {tx['hash']}\nFrom: {tx['from']}\nTo: {tx['to']}\nAmount: {tx['amount']} TON" for tx in txs])
    else:
        text = "Транзакции не найдены"
    await bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["toggle"])
async def toggle_notifications(message):
    global notifications_enabled
    notifications_enabled = not notifications_enabled
    status = "включены" if notifications_enabled else "выключены"
    await bot.send_message(message.chat.id, f"Уведомления {status}.")

# Смена адреса кошелька
@bot.message_handler(commands=["setwallet"])
async def set_wallet(message):
    global wallet_address, history, last_hash
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await bot.send_message(message.chat.id, "Используйте: /setwallet <адрес>")
            return
        wallet_address = parts[1]
        history = []
        last_hash = None
        await bot.send_message(message.chat.id, f"Адрес кошелька изменен на {wallet_address}")
    except Exception as e:
        await bot.send_message(message.chat.id, f"Ошибка: {e}")

# Фоновая проверка транзакций
async def monitor_wallet():
    global history, last_hash
    while True:
        try:
            txs = get_transactions(wallet_address)
            if txs:
                if last_hash != txs[0]["hash"]:
                    last_hash = txs[0]["hash"]
                    history.insert(0, txs[0])
                    if notifications_enabled:
                        msg = f"Новая транзакция!\nFrom: {txs[0]['from']}\nTo: {txs[0]['to']}\nAmount: {txs[0]['amount']} TON"
                        await bot.send_message(chat_id=CHAT_ID, text=msg)
        except Exception as e:
            print("Ошибка мониторинга:", e)
        await asyncio.sleep(30)

# Запуск бота
async def main():
    asyncio.create_task(monitor_wallet())
    await bot.infinity_polling()

if __name__ == "__main__":
    asyncio.run(main())
