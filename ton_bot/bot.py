import os
import requests
import telebot
from telebot import types
from threading import Thread
import time

# === Переменные окружения ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY")  # ключ TonCenter API
MIN_TON = 0.0001  # минимальная сумма TON для уведомлений

bot = telebot.TeleBot(BOT_TOKEN)

# Хранилище кошелька и уведомлений
wallet_address = None
notifications_enabled = False
last_checked_tx = []

# === Кнопки ===
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("/balance", "/transactions")
    kb.add("Вкл уведомления", "Выкл уведомления")
    kb.add("/setwallet")
    return kb

# === Получение баланса и токенов ===
def get_balance(address):
    url = f"https://api.toncenter.com/api/v2/getAddressInformation?address={address}&api_key={TONCENTER_API_KEY}"
    resp = requests.get(url).json()
    if not resp.get("ok"):
        return None
    result = resp["result"]
    balances = []
    # TON
    ton_balance = int(result["balance"]) / 1e9
    balances.append({"token": "TON", "amount": ton_balance})
    # Jettons
    if "jettons" in result:
        for jt in result["jettons"]:
            balances.append({"token": jt.get("name", "Unknown"), "amount": float(jt.get("balance", 0))})
    return balances

# === Получение транзакций ===
def get_transactions(address):
    url = f"https://api.toncenter.com/api/v2/getTransactions?address={address}&api_key={TONCENTER_API_KEY}"
    resp = requests.get(url).json()
    if not resp.get("ok"):
        return []
    txs = []
    for tx in resp["result"]:
        # Пропускаем слишком маленькие суммы TON
        amount = int(tx.get("value", 0)) / 1e9
        if amount < MIN_TON:
            continue
        txs.append({
            "hash": tx.get("hash"),
            "from": tx.get("source"),
            "to": tx.get("destination"),
            "amount": amount,
            "token": "TON"  # базовый TON, позже можно расширить для jettons
        })
    return txs

# === Уведомления о новых транзакциях ===
def transaction_watcher():
    global last_checked_tx
    while True:
        if wallet_address and notifications_enabled:
            txs = get_transactions(wallet_address)
            for tx in txs:
                if tx["hash"] not in last_checked_tx:
                    last_checked_tx.append(tx["hash"])
                    message = f"💥 Новая транзакция!\n" \
                              f"🔹 From: {tx['from']}\n" \
                              f"🔹 To: {tx['to']}\n" \
                              f"Токен: {tx['token']}\n" \
                              f"Количество: {tx['amount']}\n"
                    bot.send_message(chat_id=chat_id, text=message)
            # Ограничим размер списка последних tx
            if len(last_checked_tx) > 100:
                last_checked_tx = last_checked_tx[-50:]
        time.sleep(10)

# === Команды ===
@bot.message_handler(commands=["start"])
def start(message):
    global chat_id
    chat_id = message.chat.id
    bot.send_message(chat_id, "Привет! Это Шакалинг кошелька 2.0", reply_markup=main_keyboard())

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    global wallet_address
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "Использование: /setwallet <адрес_кошелька>")
        return
    wallet_address = parts[1]
    bot.send_message(message.chat.id, f"Кошелек установлен: {wallet_address}")

@bot.message_handler(commands=["balance"])
def balance(message):
    if not wallet_address:
        bot.send_message(message.chat.id, "Кошелек не установлен!")
        return
    balances = get_balance(wallet_address)
    if not balances:
        bot.send_message(message.chat.id, "Баланс недоступен")
        return
    msg = f"💰 Баланс кошелька {wallet_address} 💰\n\n"
    for b in balances:
        msg += f"🔹 {b['token']}: {b['amount']}\n"
    bot.send_message(message.chat.id, msg)

@bot.message_handler(commands=["transactions"])
def transactions(message):
    if not wallet_address:
        bot.send_message(message.chat.id, "Кошелек не установлен!")
        return
    txs = get_transactions(wallet_address)
    if not txs:
        bot.send_message(message.chat.id, "Транзакций нет")
        return
    msg = ""
    for i, tx in enumerate(txs, start=1):
        msg += f"{i}. 📝 Hash: {tx['hash']}\n" \
               f"   🔹 From: {tx['from']}\n" \
               f"   🔹 To: {tx['to']}\n" \
               f"   Токен: {tx['token']}\n" \
               f"   Количество: {tx['amount']}\n\n"
    bot.send_message(message.chat.id, msg)

# === Вкл/выкл уведомлений ===
@bot.message_handler(func=lambda m: m.text == "Вкл уведомления")
def enable_notifications(message):
    global notifications_enabled
    notifications_enabled = True
    bot.send_message(message.chat.id, "Уведомления включены")

@bot.message_handler(func=lambda m: m.text == "Выкл уведомления")
def disable_notifications(message):
    global notifications_enabled
    notifications_enabled = False
    bot.send_message(message.chat.id, "Уведомления выключены")

# === Запуск watcher в отдельном потоке ===
watcher_thread = Thread(target=transaction_watcher, daemon=True)
watcher_thread.start()

# === Запуск бота ===
bot.infinity_polling()
