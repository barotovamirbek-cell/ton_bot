import os
import requests
import threading
import time
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

TONCENTER_API = "https://toncenter.com/api/v2"
wallets = {}  # user_id -> wallet
notifications = {}  # user_id -> True/False
last_tx_hash = {}  # user_id -> last known hash

TOKEN_EMOJI = {"TON": "💰"}  # Можно добавить другие токены

def format_amount(amount):
    return f"{amount:.9f}".rstrip('0').rstrip('.') if amount else "0"

def get_balance(wallet):
    res = requests.get(f"{TONCENTER_API}/getWalletInformation", params={"wallet": wallet})
    data = res.json()
    balance_msg = "Баланс недоступен"
    if data.get("ok"):
        balances = []
        ton_balance = int(data["result"].get("balance", 0)) / 1e9
        balances.append(f"🔹 TON: {format_amount(ton_balance)}")
        for t in data["result"].get("tokens", []):
            balances.append(f"🔹 {t['name']}: {format_amount(float(t['balance']))}")
        balance_msg = "\n".join(balances)
    return balance_msg

def get_transactions(wallet):
    res = requests.get(f"{TONCENTER_API}/getTransactions", params={"address": wallet, "limit": 100})
    data = res.json()
    txs = []
    if data.get("ok") and isinstance(data.get("result"), list):
        for tx in data["result"]:
            # TON транзакции
            in_msg = tx.get("in_msg", {})
            if in_msg:
                amount = int(in_msg.get("value", 0)) / 1e9
                txs.append({
                    "hash": tx.get("hash", ""),
                    "from": in_msg.get("source", ""),
                    "to": in_msg.get("destination", ""),
                    "amount": amount,
                    "token": "TON"
                })
            # Токены
            for t in tx.get("token_balances", []):
                txs.append({
                    "hash": tx.get("hash", ""),
                    "from": t.get("source", ""),
                    "to": t.get("destination", ""),
                    "amount": float(t.get("balance", 0)),
                    "token": t.get("name", "UNKNOWN")
                })
    return txs

def format_transactions(txs):
    if not txs:
        return "Транзакций нет"
    msg = ""
    for i, tx in enumerate(txs, 1):
        emoji = TOKEN_EMOJI.get(tx['token'], "⚪")
        msg += f"{i}. 📝 Hash: {tx['hash']}\n"
        msg += f"   🔹 From: {tx['from']}\n"
        msg += f"   🔹 To: {tx['to']}\n"
        msg += f"   {emoji} Токен: {tx['token']}\n"
        msg += f"   Количество: {format_amount(tx['amount'])}\n\n"
    return msg

def monitor_user(user_id):
    while notifications.get(user_id):
        wallet = wallets.get(user_id)
        if not wallet:
            time.sleep(5)
            continue
        txs = get_transactions(wallet)
        if not txs:
            time.sleep(5)
            continue
        last_hash = last_tx_hash.get(user_id)
        for tx in reversed(txs):
            if tx["hash"] == last_hash:
                break
            msg = f"💥 Новая транзакция!\n🔹 From: {tx['from']}\n🔹 To: {tx['to']}\n💰 Токен: {tx['token']}\nКоличество: {format_amount(tx['amount'])}"
            bot.send_message(user_id, msg)
        last_tx_hash[user_id] = txs[0]["hash"]
        time.sleep(5)

def start_monitor(user_id):
    if notifications.get(user_id):
        return
    notifications[user_id] = True
    threading.Thread(target=monitor_user, args=(user_id,), daemon=True).start()

@bot.message_handler(commands=['start'])
def start(msg):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Вкл уведомления", "Выкл уведомления", "Показать баланс", "История транзакций", "Сменить кошелек")
    bot.send_message(msg.chat.id, "Привет! Выбери действие:", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text)
def handle_buttons(msg):
    user_id = msg.chat.id
    if msg.text == "Вкл уведомления":
        if not wallets.get(user_id):
            bot.send_message(user_id, "Сначала установите кошелек командой /setwallet")
            return
        start_monitor(user_id)
        bot.send_message(user_id, "Уведомления включены ✅")
    elif msg.text == "Выкл уведомления":
        notifications[user_id] = False
        bot.send_message(user_id, "Уведомления выключены ❌")
    elif msg.text == "Показать баланс":
        wallet = wallets.get(user_id)
        if not wallet:
            bot.send_message(user_id, "Сначала установите кошелек командой /setwallet")
            return
        balance = get_balance(wallet)
        bot.send_message(user_id, f"💰 Баланс кошелька {wallet} 💰\n\n{balance}")
    elif msg.text == "История транзакций":
        wallet = wallets.get(user_id)
        if not wallet:
            bot.send_message(user_id, "Сначала установите кошелек командой /setwallet")
            return
        txs = get_transactions(wallet)
        bot.send_message(user_id, format_transactions(txs))
    elif msg.text == "Сменить кошелек":
        bot.send_message(user_id, "Отправьте новый адрес кошелька для установки")
        bot.register_next_step_handler(msg, set_wallet)
    else:
        bot.send_message(user_id, "Неизвестная команда")

def set_wallet(msg):
    user_id = msg.chat.id
    wallets[user_id] = msg.text.strip()
    bot.send_message(user_id, f"Кошелек установлен: {wallets[user_id]}")

if __name__ == "__main__":
    bot.infinity_polling()
