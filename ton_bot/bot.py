import os
import time
import requests
from threading import Thread
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

TONCENTER_API = "https://toncenter.com/api/v2"
wallets = {}  # user_id -> wallet
notifications = {}  # user_id -> bool
TOKEN_EMOJI = {"TON": "💰"}  # можно добавить другие токены
last_txs = {}  # user_id -> set of last seen hashes

CHECK_INTERVAL = 20  # секунд между проверкой новых транзакций

def format_amount(amount):
    return f"{amount:.8f}".rstrip("0").rstrip(".") if amount else "0"

def get_balance(wallet):
    res = requests.get(f"{TONCENTER_API}/getWalletInformation", params={"wallet": wallet})
    data = res.json()
    balances = []
    if data.get("ok"):
        ton_balance = int(data["result"].get("balance", 0)) / 1e9
        balances.append(f"🔹 TON: {format_amount(ton_balance)}")
        for t in data["result"].get("tokens", []):
            balances.append(f"🔹 {t['name']}: {format_amount(float(t.get('balance', 0)))}")
    else:
        balances.append("Баланс недоступен")
    return "\n".join(balances)

def get_transactions(wallet):
    res = requests.get(f"{TONCENTER_API}/getTransactions", params={"wallet": wallet})
    data = res.json()
    txs = []
    if data.get("ok"):
        for tx in data.get("result", {}).get("transactions", []):
            token = tx.get("token", "TON")
            amount = float(tx.get("amount", 0))
            # фильтруем слишком мелкие транзакции
            if token == "TON" and amount < 0.000001:
                continue
            elif token != "TON" and amount < 0.01:
                continue
            txs.append({
                "hash": tx.get("hash", ""),
                "from": tx.get("from", ""),
                "to": tx.get("to", ""),
                "token": token,
                "amount": amount
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

@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    if user_id not in wallets:
        wallets[user_id] = ""
    notifications[user_id] = True
    last_txs[user_id] = set()
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("Вкл уведомления", "Выкл уведомления")
    markup.row("Показ баланса", "История транзакций")
    bot.send_message(message.chat.id, "Бот запущен!", reply_markup=markup)

@bot.message_handler(commands=["setwallet"])
def set_wallet(message):
    user_id = message.from_user.id
    parts = message.text.split()
    if len(parts) == 2:
        wallets[user_id] = parts[1]
        last_txs[user_id] = set()
        bot.send_message(message.chat.id, f"Адрес кошелька установлен: {parts[1]}")
    else:
        bot.send_message(message.chat.id, "Используйте: /setwallet <адрес>")

@bot.message_handler(func=lambda m: True)
def main_buttons(message):
    user_id = message.from_user.id
    wallet = wallets.get(user_id)
    if message.text == "Вкл уведомления":
        notifications[user_id] = True
        bot.send_message(message.chat.id, "Уведомления включены")
    elif message.text == "Выкл уведомления":
        notifications[user_id] = False
        bot.send_message(message.chat.id, "Уведомления выключены")
    elif message.text == "Показ баланса":
        if not wallet:
            bot.send_message(message.chat.id, "Сначала установите кошелек: /setwallet <адрес>")
            return
        bot.send_message(message.chat.id, get_balance(wallet))
    elif message.text == "История транзакций":
        if not wallet:
            bot.send_message(message.chat.id, "Сначала установите кошелек: /setwallet <адрес>")
            return
        txs = get_transactions(wallet)
        bot.send_message(message.chat.id, format_transactions(txs))

def monitor_transactions():
    while True:
        for user_id, wallet in wallets.items():
            if not wallet or not notifications.get(user_id, True):
                continue
            txs = get_transactions(wallet)
            new_txs = [tx for tx in txs if tx['hash'] not in last_txs.get(user_id, set())]
            for tx in new_txs:
                last_txs[user_id].add(tx['hash'])
                emoji = TOKEN_EMOJI.get(tx['token'], "⚪")
                msg = (f"💥 Новая транзакция!\n"
                       f"🔹 From: {tx['from']}\n"
                       f"🔹 To: {tx['to']}\n"
                       f"{emoji} Токен: {tx['token']}\n"
                       f"Количество: {format_amount(tx['amount'])}\n"
                       f"💰 Amount: {format_amount(tx['amount'])} TON")
                bot.send_message(user_id, msg)
        time.sleep(CHECK_INTERVAL)

# запуск мониторинга в отдельном потоке
Thread(target=monitor_transactions, daemon=True).start()

bot.infinity_polling()
