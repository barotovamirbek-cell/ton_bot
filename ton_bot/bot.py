import os
import time
import threading
import requests
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

TONCENTER_API = "https://toncenter.com/api/v2"
MIN_AMOUNT = 0.000001  # фильтруем слишком мелкие транзакции

# Словарь кошельков по chat_id
wallets = {}

# Для отслеживания последних tx для каждого кошелька
last_tx_hash = {}

# ====== Работа с API ======
def get_balance(address):
    balances = {}
    try:
        resp = requests.get(f"{TONCENTER_API}/getAddressInfo?address={address}", timeout=10)
        if resp.status_code == 200 and resp.text:
            data = resp.json()
            ton = int(data.get("result", {}).get("balance", 0)) / 1e9
            balances["TON"] = ton
        else:
            balances["TON"] = 0
    except Exception as e:
        print(f"Ошибка TON: {e}")
        balances["TON"] = 0

    try:
        resp = requests.get(f"{TONCENTER_API}/getJettons?address={address}", timeout=10)
        if resp.status_code == 200 and resp.text:
            data = resp.json()
            for jet in data.get("result", []):
                name = jet.get("name", "Unknown")
                amount = float(jet.get("balance", 0))
                balances[name] = amount
    except Exception as e:
        print(f"Ошибка Jettons: {e}")

    return balances

def get_transactions(address):
    tx_list = []
    try:
        resp = requests.get(f"{TONCENTER_API}/getTransactions?address={address}&limit=50", timeout=10)
        if resp.status_code == 200 and resp.text:
            data = resp.json()
            for tx in data.get("result", []):
                amount = int(tx.get("amount", 0)) / 1e9
                if amount >= MIN_AMOUNT:
                    tx_list.append({
                        "hash": tx.get("hash", ""),
                        "from": tx.get("from", ""),
                        "to": tx.get("to", ""),
                        "amount": amount,
                        "token": "TON"
                    })
                for jt in tx.get("jettons", []):
                    if float(jt.get("amount", 0)) >= MIN_AMOUNT:
                        tx_list.append({
                            "hash": tx.get("hash", ""),
                            "from": tx.get("from", ""),
                            "to": tx.get("to", ""),
                            "amount": float(jt.get("amount", 0)),
                            "token": jt.get("name", "Unknown")
                        })
    except Exception as e:
        print(f"Ошибка транзакций: {e}")
    return tx_list

# ====== Команды бота ======
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Показать баланс", "История транзакций", "Установить кошелек")
    bot.send_message(chat_id, "Привет! Выбери команду:", reply_markup=keyboard)

@bot.message_handler(func=lambda m: m.text == "Установить кошелек")
def set_wallet(message):
    chat_id = message.chat.id
    bot.send_message(chat_id, "Отправь адрес кошелька TON:")
    bot.register_next_step_handler(message, save_wallet)

def save_wallet(message):
    chat_id = message.chat.id
    wallets[chat_id] = message.text.strip()
    last_tx_hash[chat_id] = set()
    bot.send_message(chat_id, f"Кошелек установлен: {wallets[chat_id]}")

@bot.message_handler(func=lambda m: m.text == "Показать баланс")
def show_balance(message):
    chat_id = message.chat.id
    if chat_id not in wallets:
        bot.send_message(chat_id, "Сначала установите кошелек!")
        return
    address = wallets[chat_id]
    balances = get_balance(address)
    text = f"💰 Баланс кошелька {address} 💰\n\n"
    for token, amount in balances.items():
        text += f"{token}: {amount}\n"
    bot.send_message(chat_id, text)

@bot.message_handler(func=lambda m: m.text == "История транзакций")
def show_transactions(message):
    chat_id = message.chat.id
    if chat_id not in wallets:
        bot.send_message(chat_id, "Сначала установите кошелек!")
        return
    address = wallets[chat_id]
    txs = get_transactions(address)
    if not txs:
        bot.send_message(chat_id, "Транзакций нет")
        return
    text = ""
    for i, tx in enumerate(txs, 1):
        text += f"{i}. 🔹 From: {tx['from']}\n"
        text += f"   🔹 To: {tx['to']}\n"
        text += f"   Токен: {tx['token']}\n"
        text += f"   Количество: {tx['amount']}\n\n"
    bot.send_message(chat_id, text)

# ====== Уведомления о новых транзакциях ======
def notify_new_transactions():
    while True:
        for chat_id, address in wallets.items():
            txs = get_transactions(address)
            for tx in txs:
                if tx["hash"] not in last_tx_hash.get(chat_id, set()):
                    last_tx_hash[chat_id].add(tx["hash"])
                    text = f"💥 Новая транзакция!\n🔹 From: {tx['from']}\n🔹 To: {tx['to']}\n"
                    text += f"Токен: {tx['token']}\nКоличество: {tx['amount']}\n💰 Amount: {tx['amount']} {tx['token']}\n"
                    bot.send_message(chat_id, text)
        time.sleep(20)  # проверка каждые 20 секунд

# ====== Запуск уведомлений в отдельном потоке ======
threading.Thread(target=notify_new_transactions, daemon=True).start()

# ====== Запуск бота ======
bot.infinity_polling()
