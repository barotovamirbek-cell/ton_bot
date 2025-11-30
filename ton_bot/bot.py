import os
import requests
from telebot import TeleBot, types
from threading import Thread
import time

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

# Хранилище кошельков для каждого чата
wallets = {}
notify_status = {}

TONCENTER_API = "https://toncenter.com/api/v2/"
API_KEY = os.getenv("TONCENTER_API_KEY")  # свой API ключ

MIN_AMOUNT_FILTER = 0.000001  # минимальная сумма для уведомлений


def get_balance(address):
    """Получение баланса TON и токенов."""
    url = f"{TONCENTER_API}getWalletInformation?address={address}&api_key={API_KEY}"
    r = requests.get(url).json()
    if r.get("ok"):
        result = r["result"]
        balances = {"TON": float(result.get("balance", 0)) / 1e9}  # в TON
        for jetton in result.get("jettons", []):
            balances[jetton["name"]] = float(jetton["balance"])
        return balances
    else:
        return None


def get_transactions(address):
    """Получение истории транзакций."""
    url = f"{TONCENTER_API}getTransactions?address={address}&api_key={API_KEY}"
    r = requests.get(url).json()
    if r.get("ok"):
        tx_list = []
        for tx in r["result"]:
            if float(tx.get("amount", 0)) < MIN_AMOUNT_FILTER:
                continue
            tx_list.append({
                "hash": tx.get("hash"),
                "from": tx.get("source"),
                "to": tx.get("destination"),
                "amount": float(tx.get("amount", 0)),
                "token": tx.get("token_name", "TON")
            })
        return tx_list
    else:
        return []


def check_new_transactions(chat_id):
    """Проверка новых транзакций и уведомления."""
    last_hashes = set()
    while True:
        if chat_id in wallets and notify_status.get(chat_id, False):
            wallet = wallets[chat_id]
            txs = get_transactions(wallet)
            for tx in txs:
                if tx["hash"] not in last_hashes:
                    last_hashes.add(tx["hash"])
                    msg = (
                        f"💥 Новая транзакция!\n"
                        f"🔹 From: {tx['from']}\n"
                        f"🔹 To: {tx['to']}\n"
                        f"Токен: {tx['token']}\n"
                        f"Количество: {tx['amount']}\n"
                        f"💰 Amount: {tx['amount']} {tx['token']}"
                    )
                    bot.send_message(chat_id, msg)
        time.sleep(10)


@bot.message_handler(commands=["start"])
def start(message):
    chat_id = message.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("/setwallet", "/balance", "/transactions", "/notify_on", "/notify_off")
    bot.send_message(chat_id, "Привет! Настрой свой кошелек TON:", reply_markup=markup)


@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    chat_id = message.chat.id
    args = message.text.split()
    if len(args) != 2:
        bot.send_message(chat_id, "Использование: /setwallet <адрес кошелька>")
        return
    wallets[chat_id] = args[1]
    notify_status[chat_id] = True
    bot.send_message(chat_id, f"Кошелек {args[1]} установлен!")
    # Запуск потока уведомлений
    Thread(target=check_new_transactions, args=(chat_id,), daemon=True).start()


@bot.message_handler(commands=["balance"])
def show_balance(message):
    chat_id = message.chat.id
    if chat_id not in wallets:
        bot.send_message(chat_id, "Кошелек не установлен! Используй /setwallet")
        return
    balances = get_balance(wallets[chat_id])
    if not balances:
        bot.send_message(chat_id, "Баланс недоступен")
        return
    msg = f"💰 Баланс кошелька {wallets[chat_id]} 💰\n\n"
    for token, amount in balances.items():
        msg += f"{token}: {amount}\n"
    bot.send_message(chat_id, msg)


@bot.message_handler(commands=["transactions"])
def show_transactions(message):
    chat_id = message.chat.id
    if chat_id not in wallets:
        bot.send_message(chat_id, "Кошелек не установлен! Используй /setwallet")
        return
    txs = get_transactions(wallets[chat_id])
    if not txs:
        bot.send_message(chat_id, "Транзакций нет")
        return
    msg = ""
    for i, tx in enumerate(txs, start=1):
        msg += (
            f"{i}. 📝 Hash: {tx['hash']}\n"
            f"   🔹 From: {tx['from']}\n"
            f"   🔹 To: {tx['to']}\n"
            f"   Токен: {tx['token']}\n"
            f"   Количество: {tx['amount']}\n\n"
        )
    bot.send_message(chat_id, msg)


@bot.message_handler(commands=["notify_on"])
def notify_on(message):
    chat_id = message.chat.id
    notify_status[chat_id] = True
    bot.send_message(chat_id, "Уведомления включены!")


@bot.message_handler(commands=["notify_off"])
def notify_off(message):
    chat_id = message.chat.id
    notify_status[chat_id] = False
    bot.send_message(chat_id, "Уведомления выключены!")


bot.infinity_polling()
