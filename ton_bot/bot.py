import os
import time
from threading import Thread
from tontools import TonCenterClient, Wallet, Jetton
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

client = TonCenterClient()

users = {}  # chat_id -> {"wallet": <address>, "notify": True}

# --------------------- ФУНКЦИИ ---------------------

def get_full_balance(address):
    wallet = Wallet(provider=client, address=address)
    wallet.update()
    
    balances = {"TON": wallet.balance}

    # Получаем все jettons кошелька
    jettons = wallet.jettons()
    for jet in jettons:
        jet_obj = Jetton(jet.master, provider=client)
        jw = jet_obj.get_jetton_wallet(address)
        jw.update()
        amt = jw.balance / (10 ** jet_obj.decimals)
        if amt > 0:
            balances[jet_obj.symbol] = amt

    return balances

def format_balance(balances):
    text = ""
    for token, amount in balances.items():
        text += f"🔹 {token}: {amount}\n"
    return text

def get_recent_transactions(address, limit=10, min_amount=0.000001):
    wallet = Wallet(provider=client, address=address)
    wallet.update()
    txs = wallet.transactions(limit=limit)
    filtered = []
    for tx in txs:
        if tx.amount < min_amount and tx.token == "TON":
            continue
        filtered.append(tx)
    return filtered

def format_transactions(txs):
    text = ""
    for i, tx in enumerate(txs, 1):
        text += f"{i}. 💥 Новая транзакция!\n"
        text += f"   🔹 From: {tx.from_address}\n"
        text += f"   🔹 To: {tx.to_address}\n"
        text += f"   Токен: {tx.token}\n"
        text += f"   Количество: {tx.amount}\n\n"
    return text

# --------------------- КОМАНДЫ ---------------------

@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    users.setdefault(chat_id, {"wallet": None, "notify": True})
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("💰 Баланс", "📜 История")
    markup.row("⚡ Вкл уведомления", "❌ Выкл уведомления")
    bot.send_message(chat_id, "Привет! Установи кошелек командой /setwallet", reply_markup=markup)

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    chat_id = message.chat.id
    msg = message.text.split()
    if len(msg) < 2:
        bot.send_message(chat_id, "Используй: /setwallet <адрес>")
        return
    address = msg[1]
    users.setdefault(chat_id, {})["wallet"] = address
    bot.send_message(chat_id, f"Кошелек установлен: {address}")

@bot.message_handler(commands=["balance"])
def show_balance(message):
    chat_id = message.chat.id
    user = users.get(chat_id)
    if not user or not user.get("wallet"):
        bot.send_message(chat_id, "Сначала установи кошелек через /setwallet")
        return
    balances = get_full_balance(user["wallet"])
    text = f"💰 Баланс кошелька {user['wallet']} 💰\n\n"
    text += format_balance(balances)
    bot.send_message(chat_id, text)

@bot.message_handler(commands=["transactions"])
def show_transactions(message):
    chat_id = message.chat.id
    user = users.get(chat_id)
    if not user or not user.get("wallet"):
        bot.send_message(chat_id, "Сначала установи кошелек через /setwallet")
        return
    txs = get_recent_transactions(user["wallet"])
    if not txs:
        bot.send_message(chat_id, "Транзакций нет")
        return
    text = format_transactions(txs)
    bot.send_message(chat_id, text)

# --------------------- КНОПКИ ---------------------

@bot.message_handler(func=lambda m: True)
def handle_buttons(message):
    chat_id = message.chat.id
    user = users.get(chat_id, {})
    text = message.text
    if text == "💰 Баланс":
        show_balance(message)
    elif text == "📜 История":
        show_transactions(message)
    elif text == "⚡ Вкл уведомления":
        user["notify"] = True
        bot.send_message(chat_id, "Уведомления включены")
    elif text == "❌ Выкл уведомления":
        user["notify"] = False
        bot.send_message(chat_id, "Уведомления выключены")

# --------------------- УВЕДОМЛЕНИЯ ---------------------

def tx_checker():
    last_seen = {}
    while True:
        for chat_id, user in users.items():
            if not user.get("wallet") or not user.get("notify"):
                continue
            txs = get_recent_transactions(user["wallet"])
            for tx in txs[::-1]:  # показываем от старых к новым
                tx_id = tx.hash
                if last_seen.get(chat_id) == tx_id:
                    break
                text = f"💥 Новая транзакция!\n🔹 From: {tx.from_address}\n🔹 To: {tx.to_address}\nТокен: {tx.token}\nКоличество: {tx.amount}\n"
                bot.send_message(chat_id, text)
                last_seen[chat_id] = tx_id
        time.sleep(15)

Thread(target=tx_checker, daemon=True).start()

# --------------------- START ---------------------
bot.infinity_polling()
