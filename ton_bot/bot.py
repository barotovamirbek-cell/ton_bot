import os
import time
import threading
import requests
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

# Для хранения кошельков и статуса уведомлений
wallets = {}          # chat_id -> wallet_address
notify_status = {}    # chat_id -> True/False
last_tx_hash = {}     # chat_id -> hash последней транзакции
MIN_AMOUNT = 0.000001  # минимальная сумма для уведомлений

TONCENTER_API = "https://toncenter.com/api/v2"

# --- Функции для работы с TonCenter API ---
def get_balance(wallet):
    try:
        r = requests.get(f"{TONCENTER_API}/getAddressInfo?address={wallet}")
        data = r.json()
        if data["ok"]:
            balance = int(data["result"]["balance"]) / 1e9
            jettons = {}
            for jt in data["result"].get("jettons", []):
                name = jt.get("name", "UNKNOWN")
                amount = int(jt.get("balance", 0)) / (10 ** int(jt.get("decimals", 0)))
                jettons[name] = amount
            return balance, jettons
        else:
            return None, {}
    except:
        return None, {}

def get_transactions(wallet):
    try:
        r = requests.get(f"{TONCENTER_API}/getTransactions?address={wallet}&limit=20")
        data = r.json()
        txs = []
        if data["ok"]:
            for tx in data["result"]:
                amt = int(tx.get("amount", 0)) / 1e9
                if amt < MIN_AMOUNT:
                    continue
                txs.append({
                    "hash": tx.get("id", ""),
                    "from": tx.get("from", ""),
                    "to": tx.get("to", ""),
                    "token": "TON",
                    "amount": amt
                })
        return txs
    except:
        return []

# --- Мониторинг новых транзакций ---
CHECK_INTERVAL = 20
def monitor_wallets():
    while True:
        for chat_id, wallet in wallets.items():
            if not notify_status.get(chat_id, True):
                continue
            txs = get_transactions(wallet)
            if not txs:
                continue
            new_txs = []
            for tx in txs:
                if last_tx_hash.get(chat_id) == tx["hash"]:
                    break
                new_txs.append(tx)
            if new_txs:
                new_txs.reverse()
                for tx in new_txs:
                    text = (f"💥 Новая транзакция!\n"
                            f"🔹 From: {tx['from']}\n"
                            f"🔹 To: {tx['to']}\n"
                            f"Токен: {tx['token']}\n"
                            f"Количество: {tx['amount']}\n"
                            f"💰 Amount: {tx['amount']} {tx['token']}")
                    bot.send_message(chat_id, text)
                last_tx_hash[chat_id] = new_txs[0]["hash"]
        time.sleep(CHECK_INTERVAL)

threading.Thread(target=monitor_wallets, daemon=True).start()

# --- Команды бота ---
@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    wallets.setdefault(chat_id, "")
    notify_status.setdefault(chat_id, True)
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("Вкл уведомления", "Выкл уведомления", "Показ баланса", "История транзакций")
    bot.send_message(chat_id, "Привет! Я Шакалинг кошелька 2.0\nУстановите кошелек через /setwallet", reply_markup=markup)

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    chat_id = message.chat.id
    text = message.text.split()
    if len(text) < 2:
        bot.send_message(chat_id, "Используй: /setwallet <адрес_кошелька>")
        return
    wallets[chat_id] = text[1]
    bot.send_message(chat_id, f"Кошелек установлен: {text[1]}")

@bot.message_handler(lambda m: m.text.lower() == "вкл уведомления")
def enable_notify(message):
    chat_id = message.chat.id
    notify_status[chat_id] = True
    bot.send_message(chat_id, "Уведомления включены ✅")

@bot.message_handler(lambda m: m.text.lower() == "выкл уведомления")
def disable_notify(message):
    chat_id = message.chat.id
    notify_status[chat_id] = False
    bot.send_message(chat_id, "Уведомления выключены ❌")

@bot.message_handler(lambda m: m.text.lower() == "показ баланса")
def show_balance(message):
    chat_id = message.chat.id
    wallet = wallets.get(chat_id)
    if not wallet:
        bot.send_message(chat_id, "Кошелек не установлен")
        return
    ton_balance, jettons = get_balance(wallet)
    text = f"💰 Баланс кошелька {wallet} 💰\n\n"
    if ton_balance is None:
        text += "Баланс недоступен"
    else:
        text += f"🔹 TON: {ton_balance}\n"
        for token, amt in jettons.items():
            text += f"🔹 {token}: {amt}\n"
    bot.send_message(chat_id, text)

@bot.message_handler(lambda m: m.text.lower() == "история транзакций")
def show_transactions(message):
    chat_id = message.chat.id
    wallet = wallets.get(chat_id)
    if not wallet:
        bot.send_message(chat_id, "Кошелек не установлен")
        return
    txs = get_transactions(wallet)
    if not txs:
        bot.send_message(chat_id, "Транзакций нет")
        return
    text = ""
    for i, tx in enumerate(txs, 1):
        text += (f"{i}. 📝 Hash: {tx['hash']}\n"
                 f"   🔹 From: {tx['from']}\n"
                 f"   🔹 To: {tx['to']}\n"
                 f"   Токен: {tx['token']}\n"
                 f"   Количество: {tx['amount']}\n"
                 f"   💰 Amount: {tx['amount']} {tx['token']}\n\n")
    bot.send_message(chat_id, text)

# --- Запуск бота ---
bot.infinity_polling()
