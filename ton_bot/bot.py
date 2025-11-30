import os
import requests
from telebot import TeleBot, types
from threading import Thread
import time

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

user_wallets = {}
notify_status = {}

TONCENTER_API = "https://toncenter.com/api/v2"

# ====== Форматирование чисел ======
def format_amount(amount):
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.9f}".rstrip('0').rstrip('.')

# ====== Функции для работы с кошельком ======
def get_balance(wallet):
    res = requests.get(f"{TONCENTER_API}/getWalletInformation", params={"address": wallet})
    data = res.json()
    balances = []
    if data.get("ok"):
        ton_balance = int(data["result"]["balance"]) / 1e9
        balances.append({"token": "TON", "amount": ton_balance})
        for token in data["result"].get("tokens", []):
            balances.append({"token": token["name"], "amount": float(token["balance"])})
    return balances

def get_transactions(wallet):
    res = requests.get(f"{TONCENTER_API}/getTransactions", params={"address": wallet, "limit": 50})
    data = res.json()
    txs = []
    if data.get("ok"):
        for tx in data["result"]:
            # TON транзакции
            if "in_msg" in tx and tx["in_msg"]:
                amount = int(tx["in_msg"].get("value", 0)) / 1e9
                txs.append({
                    "hash": tx.get("hash", ""),
                    "from": tx["in_msg"].get("source", ""),
                    "to": tx.get("out_msgs", [{}])[0].get("destination", ""),
                    "amount": amount,
                    "token": "TON"
                })
            # Токены
            for t in tx.get("token_balances", []):
                txs.append({
                    "hash": tx.get("hash", ""),
                    "from": tx.get("in_msg", {}).get("source", ""),
                    "to": tx.get("out_msgs", [{}])[0].get("destination", ""),
                    "amount": float(t["balance"]),
                    "token": t["name"]
                })
    return txs

# ====== Кнопки ======
def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("💰 Баланс", "📝 История транзакций")
    keyboard.row("🔔 Включить уведомления", "🔕 Выключить уведомления")
    return keyboard

# ====== Форматирование сообщений ======
def format_balance(balance):
    msg = ""
    for b in balance:
        msg += f"{b['token']}: {format_amount(b['amount'])}\n"
    return msg

def format_transactions(txs):
    msg = ""
    for i, tx in enumerate(txs, 1):
        msg += f"{i}. 📝 Hash: {tx['hash']}\n"
        msg += f"   🔹 From: {tx['from']}\n"
        msg += f"   🔹 To: {tx['to']}\n"
        msg += f"   Токен: {tx['token']}\n"
        msg += f"   Количество: {format_amount(tx['amount'])}\n\n"
    return msg

def format_new_tx(tx):
    return f"💥 Новая транзакция!\n🔹 From: {tx['from']}\n🔹 To: {tx['to']}\nТокен: {tx['token']}\nКоличество: {format_amount(tx['amount'])}\n"

# ====== Команды ======
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "Привет! Установи кошелёк командой /setwallet <адрес>", reply_markup=main_menu())

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "Используй: /setwallet <адрес>")
        return
    user_wallets[message.chat.id] = parts[1]
    notify_status[message.chat.id] = True
    bot.send_message(message.chat.id, f"Адрес кошелька установлен: {parts[1]}", reply_markup=main_menu())

# ====== Обработка кнопок ======
@bot.message_handler(func=lambda message: True)
def menu_handler(message):
    wallet = user_wallets.get(message.chat.id)
    if not wallet:
        bot.send_message(message.chat.id, "Сначала установи кошелек командой /setwallet")
        return

    if message.text == "💰 Баланс":
        balance = get_balance(wallet)
        bot.send_message(message.chat.id, f"💰 Баланс кошелька {wallet} 💰\n\n{format_balance(balance)}")

    elif message.text == "📝 История транзакций":
        txs = get_transactions(wallet)
        if not txs:
            bot.send_message(message.chat.id, "Транзакций нет")
            return
        bot.send_message(message.chat.id, format_transactions(txs))

    elif message.text == "🔔 Включить уведомления":
        notify_status[message.chat.id] = True
        bot.send_message(message.chat.id, "Уведомления включены")

    elif message.text == "🔕 Выключить уведомления":
        notify_status[message.chat.id] = False
        bot.send_message(message.chat.id, "Уведомления выключены")

# ====== Уведомления о новых транзакциях ======
def poll_new_transactions():
    last_seen = {}
    while True:
        for chat_id, wallet in user_wallets.items():
            if not notify_status.get(chat_id, True):
                continue
            txs = get_transactions(wallet)
            if not txs:
                continue
            if chat_id not in last_seen:
                last_seen[chat_id] = txs[0]["hash"]
                continue
            for tx in reversed(txs):
                if tx["hash"] == last_seen[chat_id]:
                    break
                bot.send_message(chat_id, format_new_tx(tx))
            last_seen[chat_id] = txs[0]["hash"]
        time.sleep(15)

Thread(target=poll_new_transactions, daemon=True).start()
bot.infinity_polling()
