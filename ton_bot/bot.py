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

TOKEN_EMOJI = {
    "TON": "💎",
    "USDT": "🟢",
    "BTC": "🟡",
    "ETH": "🔵"
}

def format_amount(amount):
    if amount == int(amount):
        return str(int(amount))
    return f"{amount:.9f}".rstrip('0').rstrip('.')

def get_wallet_info(wallet):
    res = requests.get(f"{TONCENTER_API}/getWalletInformation", params={"address": wallet})
    data = res.json()
    return data.get("result", {}) if data.get("ok") else {}

def get_balance(wallet):
    info = get_wallet_info(wallet)
    balances = []

    # TON
    ton_balance = int(info.get("balance", 0)) / 1e9
    balances.append({"token": "TON", "amount": ton_balance})

    # Токены
    for token in info.get("tokens", []):
        balances.append({
            "token": token.get("name", "UNKNOWN"),
            "amount": float(token.get("balance", 0))
        })
    return balances

def get_transactions(wallet):
    res = requests.get(f"{TONCENTER_API}/getTransactions", params={"address": wallet, "limit": 50})
    data = res.json()
    txs = []
    if data.get("ok"):
        for tx in data["result"]:
            # TON транзакции
            in_msg = tx.get("in_msg", {})
            out_msgs = tx.get("out_msgs", [])
            if in_msg:
                amount = int(in_msg.get("value", 0)) / 1e9
                txs.append({
                    "hash": tx.get("hash", ""),
                    "from": in_msg.get("source", ""),
                    "to": out_msgs[0].get("destination", "") if out_msgs else "",
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

def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("💰 Баланс", "📝 История транзакций")
    keyboard.row("🔔 Включить уведомления", "🔕 Выключить уведомления")
    return keyboard

def format_balance(balance):
    msg = ""
    for b in balance:
        emoji = TOKEN_EMOJI.get(b['token'], "⚪")
        msg += f"{emoji} {b['token']}: {format_amount(b['amount'])}\n"
    return msg

def format_transactions(txs):
    msg = ""
    for i, tx in enumerate(txs, 1):
        emoji = TOKEN_EMOJI.get(tx['token'], "⚪")
        msg += f"{i}. 📝 Hash: {tx['hash']}\n"
        msg += f"   🔹 From: {tx['from']}\n"
        msg += f"   🔹 To: {tx['to']}\n"
        msg += f"   {emoji} Токен: {tx['token']}\n"
        msg += f"   Количество: {format_amount(tx['amount'])}\n\n"
    return msg

def format_new_tx(tx):
    emoji = TOKEN_EMOJI.get(tx['token'], "⚪")
    return f"💥 Новая транзакция!\n🔹 From: {tx['from']}\n🔹 To: {tx['to']}\n{emoji} Токен: {tx['token']}\nКоличество: {format_amount(tx['amount'])}\n"

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
