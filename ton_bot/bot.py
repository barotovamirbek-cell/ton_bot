import os
import requests
import time
from threading import Thread
from telebot import TeleBot, types

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")

bot = TeleBot(BOT_TOKEN)

# TonAPI endpoint
TONAPI_BASE = "https://tonapi.io/v2"

# Пользователи
users = {}  # chat_id → {"wallet": address, "notify": bool, "last_seen": set()}

MIN_AMOUNT = 0.0001  # минималка для уведомлений для Toncoin и жетонов

def format_amount(a: float) -> str:
    """Красивое форматирование суммы."""
    if a >= 1:
        return f"{a:.6f}"
    return f"{a:.9f}".rstrip('0').rstrip('.') or "0"

def get_account_info(address: str):
    """Запрашивает информацию об аккаунте через TonAPI."""
    try:
        resp = requests.get(f"{TONAPI_BASE}/accounts/{address}")
        data = resp.json()
        return data
    except Exception as e:
        print("Error get_account_info:", e)
        return None

def get_wallet_balance(address: str):
    info = get_account_info(address)
    if not info or not info.get("ok"):
        return None
    result = info.get("result", {})
    balances = []
    # Toncoin
    ton = result.get("balance")
    if ton is not None:
        bal = float(ton) / 1e9
        balances.append(("TON", bal))
    # Jettons / токены
    for jt in result.get("jettons", []):
        symbol = jt.get("symbol") or jt.get("name") or "TOKEN"
        bal = float(jt.get("balance", 0))
        balances.append((symbol, bal))
    return balances

def get_transactions(address: str, limit=20):
    try:
        resp = requests.get(f"{TONAPI_BASE}/accounts/{address}/transfers?limit={limit}")
        data = resp.json()
    except Exception as e:
        print("Error get_transactions:", e)
        return []
    if not data.get("ok"):
        return []
    txs = []
    for tx in data.get("result", []):
        h = tx.get("hash") or tx.get("id")
        frm = tx.get("from", "")
        to = tx.get("to", "")
        # Toncoin
        if tx.get("amount"):
            amt = float(tx["amount"]) / 1e9
            tkn = "TON"
        else:
            # jetton/token transfer
            jt = tx.get("jetton")
            if jt:
                tkn = jt.get("symbol") or jt.get("name") or "TOKEN"
                amt = float(tx.get("jetton_balance", 0))
            else:
                continue
        if amt < MIN_AMOUNT:
            continue
        txs.append({"hash": h, "from": frm, "to": to, "token": tkn, "amount": amt})
    return txs

def send_balance(chat_id):
    addr = users[chat_id]["wallet"]
    bal = get_wallet_balance(addr)
    if not bal:
        bot.send_message(chat_id, "Баланс недоступен")
        return
    msg = f"💰 Баланс кошелька {addr} 💰\n"
    for sym, amt in bal:
        msg += f"🔹 {sym}: {format_amount(amt)}\n"
    bot.send_message(chat_id, msg)

def send_transactions(chat_id):
    addr = users[chat_id]["wallet"]
    txs = get_transactions(addr, limit=20)
    if not txs:
        bot.send_message(chat_id, "Транзакций нет")
        return
    msg = ""
    for i, tx in enumerate(txs, start=1):
        msg += (f"{i}. 📝 Hash: {tx['hash']}\n"
                f"   🔹 From: {tx['from']}\n"
                f"   🔹 To: {tx['to']}\n"
                f"   Токен: {tx['token']}\n"
                f"   Количество: {format_amount(tx['amount'])}\n\n")
    bot.send_message(chat_id, msg)

def monitor_loop():
    while True:
        for chat_id, info in list(users.items()):
            addr = info.get("wallet")
            if not addr or not info.get("notify", False):
                continue
            txs = get_transactions(addr, limit=5)
            for tx in txs:
                h = tx["hash"]
                if h in info["last_seen"]:
                    continue
                info["last_seen"].add(h)
                bot.send_message(chat_id,
                                 (f"💥 Новая транзакция!\n"
                                  f"🔹 From: {tx['from']}\n"
                                  f"🔹 To: {tx['to']}\n"
                                  f"Токен: {tx['token']}\n"
                                  f"Количество: {format_amount(tx['amount'])}"))
        time.sleep(20)

@bot.message_handler(commands=["start"])
def cmd_start(m):
    users[m.chat.id] = {"wallet": "", "notify": True, "last_seen": set()}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("/setwallet", "/balance", "/transactions")
    kb.row("🔔 Вкл уведомления", "🔕 Выкл уведомлений")
    bot.send_message(m.chat.id, "Привет! Установи кошелёк командой /setwallet <адрес>", reply_markup=kb)

@bot.message_handler(commands=["setwallet"])
def cmd_setwallet(m):
    parts = m.text.split()
    if len(parts) != 2:
        bot.send_message(m.chat.id, "Используй: /setwallet <адрес>")
        return
    users[m.chat.id]["wallet"] = parts[1]
    users[m.chat.id]["last_seen"] = set()
    bot.send_message(m.chat.id, f"Кошелек установлен: {parts[1]}")

@bot.message_handler(func=lambda m: True)
def handler(m):
    chat = m.chat.id
    text = m.text.strip()
    if text == "/balance":
        if not users[chat]["wallet"]:
            bot.send_message(chat, "Кошелек не установлен")
            return
        send_balance(chat)
    elif text == "/transactions":
        if not users[chat]["wallet"]:
            bot.send_message(chat, "Кошелек не установлен")
            return
        send_transactions(chat)
    elif text == "🔔 Вкл уведомления":
        users[chat]["notify"] = True
        bot.send_message(chat, "Уведомления включены")
    elif text == "🔕 Выкл уведомлений":
        users[chat]["notify"] = False
        bot.send_message(chat, "Уведомления выключены")

if __name__ == "__main__":
    Thread(target=monitor_loop, daemon=True).start()
    bot.infinity_polling()
