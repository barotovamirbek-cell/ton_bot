import os
import time
import requests
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = TeleBot(BOT_TOKEN)

wallet_address = None
notify_enabled = True
last_tx_hash = set()

TONCENTER_API = "https://toncenter.com/api/v2"

def get_wallet_balance(wallet):
    url = f"{TONCENTER_API}/getAccount?account={wallet}"
    r = requests.get(url).json()
    if not r.get("ok"):
        return "Баланс недоступен"
    result = r["result"]
    balances = []

    # Основной TON
    ton_amount = int(result.get("balance", 0)) / 10**9
    balances.append(f"TON: {ton_amount}")

    # Токены
    for token in result.get("fungible_tokens", []):
        name = token.get("name", "Unknown")
        decimals = int(token.get("decimals", 0))
        amount = int(token.get("balance", 0)) / (10**decimals if decimals else 1)
        balances.append(f"{name}: {amount}")

    return "\n".join(balances)

def get_transactions(wallet, limit=10):
    url = f"{TONCENTER_API}/getTransactions?account={wallet}&limit={limit}"
    r = requests.get(url).json()
    if not r.get("ok"):
        return []
    return r["result"].get("transactions", [])

def format_transaction(tx):
    # Определяем токены
    token_name = "TON"
    amount = int(tx.get("in_msg", {}).get("value", 0)) / 10**9
    if tx.get("in_msg", {}).get("msg_data_type") == "frozen":
        token_name = tx.get("in_msg", {}).get("token", {}).get("name", "Unknown")
        amount = int(tx.get("in_msg", {}).get("token", {}).get("balance", 0))
    return (
        f"📝 Hash: {tx.get('hash')}\n"
        f"🔹 From: {tx.get('source')}\n"
        f"🔹 To: {tx.get('destination')}\n"
        f"Токен: {token_name}\n"
        f"Количество: {amount}\n"
    )

# --- Команды ---
@bot.message_handler(commands=["start"])
def start_message(msg):
    bot.send_message(msg.chat.id, "Привет! Я слежу за TON кошельком.")

@bot.message_handler(commands=["setwallet"])
def set_wallet(msg):
    global wallet_address
    parts = msg.text.split()
    if len(parts) < 2:
        bot.send_message(msg.chat.id, "Использование: /setwallet <адрес_кошелька>")
        return
    wallet_address = parts[1]
    bot.send_message(msg.chat.id, f"Кошелек установлен: {wallet_address}")

@bot.message_handler(commands=["balance"])
def show_balance(msg):
    if not wallet_address:
        bot.send_message(msg.chat.id, "Кошелек не установлен")
        return
    balance = get_wallet_balance(wallet_address)
    bot.send_message(msg.chat.id, f"💰 Баланс кошелька {wallet_address}\n{balance}")

@bot.message_handler(commands=["transactions"])
def show_transactions(msg):
    if not wallet_address:
        bot.send_message(msg.chat.id, "Кошелек не установлен")
        return
    txs = get_transactions(wallet_address)
    if not txs:
        bot.send_message(msg.chat.id, "Транзакций нет")
        return
    for i, tx in enumerate(txs, 1):
        bot.send_message(msg.chat.id, f"{i}.\n{format_transaction(tx)}")

@bot.message_handler(commands=["notify_on"])
def notify_on(msg):
    global notify_enabled
    notify_enabled = True
    bot.send_message(msg.chat.id, "Уведомления включены")

@bot.message_handler(commands=["notify_off"])
def notify_off(msg):
    global notify_enabled
    notify_enabled = False
    bot.send_message(msg.chat.id, "Уведомления выключены")

# --- Отслеживание новых транзакций ---
def check_new_transactions():
    global last_tx_hash
    if not wallet_address:
        return
    txs = get_transactions(wallet_address, limit=5)
    for tx in txs:
        if tx["hash"] not in last_tx_hash:
            last_tx_hash.add(tx["hash"])
            if notify_enabled:
                text = f"💥 Новая транзакция!\n{format_transaction(tx)}"
                # Отправка всем пользователям, которые писали боту
                bot.send_message(chat_id=wallet_chat_id, text=text)

# --- Основной цикл ---
def run_bot():
    while True:
        try:
            check_new_transactions()
            bot.polling(none_stop=True)
        except Exception as e:
            print("Ошибка:", e)
            time.sleep(5)

if __name__ == "__main__":
    wallet_chat_id = os.getenv("CHAT_ID")  # Если хочешь всем писать, нужно хранить список пользователей
    run_bot()
