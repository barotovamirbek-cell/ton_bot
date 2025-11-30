import os
import requests
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

# Хранилище пользователей: {user_id: {"wallet": "...", "notify": True}}
users = {}

# API Toncenter
TON_API = "https://toncenter.com/api/v2"

def get_wallet_balance(wallet):
    url = f"{TON_API}/getAccount?account={wallet}"
    r = requests.get(url).json()
    if not r.get("ok"):
        return "Баланс недоступен"
    result = r["result"]

    balances = []

    # Основной TON
    ton_amount = int(result.get("balance", 0)) / 10**9
    balances.append(f"TON: {ton_amount}")

    # Все токены
    for token in result.get("fungible_tokens", []):
        token_name = token.get("name") or token.get("symbol") or "Unknown"
        decimals = int(token.get("decimals", 0)) if token.get("decimals") else 0
        amount = int(token.get("balance", 0)) / (10**decimals if decimals else 1)
        balances.append(f"{token_name}: {amount}")

    return "\n".join(balances)

def get_transactions(wallet):
    url = f"{TON_API}/getTransactions?account={wallet}&limit=20"
    r = requests.get(url).json()
    if not r.get("ok"):
        return []

    txs_list = []

    for tx in r["result"]["transactions"]:
        # Основной TON
        amount = int(tx.get("in_msg", {}).get("value", 0)) / 10**9
        txs_list.append({
            "hash": tx.get("id", ""),
            "from": tx.get("in_msg", {}).get("source", ""),
            "to": tx.get("out_msgs", [{}])[0].get("destination", ""),
            "token": "TON",
            "amount": amount
        })

        # Токены
        for ftoken in tx.get("in_msg", {}).get("fungible_tokens", []):
            name = ftoken.get("name") or ftoken.get("symbol") or "Unknown"
            decimals = int(ftoken.get("decimals", 0)) if ftoken.get("decimals") else 0
            amt = int(ftoken.get("amount", 0)) / (10**decimals if decimals else 1)
            txs_list.append({
                "hash": tx.get("id", ""),
                "from": tx.get("in_msg", {}).get("source", ""),
                "to": tx.get("out_msgs", [{}])[0].get("destination", ""),
                "token": name,
                "amount": amt
            })

    return txs_list

# ====== КОМАНДЫ ======

@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    if user_id not in users:
        users[user_id] = {"wallet": "", "notify": True}
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("/setwallet", "/balance")
    markup.row("/transactions", "/toggle_notify")
    bot.send_message(message.chat.id, "Бот запущен. Настройте кошелек командой /setwallet", reply_markup=markup)

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    user_id = message.from_user.id
    text = message.text.split()
    if len(text) != 2:
        bot.reply_to(message, "Используй: /setwallet <адрес>")
        return
    wallet = text[1]
    users[user_id]["wallet"] = wallet
    bot.reply_to(message, f"Кошелек установлен: {wallet}")

@bot.message_handler(commands=["balance"])
def show_balance(message):
    user_id = message.from_user.id
    wallet = users.get(user_id, {}).get("wallet")
    if not wallet:
        bot.reply_to(message, "Кошелек не установлен")
        return
    bal = get_wallet_balance(wallet)
    bot.reply_to(message, f"💰 Баланс кошелька {wallet} 💰\n\n{bal}")

@bot.message_handler(commands=["transactions"])
def show_transactions(message):
    user_id = message.from_user.id
    wallet = users.get(user_id, {}).get("wallet")
    if not wallet:
        bot.reply_to(message, "Кошелек не установлен")
        return
    txs = get_transactions(wallet)
    if not txs:
        bot.reply_to(message, "Транзакций нет")
        return
    msg = ""
    for i, tx in enumerate(txs, start=1):
        msg += (f"{i}. 📝 Hash: {tx['hash']}\n"
                f"   🔹 From: {tx['from']}\n"
                f"   🔹 To: {tx['to']}\n"
                f"   Токен: {tx['token']}\n"
                f"   Количество: {tx['amount']}\n\n")
    bot.reply_to(message, msg)

@bot.message_handler(commands=["toggle_notify"])
def toggle_notify(message):
    user_id = message.from_user.id
    users[user_id]["notify"] = not users[user_id]["notify"]
    status = "включены" if users[user_id]["notify"] else "выключены"
    bot.reply_to(message, f"Уведомления {status}")

# ====== ПУЛЛИНГ ======
bot.infinity_polling()
