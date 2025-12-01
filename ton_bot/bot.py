# bot.py
import os
import asyncio
import requests
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import config

# -------------------- Настройки --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TONAPI_HEADERS = {"Authorization": f"Bearer {config.TON_API_KEY}"}
TONAPI_BASE = "https://tonapi.io/v2/accounts"
CHECK_INTERVAL = 10  # сек — интервал проверки транзакций
MIN_TX_AMOUNT = 0.000000001  # фильтр маленьких транзакций

# -------------------- Хранилище --------------------
users_wallets: Dict[int, str] = {}
users_notify: Dict[int, bool] = {}
users_seen_txs: Dict[int, set] = {}
users_history: Dict[int, List[str]] = {}

# -------------------- UI --------------------
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
            InlineKeyboardButton(text="📜 История", callback_data="history")
        ],
        [
            InlineKeyboardButton(text="🔔 Вкл/Выкл уведомления", callback_data="toggle_notify")
        ]
    ])

# -------------------- TON API --------------------
def _tonapi_get_account(wallet: str) -> Dict[str, Any]:
    r = requests.get(f"{TONAPI_BASE}/{wallet}", headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def _tonapi_get_transactions(wallet: str, limit: int = 20) -> List[Dict[str, Any]]:
    r = requests.get(f"{TONAPI_BASE}/{wallet}/transactions?limit={limit}", headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("transactions", [])

async def tonapi_get_account(wallet: str):
    try:
        return await asyncio.to_thread(_tonapi_get_account, wallet)
    except Exception:
        return None

async def tonapi_get_transactions(wallet: str, limit: int = 20):
    try:
        return await asyncio.to_thread(_tonapi_get_transactions, wallet, limit)
    except Exception:
        return []

# -------------------- Баланс --------------------
def parse_account_balances(account_json: Dict[str, Any]) -> Dict[str, float]:
    ton_balance = 0.0
    try:
        ton_balance = float(account_json.get("balance", 0))
        if ton_balance > 1e6:
            ton_balance /= 1e9
    except:
        pass
    return {"TON": round(ton_balance, 9)}

# -------------------- Форматирование транзакции --------------------
def format_tx(tx: Dict[str, Any], wallet: str) -> str:
    tx_hash = tx.get("hash") or tx.get("id") or "—"
    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", []) or tx.get("out_msg", [])
    incoming_flag = tx.get("incoming")

    amount = None
    try:
        if in_msg.get("value"):
            amount = int(in_msg.get("value", 0)) / 1e9
        elif out_msgs and out_msgs[0].get("value"):
            amount = int(out_msgs[0].get("value", 0)) / 1e9
        elif tx.get("amount") is not None:
            amount = float(tx.get("amount"))
    except:
        amount = None

    if amount is None or amount < MIN_TX_AMOUNT:
        return ""

    from_addr = in_msg.get("source") or tx.get("from") or "—"
    to_addr = in_msg.get("destination") or tx.get("to") or (out_msgs[0].get("destination") if out_msgs else "—")

    # Покупка/Продажа
    is_trade = bool(tx.get("token_balances") or tx.get("jetton_transfers"))

    if is_trade:
        direction = "Покупка/Продажа 🛒"
        emoji = "🟡"
    elif wallet.lower() == to_addr.lower():
        direction = "Приход 💰"
        emoji = "🟢⬆️"
    else:
        direction = "Отправка 💸"
        emoji = "🔴⬇️"

    text = (
        f"{emoji} *{direction}*\n"
        f"Хэш: `{tx_hash}`\n"
        f"От: `{from_addr}`\n"
        f"Кому: `{to_addr}`\n"
        f"Валюта: TON\n"
        f"Количество: {round(amount, 9)}"
    )
    return text

# -------------------- Мониторинг --------------------
async def monitor_all_wallets():
    await asyncio.sleep(2)
    while True:
        for user_id, wallet in list(users_wallets.items()):
            txs = await tonapi_get_transactions(wallet, limit=10)
            if not txs:
                continue
            seen = users_seen_txs.setdefault(user_id, set())
            history = users_history.setdefault(user_id, [])

            for tx in reversed(txs):
                tx_hash = tx.get("hash") or tx.get("id")
                if not tx_hash or tx_hash in seen:
                    continue
                seen.add(tx_hash)
                text = format_tx(tx, wallet)
                if not text:
                    continue
                history.append(text)
                if len(history) > 100:
                    history.pop(0)
                if users_notify.get(user_id, True):
                    try:
                        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                    except:
                        pass
        await asyncio.sleep(CHECK_INTERVAL)

# -------------------- Команды --------------------
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    users_notify.setdefault(user_id, True)
    await message.answer(
        "Бот запущен. Установите кошелек: /setwallet <адрес>\n"
        "Команды:\n"
        "/setwallet <адрес> — установить/изменить адрес кошелька\n"
        "Кнопки: Баланс, История, Вкл/Выкл уведомления",
        reply_markup=main_keyboard()
    )

@dp.message(F.text.startswith("/setwallet"))
async def cmd_setwallet(message: types.Message):
    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: /setwallet <адрес_кошелька>")
        return
    wallet = parts[1].strip()
    users_wallets[user_id] = wallet
    users_seen_txs[user_id] = set()
    users_history[user_id] = []
    users_notify.setdefault(user_id, True)
    await message.answer(f"Адрес кошелька установлен: `{wallet}`", parse_mode="Markdown")

# -------------------- Inline callbacks --------------------
@dp.callback_query(F.data == "balance")
async def cb_balance(call: types.CallbackQuery):
    user_id = call.from_user.id
    wallet = users_wallets.get(user_id)
    if not wallet:
        await call.message.answer("Сначала установите адрес через /setwallet")
        return
    account = await tonapi_get_account(wallet)
    if not account:
        await call.message.answer("Не удалось получить данные кошелька.")
        return
    balances = parse_account_balances(account)
    text = "💰 Баланс кошелька:\n" + "\n".join(f"{k}: {v}" for k,v in balances.items())
    await call.message.answer(text)

@dp.callback_query(F.data == "history")
async def cb_history(call: types.CallbackQuery):
    user_id = call.from_user.id
    history = users_history.get(user_id, [])
    if not history:
        await call.message.answer("История пуста.")
        return
    for item in history[-10:]:
        await call.message.answer(item, parse_mode="Markdown")

@dp.callback_query(F.data == "toggle_notify")
async def cb_toggle(call: types.CallbackQuery):
    user_id = call.from_user.id
    current = users_notify.get(user_id, True)
    users_notify[user_id] = not current
    state = "включены" if users_notify[user_id] else "выключены"
    await call.message.answer(f"Уведомления {state}.")

# -------------------- Запуск --------------------
async def main():
    asyncio.create_task(monitor_all_wallets())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
