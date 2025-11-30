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

def ensure_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {"wallet": "", "notifications": True, "last_hash": None, "history": []}
        save_users()

def save_users():
    with open("users.json", "w") as f:
        json.dump(users, f)

# Баланс и токены
def get_wallet_info(wallet):
    text = f"💰 Баланс кошелька {wallet} 💰\n\n"

    # TON
    resp_info = requests.get(f"https://toncenter.com/api/v2/getAddressInformation?address={wallet}").json()
    ton_balance = 0
    if resp_info.get("ok") and resp_info.get("result"):
        ton_balance = int(resp_info["result"].get("balance", 0)) / 1e9
        ton_balance = "{:.9f}".format(ton_balance).rstrip('0').rstrip('.') if ton_balance > 0 else "0"
    text += f"Токен: TON\nКоличество: {ton_balance}\n"

    # Jettons
    resp_jettons = requests.get(f"https://toncenter.com/api/v2/getJettons?account={wallet}").json()
    if resp_jettons.get("ok") and resp_jettons.get("result"):
        for j in resp_jettons["result"]:
            name = j.get("name", "Unknown")
            symbol = j.get("symbol", "JET")
            balance_j = j.get("balance")
            decimals = int(j.get("decimals", 0))
            if balance_j is not None and int(balance_j) > 0:
                balance_j = int(balance_j) / (10 ** decimals)
                balance_j = "{:.9f}".format(balance_j).rstrip('0').rstrip('.')
                text += f"Токен: {name} ({symbol})\nКоличество: {balance_j}\n"
    return text

# Получение транзакций
def get_transactions(wallet):
    url = f"https://toncenter.com/api/v2/getTransactions?address={wallet}&limit=50"
    resp = requests.get(url).json()
    txs = []

    if resp.get("ok"):
        result = resp.get("result", [])
        transactions = []
        if isinstance(result, dict) and "transactions" in result:
            transactions = result["transactions"]
        elif isinstance(result, list):
            transactions = result

        for tx in transactions:
            hash_tx = tx.get("hash", "")
            in_msg = tx.get("in_msg", {})
            in_from = in_msg.get("source", "")
            in_to = in_msg.get("destination", "")
            in_amount = int(in_msg.get("value", 0)) / 1e9 if in_msg else 0
            in_amount = "{:.9f}".format(in_amount).rstrip('0').rstrip('.') if in_amount > 0 else "0"
            txs.append({
                "hash": hash_tx,
                "from": in_from,
                "to": in_to,
                "amount": in_amount
            })

    txs = sorted(txs, key=lambda x: x["hash"], reverse=True)
    return txs

# Форматирование транзакции с токенами
def format_transaction(tx, wallet):
    msg = ""
    msg += f"🔹 From: {tx['from']}\n"
    msg += f"🔹 To: {tx['to']}\n"

    # TON
    amount = float(tx.get("amount", 0))
    amount_str = "{:.9f}".format(amount).rstrip('0').rstrip('.') if amount > 0 else "0"
    msg += f"Токен: TON\nКоличество: {amount_str}\n"

    # Jettons
    resp_jettons = requests.get(f"https://toncenter.com/api/v2/getJettons?account={wallet}").json()
    if resp_jettons.get("ok") and resp_jettons.get("result"):
        for j in resp_jettons["result"]:
            name = j.get("name", "Unknown")
            symbol = j.get("symbol", "JET")
            balance_j = j.get("balance")
            decimals = int(j.get("decimals", 0))
            if balance_j is not None and int(balance_j) > 0:
                balance_j = int(balance_j) / (10 ** decimals)
                balance_j = "{:.9f}".format(balance_j).rstrip('0').rstrip('.')
                msg += f"Токен: {name} ({symbol})\nКоличество: {balance_j}\n"

    return msg

# Главное меню
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

# /toggle уведомления
@bot.message_handler(commands=["toggle"])
def toggle_notifications(message):
    chat_id = str(message.chat.id)
    ensure_user(chat_id)
    users[chat_id]["notifications"] = not users[chat_id]["notifications"]
    save_users()
    status = "включены" if users[chat_id]["notifications"] else "выключены"
    bot.send_message(chat_id, f"Уведомления {status}")

# /history
@bot.message_handler(commands=["history"])
def show_history(message):
    chat_id = str(message.chat.id)
    ensure_user(chat_id)
    wallet = users[chat_id]["wallet"]

    if not wallet:
        bot.send_message(chat_id, "Сначала задайте кошелек через /setwallet")
        return

    hist = users[chat_id]["history"]
    text = get_wallet_info(wallet) + "\n"

    if hist:
        for idx, tx in enumerate(hist, start=1):
            text += f"{idx}. 📝 Hash: {tx['hash']}\n"
            text += format_transaction(tx, wallet) + "\n"
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
        text = "\n\n".join([f"📝 Hash: {tx['hash']}\n{format_transaction(tx, wallet)}" for tx in txs])
    else:
        text = "Транзакции не найдены"
    bot.send_message(chat_id, text)

# Мониторинг транзакций
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
                    new_txs = []
                    for tx in txs:
                        if tx["hash"] == last_hash:
                            break
                        new_txs.append(tx)

                    if new_txs:
                        info["last_hash"] = new_txs[0]["hash"]
                        info["history"] = new_txs + info["history"]
                        if info.get("notifications", True):
                            for tx in reversed(new_txs):
                                msg = f"💥 Новая транзакция!\n"
                                msg += format_transaction(tx, wallet)
                                bot.send_message(chat_id, msg)
            except Exception as e:
                print("Ошибка мониторинга:", e)
        save_users()
        time.sleep(30)

# Запуск
if __name__ == "__main__":
    threading.Thread(target=monitor_wallets, daemon=True).start()
    bot.infinity_polling()
