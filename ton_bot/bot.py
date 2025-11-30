import os
import time
import json
import requests
from telebot import TeleBot

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

# users.json будет хранить: chat_id -> wallet info
try:
    with open("users.json", "r") as f:
        users = json.load(f)
except FileNotFoundError:
    users = {}

# Получение баланса TON
def get_ton_balance(wallet):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={wallet}"
    response = requests.get(url)
    data = response.json()
    if data.get("ok"):
        return int(data["result"]["balance"]) / 1e9
    return 0

# Получение jettons
def get_jettons(wallet):
    url = f"https://toncenter.com/api/v2/getJettons?account={wallet}"
    response = requests.get(url)
    data = response.json()
    jettons = []
    if data.get("ok"):
        for j in data["result"]:
            jettons.append({
                "name": j.get("name", "Unknown"),
                "symbol": j.get("symbol", "JET"),
                "balance": int(j.get("balance", 0)) / (10 ** int(j.get("decimals", 0)))
            })
    return jettons

# Получение транзакций
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

# /start
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = str(message.chat.id)
    if chat_id not in users:
        users[chat_id] = {"wallet": "", "notifications": True, "last_hash": None, "history": []}
        save_users()
    bot.send_message(chat_id, "Привет! Используй /setwallet <адрес> чтобы задать кошелек TON")

# /setwallet
@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    chat_id = str(message.chat.id)
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(chat_id, "Используйте: /setwallet <адрес>")
        return
    wallet = parts[1]
    if chat_id not in users:
        users[chat_id] = {"wallet": wallet, "notifications": True, "last_hash": None, "history": []}
    else:
        users[chat_id]["wallet"] = wallet
        users[chat_id]["last_hash"] = None
        users[chat_id]["history"] = []
    save_users()
    bot.send_message(chat_id, f"Адрес кошелька изменен на {wallet}")

# /toggle
@bot.message_handler(commands=["toggle"])
def toggle_notifications(message):
    chat_id = str(message.chat.id)
    users[chat_id]["notifications"] = not users[chat_id].get("notifications", True)
    save_users()
    status = "включены" if users[chat_id]["notifications"] else "выключены"
    bot.send_message(chat_id, f"Уведомления {status}")

# /history
@bot.message_handler(commands=["history"])
def show_history(message):
    chat_id = str(message.chat.id)
    hist = users.get(chat_id, {}).get("history", [])
    if hist:
        text = "\n".join([f"{tx['hash']} | {tx['amount']} TON" for tx in hist])
    else:
        text = "История пуста"
    bot.send_message(chat_id, text)

# /transactions
@bot.message_handler(commands=["transactions"])
def show_transactions(message):
    chat_id = str(message.chat.id)
    wallet = users.get(chat_id, {}).get("wallet")
    if not wallet:
        bot.send_message(chat_id, "Сначала задайте кошелек через /setwallet")
        return
    txs = get_transactions(wallet)
    if txs:
        text = "\n\n".join([f"Hash: {tx['hash']}\nFrom: {tx['from']}\nTo: {tx['to']}\nAmount: {tx['amount']} TON" for tx in txs])
    else:
        text = "Транзакции не найдены"
    bot.send_message(chat_id, text)

# Мониторинг всех кошельков
def monitor_wallets():
    while True:
        for chat_id, info in users.items():
            wallet = info.get("wallet")
            if not wallet:
                continue
            try:
                txs = get_transactions(wallet)
                if txs:
                    last_hash = info.get("last_hash")
                    if txs[0]["hash"] != last_hash:
                        info["last_hash"] = txs[0]["hash"]
                        info["history"].insert(0, txs[0])
                        if info.get("notifications", True):
                            msg = f"Новая транзакция!\nFrom: {txs[0]['from']}\nTo: {txs[0]['to']}\nAmount: {txs[0]['amount']} TON"
                            bot.send_message(chat_id, msg)
            except Exception as e:
                print("Ошибка мониторинга:", e)
        save_users()
        time.sleep(30)

# Сохраняем users.json
def save_users():
    with open("users.json", "w") as f:
        json.dump(users, f)

# Запуск
if __name__ == "__main__":
    import threading
    threading.Thread(target=monitor_wallets, daemon=True).start()
    bot.infinity_polling()
