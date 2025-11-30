import os
import requests
from telebot import TeleBot, types
from threading import Thread
import time

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

# Словарь для хранения кошельков пользователей
user_wallets = {}

# API Toncenter
TONCENTER_API = "https://toncenter.com/api/v2"

# Получение баланса
def get_balance(wallet):
    res = requests.get(f"{TONCENTER_API}/getWalletInformation", params={"address": wallet})
    data = res.json()
    balance = []
    if data.get("ok"):
        ton_balance = int(data["result"]["balance"]) / 1e9
        balance.append({"token": "TON", "amount": ton_balance})
        for token in data["result"].get("tokens", []):
            balance.append({"token": token["name"], "amount": float(token["balance"])})
    return balance

# Получение транзакций
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

# Кнопки меню
def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("💰 Баланс", "📝 История транзакций")
    return keyboard

# Команда /start
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "Привет! Установи кошелёк командой /setwallet <адрес>", reply_markup=main_menu())

# Команда /setwallet
@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "Используй: /setwallet <адрес>")
        return
    user_wallets[message.chat.id] = parts[1]
    bot.send_message(message.chat.id, f"Адрес кошелька установлен: {parts[1]}", reply_markup=main_menu())

# Обработка кнопок
@bot.message_handler(func=lambda message: True)
def menu_handler(message):
    wallet = user_wallets.get(message.chat.id)
    if not wallet:
        bot.send_message(message.chat.id, "Сначала установи кошелек командой /setwallet")
        return

    if message.text == "💰 Баланс":
        balance = get_balance(wallet)
        msg = f"💰 Баланс кошелька {wallet} 💰\n\n"
        for b in balance:
            msg += f"{b['token']}: {b['amount']}\n"
        bot.send_message(message.chat.id, msg)

    elif message.text == "📝 История транзакций":
        txs = get_transactions(wallet)
        if not txs:
            bot.send_message(message.chat.id, "Транзакций нет")
            return
        msg = ""
        for i, tx in enumerate(txs, 1):
            msg += f"{i}. 📝 Hash: {tx['hash']}\n"
            msg += f"   🔹 From: {tx['from']}\n"
            msg += f"   🔹 To: {tx['to']}\n"
            msg += f"   Токен: {tx['token']}\n"
            msg += f"   Количество: {tx['amount']}\n\n"
        bot.send_message(message.chat.id, msg)

# Уведомления о новых транзакциях
def poll_new_transactions():
    last_seen = {}
    while True:
        for chat_id, wallet in user_wallets.items():
            txs = get_transactions(wallet)
            if not txs:
                continue
            if chat_id not in last_seen:
                last_seen[chat_id] = txs[0]["hash"]
                continue
            for tx in reversed(txs):
                if tx["hash"] == last_seen[chat_id]:
                    break
                msg = f"💥 Новая транзакция!\n🔹 From: {tx['from']}\n🔹 To: {tx['to']}\nТокен: {tx['token']}\nКоличество: {tx['amount']}\n"
                bot.send_message(chat_id, msg)
            last_seen[chat_id] = txs[0]["hash"]
        time.sleep(15)

Thread(target=poll_new_transactions, daemon=True).start()
bot.infinity_polling()
