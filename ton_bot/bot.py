import os
import requests
import threading
import time
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

bot = TeleBot(BOT_TOKEN)
TON_API = "https://toncenter.com/api/v2"

users = {}  # {chat_id: {"wallet": "", "notify": True, "last_tx_hash": ""}}

def format_amount(amount):
    return f"{amount:.9f}".rstrip("0").rstrip(".") or "0"

def get_wallet_balance(wallet):
    try:
        r = requests.get(f"{TON_API}/getAccount?account={wallet}").json()
        if not r.get("ok"):
            return "Баланс недоступен"
        result = r.get("result", {})
        balance = int(result.get("balance", 0)) / 1e9
        tokens = result.get("fungible_tokens", [])
        token_strs = [f"🔹 {token['name']}: {format_amount(int(token['balance']) / 10**token['decimals'])}" for token in tokens]
        token_strs.insert(0, f"🔹 TON: {format_amount(balance)}")
        return "\n".join(token_strs)
    except:
        return "Баланс недоступен"

def get_transactions(wallet, limit=20):
    try:
        r = requests.get(f"{TON_API}/getTransactions?account={wallet}&limit={limit}").json()
        if not r.get("ok"):
            return []

        txs = []
        for tx in r["result"].get("transactions", []):
            in_msg = tx.get("in_msg", {})
            amount = int(in_msg.get("value", 0)) / 1e9
            txs.append({
                "hash": in_msg.get("hash", ""),
                "from": in_msg.get("source", ""),
                "to": in_msg.get("destination", ""),
                "token": "TON",
                "amount": format_amount(amount)
            })
            for tok in in_msg.get("fungible_tokens", []):
                amount_tok = int(tok.get("balance", 0)) / 10**tok.get("decimals", 9)
                txs.append({
                    "hash": in_msg.get("hash", ""),
                    "from": in_msg.get("source", ""),
                    "to": in_msg.get("destination", ""),
                    "token": tok.get("name", ""),
                    "amount": format_amount(amount_tok)
                })
        return txs
    except:
        return []

def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("/balance"))
    kb.add(types.KeyboardButton("/transactions"))
    kb.add(types.KeyboardButton("/toggle_notify"))
    return kb

@bot.message_handler(commands=["start"])
def start(message):
    users[message.chat.id] = users.get(message.chat.id, {"wallet": "", "notify": True, "last_tx_hash": ""})
    bot.send_message(message.chat.id, "Привет! Я слежу за твоим TON кошельком.", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "Используй: /setwallet <адрес_кошелька>")
        return
    users[message.chat.id]["wallet"] = parts[1]
    bot.reply_to(message, f"Кошелёк установлен: {parts[1]}")

@bot.message_handler(commands=["balance"])
def balance(message):
    wallet = users.get(message.chat.id, {}).get("wallet")
    if not wallet:
        bot.reply_to(message, "Сначала установи кошелёк командой /setwallet")
        return
    bal = get_wallet_balance(wallet)
    bot.send_message(message.chat.id, f"💰 Баланс кошелька {wallet} 💰\n\n{bal}")

@bot.message_handler(commands=["transactions"])
def transactions(message):
    wallet = users.get(message.chat.id, {}).get("wallet")
    if not wallet:
        bot.reply_to(message, "Сначала установи кошелёк командой /setwallet")
        return
    txs = get_transactions(wallet, limit=20)
    if not txs:
        bot.send_message(message.chat.id, "Транзакций нет")
        return
    text = ""
    for i, tx in enumerate(txs, 1):
        text += f"{i}. 📝 Hash: {tx['hash']}\n"
        text += f"   🔹 From: {tx['from']}\n"
        text += f"   🔹 To: {tx['to']}\n"
        text += f"   Токен: {tx['token']}\n"
        text += f"   Количество: {tx['amount']}\n\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["toggle_notify"])
def toggle_notify(message):
    user = users.get(message.chat.id)
    if not user:
        bot.reply_to(message, "Сначала /start")
        return
    user["notify"] = not user.get("notify", True)
    status = "включены" if user["notify"] else "выключены"
    bot.send_message(message.chat.id, f"Уведомления {status}")

# Функция для уведомления о новых транзакциях
def check_new_transactions():
    while True:
        try:
            for chat_id, info in users.items():
                if not info.get("wallet") or not info.get("notify"):
                    continue
                txs = get_transactions(info["wallet"], limit=5)
                for tx in reversed(txs):
                    if tx["hash"] == info.get("last_tx_hash"):
                        break
                    bot.send_message(chat_id,
                                     f"💥 Новая транзакция!\n"
                                     f"🔹 From: {tx['from']}\n"
                                     f"🔹 To: {tx['to']}\n"
                                     f"Токен: {tx['token']}\n"
                                     f"Количество: {tx['amount']}")
                if txs:
                    info["last_tx_hash"] = txs[0]["hash"]
        except Exception as e:
            print("Ошибка в check_new_transactions:", e)
        time.sleep(30)  # проверяем каждые 30 секунд

# Запуск потока для уведомлений
threading.Thread(target=check_new_transactions, daemon=True).start()

bot.infinity_polling()
