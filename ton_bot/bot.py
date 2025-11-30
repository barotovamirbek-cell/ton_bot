import os
import requests
import time
from threading import Thread
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")
bot = TeleBot(BOT_TOKEN)

# Словари на пользователей
wallets = {}         # user_id → адрес кошелька
notifications = {}   # user_id → True/False
last_seen = {}       # user_id → set of seen tx‑hashes

# --- Конфигурация API ---
TONAPI_BASE = "https://tonapi.io/v2"  # базовый URL TonAPI

def format_amount(a: float) -> str:
    s = f"{a:.9f}".rstrip('0').rstrip('.')
    return s if s else "0"

def get_wallet_info_tonapi(address: str):
    """Попытка получить баланс и токены через TonAPI."""
    url = f"{TONAPI_BASE}/accounts/{address}"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    return data

def get_balance(address: str) -> str:
    info = get_wallet_info_tonapi(address)
    if not info or not info.get("ok", False):
        return "Баланс недоступен — TonAPI вернул ошибку"
    result = info.get("result", {})
    lines = []
    # баланс TON
    ton = result.get("balance")
    if ton is not None:
        # TonAPI может возвращать в нанотонах или других — нужно проверить
        try:
            ton_f = float(ton) / 1e9
        except:
            ton_f = float(ton)
        lines.append(f"🔹 TON: {format_amount(ton_f)}")
    # токены / jettons
    assets = result.get("jettons", [])
    for jt in assets:
        name = jt.get("name") or jt.get("symbol") or "TOKEN"
        bal = float(jt.get("balance", 0))
        lines.append(f"🔹 {name}: {format_amount(bal)}")
    if not lines:
        return "Баланс: 0"
    return "\n".join(lines)

def get_transactions_tonapi(address: str):
    """Получаем транзакции через TonAPI."""
    url = f"{TONAPI_BASE}/accounts/{address}/transfers"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("ok", False):
        return None
    return data.get("result", [])

def format_transactions_list(txs: list) -> str:
    if not txs:
        return "Транзакций нет"
    msg = ""
    count = 0
    seen = set()
    for tx in txs:
        hash_ = tx.get("hash") or tx.get("id") or ""
        if not hash_ or hash_ in seen:
            continue
        seen.add(hash_)
        count += 1
        frm = tx.get("from", "")
        to = tx.get("to", "")
        # условимся: если есть поле "amount", берём его
        amount = None
        token = "TON"
        if "amount" in tx:
            amount = float(tx["amount"])
        if "jetton" in tx and isinstance(tx["jetton"], dict):
            token = tx["jetton"].get("symbol") or tx["jetton"].get("name") or token
            try:
                amount = float(tx.get("jetton_balance", 0))
            except:
                pass
        if amount is None:
            continue
        msg += f"{count}. 📝 Hash: {hash_}\n"
        msg += f"   🔹 From: {frm}\n"
        msg += f"   🔹 To: {to}\n"
        msg += f"   🔹 Токен: {token}\n"
        msg += f"   🔹 Количество: {format_amount(amount)}\n\n"
    return msg if msg else "Транзакций нет"

# === Telegram handlers ===

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

@bot.message_handler(func=lambda message: True)
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
        txs = get_transactions_tonapi(wallet)
        if txs is None:
            bot.send_message(user, "Не удалось получить историю")
        else:
            bot.send_message(user, format_transactions_list(txs))
    elif m.text == "🔔 Вкл уведомления":
        notifications[user] = True
        bot.send_message(user, "Уведомления включены")
    elif m.text == "🔕 Выкл уведомлений":
        notifications[user] = False
        bot.send_message(user, "Уведомления выключены")

def monitor_loop():
    while True:
        for user, wallet in wallets.items():
            if not wallet or not notifications.get(user, False):
                continue
            txs = get_transactions_tonapi(wallet)
            if not txs:
                continue
            seen = last_seen.setdefault(user, set())
            for tx in reversed(txs):
                h = tx.get("hash") or tx.get("id") or None
                if not h or h in seen:
                    continue
                seen.add(h)
                frm = tx.get("from", "")
                to = tx.get("to", "")
                token = "TON"
                amount = None
                if "amount" in tx:
                    amount = float(tx["amount"])
                if "jetton" in tx and isinstance(tx["jetton"], dict):
                    token = tx["jetton"].get("symbol") or tx["jetton"].get("name") or token
                    try:
                        amount = float(tx.get("jetton_balance", 0))
                    except:
                        pass
                if amount is None:
                    continue
                bot.send_message(user,
                    f"💥 Новая транзакция!\n🔹 From: {frm}\n🔹 To: {to}\nТокен: {token}\nКоличество: {format_amount(amount)}"
                )
        time.sleep(20)

Thread(target=monitor_loop, daemon=True).start()

bot.infinity_polling()
