import os
import requests
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

# Простая база для хранения кошельков и уведомлений для каждого чата
wallets = {}
notifications = {}

# Фильтр минимальных сумм (не показывать мизерные транзакции)
MIN_AMOUNT = 0.001

# API для TON и Jettons
TONCENTER_API = "https://toncenter.com/api/v2"

def get_balance(address):
    balances = {}
    # TON баланс
    resp = requests.get(f"{TONCENTER_API}/getAddressInfo?address={address}")
    data = resp.json()
    if "result" in data and data["result"]:
        ton = int(data["result"]["balance"]) / 1e9
        balances["TON"] = ton
    else:
        balances["TON"] = 0

    # Jettons (USDT, другие)
    resp = requests.get(f"{TONCENTER_API}/getJettons?address={address}")
    data = resp.json()
    if "result" in data:
        for jet in data["result"]:
            name = jet.get("name", "Unknown")
            amount = float(jet.get("balance", 0))
            balances[name] = amount
    return balances

def get_transactions(address):
    tx_list = []
    resp = requests.get(f"{TONCENTER_API}/getTransactions?address={address}&limit=20")
    data = resp.json()
    if "result" in data:
        for tx in data["result"]:
            amount = int(tx.get("amount", 0)) / 1e9
            if amount < MIN_AMOUNT:
                continue
            tx_list.append({
                "hash": tx.get("hash", ""),
                "from": tx.get("from", ""),
                "to": tx.get("to", ""),
                "amount": amount,
                "token": "TON"
            })
            # Jettons внутри tx
            for jt in tx.get("jettons", []):
                tx_list.append({
                    "hash": tx.get("hash", ""),
                    "from": tx.get("from", ""),
                    "to": tx.get("to", ""),
                    "amount": float(jt.get("amount", 0)),
                    "token": jt.get("name", "Unknown")
                })
    return tx_list

def format_balance(balances):
    text = ""
    for token, amount in balances.items():
        text += f"🔹 {token}: {amount}\n"
    return text or "Баланс недоступен"

def format_transactions(tx_list):
    if not tx_list:
        return "Транзакций нет"
    text = ""
    for i, tx in enumerate(tx_list, 1):
        text += f"{i}. 📝 Hash: {tx['hash']}\n"
        text += f"   🔹 From: {tx['from']}\n"
        text += f"   🔹 To: {tx['to']}\n"
        text += f"   Токен: {tx['token']}\n"
        text += f"   Количество: {tx['amount']}\n\n"
    return text

# --- Команды ---
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("Показать баланс", "История транзакций")
    markup.row("Вкл уведомления", "Выкл уведомления")
    bot.send_message(chat_id, "Привет! Используй кнопки ниже.", reply_markup=markup)

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    chat_id = message.chat.id
    try:
        address = message.text.split()[1]
        wallets[chat_id] = address
        notifications.setdefault(chat_id, False)
        bot.send_message(chat_id, f"Кошелек установлен: {address}")
    except IndexError:
        bot.send_message(chat_id, "Использование: /setwallet <адрес_кошелька>")

# --- Кнопки ---
@bot.message_handler(func=lambda m: True)
def buttons(message):
    chat_id = message.chat.id
    if chat_id not in wallets:
        bot.send_message(chat_id, "Сначала установи кошелек с помощью /setwallet")
        return
    address = wallets[chat_id]
    text = message.text
    if text == "Показать баланс":
        balances = get_balance(address)
        bot.send_message(chat_id, f"💰 Баланс кошелька {address} 💰\n\n" + format_balance(balances))
    elif text == "История транзакций":
        tx_list = get_transactions(address)
        bot.send_message(chat_id, format_transactions(tx_list))
    elif text == "Вкл уведомления":
        notifications[chat_id] = True
        bot.send_message(chat_id, "Уведомления включены")
    elif text == "Выкл уведомления":
        notifications[chat_id] = False
        bot.send_message(chat_id, "Уведомления выключены")

# --- Проверка новых транзакций (каждую минуту) ---
import threading
import time

def poll_new_transactions():
    last_hash = {}
    while True:
        for chat_id, address in wallets.items():
            if not notifications.get(chat_id, False):
                continue
            tx_list = get_transactions(address)
            for tx in tx_list:
                h = tx["hash"]
                if last_hash.get(chat_id) == h:
                    break
                bot.send_message(chat_id, f"💥 Новая транзакция!\n🔹 From: {tx['from']}\n🔹 To: {tx['to']}\nТокен: {tx['token']}\nКоличество: {tx['amount']}\n💰 Amount: {tx['amount']} {tx['token']}")
                last_hash[chat_id] = h
        time.sleep(60)

threading.Thread(target=poll_new_transactions, daemon=True).start()

# --- Запуск бота ---
bot.infinity_polling()
