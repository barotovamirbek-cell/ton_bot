import os
import requests
import time
from threading import Thread
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")
bot = TeleBot(BOT_TOKEN)

# Пользователи
wallets = {}         # user_id -> кошелек
notifications = {}   # user_id -> True/False
last_seen = {}       # user_id -> set(hash)

# --- Конфигурация Toncenter API ---
TONCENTER_API = "https://toncenter.com/api/v2"
TONCENTER_KEY = os.getenv("TONCENTER_KEY")  # Если есть API Key

HEADERS = {"X-API-Key": TONCENTER_KEY} if TONCENTER_KEY else {}

# --- Помощники ---
def format_amount(a: float) -> str:
    s = f"{a:.9f}".rstrip("0").rstrip(".")
    return s if s else "0"

def get_balance(wallet: str) -> str:
    url = f"{TONCENTER_API}/getAddressInformation?address={wallet}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        return "Баланс недоступен"
    data = r.json().get("result")
    if not data:
        return "Баланс недоступен"
    msg = []
    # TON
    ton = int(data.get("balance", 0)) / 1e9
    msg.append(f"🔹 TON: {format_amount(ton)}")
    # Jettons / токены
    for jt in data.get("jettons", []):
        name = jt.get("name") or jt.get("symbol") or "TOKEN"
        bal = float(jt.get("balance", 0))
        msg.append(f"🔹 {name}: {format_amount(bal)}")
    return "\n".join(msg)

def get_transactions(wallet: str):
    url = f"{TONCENTER_API}/getTransactions?address={wallet}&limit=50"
    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        return []
    data = r.json().get("result", [])
    return data

def format_transactions_list(txs: list) -> str:
    if not txs:
        return "Транзакций нет"
    msg = ""
    count = 0
    seen_hashes = set()
    for tx in txs:
        h = tx.get("hash")
        if not h or h in seen_hashes:
            continue
        seen_hashes.add(h)
        count += 1
        frm = tx.get("source") or ""
        to = tx.get("destination") or ""
        token = "TON"
        amount = float(tx.get("value", 0)) / 1e9
        if tx.get("jetton"):
            token = tx["jetton"].get("symbol") or tx["jetton"].get("name") or "TOKEN"
            amount = float(tx["jetton"].get("balance", 0))
        msg += f"{count}. 📝 Hash: {h}\n"
        msg += f"   🔹 From: {frm}\n"
        msg += f"   🔹 To: {to}\n"
        msg += f"   🔹 Токен: {token}\n"
        msg += f"   🔹 Количество: {format_amount(amount)}\n\n"
    return msg if msg else "Транзакций нет"

# --- Telegram ---
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("💰 Баланс", "📝 История")
    kb.row("🔔 Вкл уведомления", "🔕 Выкл уведомлений")
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(m):
    wallets[m.chat.id] = ""
    notifications[m.chat.id] = False
    bot.send_message(m.chat.id, "Привет! Установи кошелёк через /setwallet <адрес>", reply_markup=main_menu())

@bot.message_handler(commands=["setwallet"])
def cmd_setwallet(m):
    parts = m.text.split()
    if len(parts) != 2:
        bot.send_message(m.chat.id, "Используй: /setwallet <адрес>")
        return
    wallets[m.chat.id] = parts[1]
    last_seen[m.chat.id] = set()
    bot.send_message(m.chat.id, f"Адрес установлен: {parts[1]}")

@bot.message_handler(func=lambda m: True)
def handler(m):
    user = m.chat.id
    wallet = wallets.get(user)
    if m.text == "💰 Баланс":
        if not wallet:
            bot.send_message(user, "Сначала установи кошелек через /setwallet")
            return
        bot.send_message(user, get_balance(wallet))
    elif m.text == "📝 История":
        if not wallet:
            bot.send_message(user, "Сначала установи кошелек через /setwallet")
            return
        txs = get_transactions(wallet)
        bot.send_message(user, format_transactions_list(txs))
    elif m.text == "🔔 Вкл уведомления":
        notifications[user] = True
        bot.send_message(user, "Уведомления включены")
    elif m.text == "🔕 Выкл уведомлений":
        notifications[user] = False
        bot.send_message(user, "Уведомления выключены")

# --- Фоновая проверка транзакций ---
def monitor_loop():
    while True:
        for user, wallet in wallets.items():
            if not wallet or not notifications.get(user, False):
                continue
            txs = get_transactions(wallet)
            if not txs:
                continue
            seen = last_seen.setdefault(user, set())
            for tx in reversed(txs):
                h = tx.get("hash")
                if not h or h in seen:
                    continue
                seen.add(h)
                frm = tx.get("source") or ""
                to = tx.get("destination") or ""
                token = "TON"
                amount = float(tx.get("value", 0)) / 1e9
                if tx.get("jetton"):
                    token = tx["jetton"].get("symbol") or tx["jetton"].get("name") or "TOKEN"
                    amount = float(tx["jetton"].get("balance", 0))
                bot.send_message(user,
                    f"💥 Новая транзакция!\n🔹 From: {frm}\n🔹 To: {to}\nТокен: {token}\nКоличество: {format_amount(amount)}"
                )
        time.sleep(20)

Thread(target=monitor_loop, daemon=True).start()

bot.infinity_polling()
