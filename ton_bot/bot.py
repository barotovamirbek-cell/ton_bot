import os
import time
import json
import requests
from telebot import TeleBot, types
import threading

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

# users.json хранит chat_id -> wallet info
try:
    with open("users.json", "r") as f:
        users = json.load(f)
except FileNotFoundError:
    users = {}

# Инициализация пользователя
def ensure_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {"wallet": "", "notifications": True, "last_hash": None, "history": []}
        save_users()

# Сохраняем users.json
def save_users():
    with open("users.json", "w") as f:
        json.dump(users, f)

# Получение транзакций
def get_transactions(wallet):
    url = f"https://toncenter.com/api/v2/getTransactions?address={wallet}&limit=10"
    response = requests.get(url)
    data = response.json()
    txs = []

    if data.get("ok"):
        result = data.get("result", [])

        # Если result это словарь с ключом "transactions"
        if isinstance(result, dict) and "transactions" in result:
            transactions = result["transactions"]
        # Если result это список транзакций напрямую
        elif isinstance(result, list):
            transactions = result
        else:
            transactions = []

        for tx in transactions:
            in_msg = tx.get("in_msg", {})
            out_msgs = tx.get("out_msgs", [])
            txs.append({
                "hash": tx.get("hash", ""),
                "amount": int(in_msg.get("value", 0)) / 1e9 if in_msg else 0,
                "from": in_msg.get("source", "") if in_msg else "",
                "to": out_msgs[0]["destination"] if out_msgs else "",
            })

    return txs

# Получение баланса TON и токенов (jettons)
def get_wallet_info(wallet):
    info_text = ""
    # Баланс TON
    url_info = f"https://toncenter.com/api/v2/getAddressInformation?address={wallet}"
    resp_info = requests.get(url_info).json()
    if resp_info.get("ok"):
        balance = int(resp_info["result"].get("balance", 0)) / 1e9
        info_text += f"Баланс TON: {balance} TON\n"

    # Токены (jettons)
    url_jettons = f"https://toncenter.com/api/v2/getJettons?account={wallet}"
    resp_jettons = requests.get(url_jettons).json()
    if resp_jettons.get("ok") and resp_jettons.get("result"):
        info_text += "Токены:\n"
        for j in resp_jettons["result"]:
            name = j.get("name", "Unknown")
            symbol = j.get("symbol", "JET")
            balance_j = int(j.get("balance", 0)) / (10 ** int(j.get("decimals", 0)))
            info_text += f"{name} ({symbol}): {balance_j}\n"
    return info_text if info_text else "Баланс не найден"

# Главное меню с кнопками
def create_main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("/setwallet"))
    markup.add(types.KeyboardButton("/history"))
    markup.add(types.KeyboardButton("/transactions"))
    markup.add(types.KeyboardButton("/toggle"))
    bot.send_message(chat_id, "Выберите действие:", reply_markup=markup)

# /start
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = str(message.chat.id)
    ensure_user(chat_id)
    bot.send_message(chat_id, "Привет! Используй кнопки ниже для действий.")
    create_main_menu(chat_id)

# /setwallet
@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    chat_id = str(message.chat.id)
    ensure_user(chat_id)
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(chat_id, "Используйте: /setwallet <адрес>")
        return
    wallet = parts[1]
    users[chat_id]["wallet"] = wallet
    users[chat_id]["last_hash"] = None
    users[chat_id]["history"] = []
    save_users()
    bot.send_message(chat_id, f"Адрес кошелька изменен на {wallet}")

# /toggle
@bot.message_handler(commands=["toggle"])
def toggle_notifications(message):
    chat_id = str(message.chat.id)
    ensure_user(chat_id)
    users[chat_id]["notifications"] = not users[chat_id]["notifications"]
    save_users()
    status = "включены" if users[chat_id]["notifications"] else "выключены"
    bot.send_message(chat_id, f"Уведомления {status}")

# /history с нумерацией и балансом
@bot.message_handler(commands=["history"])
def show_history(message):
    chat_id = str(message.chat.id)
    ensure_user(chat_id)
    hist = users[chat_id]["history"]
    wallet = users[chat_id]["wallet"]

    # Добавляем баланс и токены сверху
    text = get_wallet_info(wallet) + "\n\n"

    if hist:
        for idx, tx in enumerate(hist, start=1):
            text += f"{idx}. Hash: {tx['hash']}\n   From: {tx['from']}\n   To: {tx['to']}\n   Amount: {tx['amount']} TON\n\n"
    else:
        text += "История пуста"

    bot.send_message(chat_id, text)

# /transactions
@bot.message_handler(commands=["transactions"])
def show_transactions(message):
    chat_id = str(message.chat.id)
    ensure_user(chat_id)
    wallet = users[chat_id]["wallet"]
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

# Запуск бота
if __name__ == "__main__":
    threading.Thread(target=monitor_wallets, daemon=True).start()
    bot.infinity_polling()
