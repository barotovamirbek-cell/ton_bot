import os
import requests
import time
from telebot import TeleBot, types
from threading import Thread

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

wallets = {}  # chat_id -> wallet
notify_enabled = {}  # chat_id -> True/False
last_tx_hashes = {}  # chat_id -> set of hashes для уведомлений

MIN_AMOUNT_TON = 0.0001
MIN_AMOUNT_JETTON = 0.01

def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("/start", "/setwallet")
    keyboard.row("/balance", "/transactions")
    keyboard.row("/notify_on", "/notify_off")
    return keyboard

def get_balance(wallet):
    balances = []
    try:
        # TON баланс
        ton_res = requests.get(f"https://tonapi.io/v1/accounts/balance?account={wallet}").json()
        ton_balance = float(ton_res.get("balance", 0)) / 1e9
        if ton_balance >= MIN_AMOUNT_TON:
            balances.append(("TON", ton_balance))

        # Jettons/USDT
        jetton_res = requests.get(f"https://tonapi.io/v1/accounts/jettons?account={wallet}").json()
        for jt in jetton_res.get("jettons", []):
            name = jt.get("name")
            amount = float(jt.get("balance", 0))
            if amount >= MIN_AMOUNT_JETTON:
                balances.append((name, amount))
    except Exception as e:
        print("Error fetching balance:", e)
    return balances

def get_transactions(wallet):
    txs = []
    try:
        tx_res = requests.get(f"https://tonapi.io/v1/accounts/transactions?account={wallet}").json()
        for tx in tx_res.get("transactions", []):
            amount = float(tx.get("amount", 0))
            if amount < MIN_AMOUNT_TON and tx.get("jetton_name") is None:
                continue  # фильтруем мелкие транзакции TON
            txs.append({
                "hash": tx.get("hash"),
                "from": tx.get("from"),
                "to": tx.get("to"),
                "token": tx.get("jetton_name", "TON"),
                "amount": amount
            })
    except Exception as e:
        print("Error fetching transactions:", e)
    return txs

def notify_new_transactions():
    while True:
        for chat_id, wallet in wallets.items():
            if not notify_enabled.get(chat_id):
                continue
            txs = get_transactions(wallet)
            known_hashes = last_tx_hashes.get(chat_id, set())
            for tx in txs:
                if tx["hash"] not in known_hashes:
                    text = (
                        f"💥 Новая транзакция!\n"
                        f"🔹 From: {tx['from']}\n"
                        f"🔹 To: {tx['to']}\n"
                        f"Токен: {tx['token']}\n"
                        f"Количество: {tx['amount']}\n"
                    )
                    bot.send_message(chat_id, text)
                    known_hashes.add(tx["hash"])
            last_tx_hashes[chat_id] = known_hashes
        time.sleep(30)  # проверяем каждые 30 секунд

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "Привет! Выбери команду.",
        reply_markup=get_main_keyboard()
    )

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.send_message(message.chat.id, "Используй: /setwallet <адрес кошелька>")
        return
    wallet_address = args[1]
    wallets[message.chat.id] = wallet_address
    bot.send_message(message.chat.id, f"Кошелек сохранен: {wallet_address}")

@bot.message_handler(commands=["balance"])
def show_balance(message):
    wallet = wallets.get(message.chat.id)
    if not wallet:
        bot.send_message(message.chat.id, "Сначала установите кошелек через /setwallet")
        return
    balances = get_balance(wallet)
    if not balances:
        bot.send_message(message.chat.id, "Баланс недоступен")
        return
    text = f"💰 Баланс кошелька {wallet} 💰\n\n"
    for name, amount in balances:
        text += f"{name}: {amount}\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["transactions"])
def show_transactions(message):
    wallet = wallets.get(message.chat.id)
    if not wallet:
        bot.send_message(message.chat.id, "Сначала установите кошелек через /setwallet")
        return
    txs = get_transactions(wallet)
    if not txs:
        bot.send_message(message.chat.id, "Транзакций нет")
        return
    text = ""
    for i, tx in enumerate(txs, 1):
        text += (
            f"{i}. 📝 Hash: {tx['hash']}\n"
            f"   🔹 From: {tx['from']}\n"
            f"   🔹 To: {tx['to']}\n"
            f"   Токен: {tx['token']}\n"
            f"   Количество: {tx['amount']}\n\n"
        )
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["notify_on"])
def notify_on(message):
    notify_enabled[message.chat.id] = True
    bot.send_message(message.chat.id, "Уведомления включены!")

@bot.message_handler(commands=["notify_off"])
def notify_off(message):
    notify_enabled[message.chat.id] = False
    bot.send_message(message.chat.id, "Уведомления отключены!")

# Запускаем поток для уведомлений
Thread(target=notify_new_transactions, daemon=True).start()

bot.infinity_polling()
