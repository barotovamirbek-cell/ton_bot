# bot.py
import os
import asyncio
import requests
from typing import Dict, Any, List, Optional

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

CHECK_INTERVAL = 10  # интервал проверки транзакций

# -------------------- Хранилище --------------------
users_wallets: Dict[int, str] = {}
users_notify: Dict[int, bool] = {}
users_seen_txs: Dict[int, set] = {}
users_history: Dict[int, List[str]] = {}

# -------------------- UI --------------------
def main_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("💰 Баланс", callback_data="balance"),
         InlineKeyboardButton("📜 История", callback_data="history")],
        [InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="toggle_notify")]
    ])
    return kb

# -------------------- TON API --------------------
def _tonapi_get_account(wallet: str) -> Dict[str, Any]:
    url = f"{TONAPI_BASE}/{wallet}"
    r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def _tonapi_get_transactions(wallet: str) -> List[Dict[str, Any]]:
    url = f"{TONAPI_BASE}/{wallet}/transactions"
    r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("transactions", [])

async def tonapi_get_account(wallet: str) -> Optional[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_tonapi_get_account, wallet)
    except Exception:
        return None

async def tonapi_get_transactions(wallet: str) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_tonapi_get_transactions, wallet)
    except Exception:
        return []

# -------------------- Баланс --------------------
def parse_account_balances(account_json: Dict[str, Any]) -> Dict[str, float]:
    balances: Dict[str, float] = {}
    ton_balance = 0.0
    try:
        ton_balance = float(account_json.get("balance", 0))
        if ton_balance > 1e6:  # nanoton -> TON
            ton_balance /= 1e9
    except Exception:
        ton_balance = 0.0
    balances["TON"] = round(ton_balance, 9)
    return balances

# -------------------- Форматирование транзакции --------------------
def format_tx_simple(tx: Dict[str, Any], wallet: str) -> str:
    tx_hash = tx.get("hash") or tx.get("id") or ""
    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", []) or tx.get("out_msg", [])
    incoming_flag = tx.get("incoming")
    token_name = "TON"
    amount = 0.0
    try:
        if in_msg.get("value"):
            amount = int(in_msg.get("value")) / 1e9
        elif out_msgs and out_msgs[0].get("value"):
            amount = int(out_msgs[0].get("value")) / 1e9
        elif tx.get("amount"):
            amount = float(tx.get("amount"))
    except Exception:
        amount = 0.0

    from_addr = in_msg.get("source") or tx.get("from")
    to_addr = in_msg.get("destination") or tx.get("to")
    direction = "Неизвестно"
    if wallet.lower() == (to_addr or "").lower():
        direction = "Приход"
    elif wallet.lower() == (from_addr or "").lower():
        direction = "Отправка"
    elif incoming_flag is True:
        direction = "Приход"
    elif incoming_flag is False:
        direction = "Отправка"

    if amount >= 0.000001:
        parts = [
            f"Хэш: `{tx_hash}`",
            f"Тип: {direction}",
            f"От: `{from_addr or '—'}`",
            f"Кому: `{to_addr or '—'}`",
            f"Валюта: {token_name}",
            f"Количество: {amount}"
        ]
        return "\n".join(parts)
    return ""

# -------------------- Мониторинг --------------------
async def monitor_all_wallets():
    await asyncio.sleep(2)
    while True:
        for user_id, wallet in users_wallets.items():
            txs = await tonapi_get_transactions(wallet)
            if not txs:
                continue

            seen = users_seen_txs.setdefault(user_id, set())
            history = users_history.setdefault(user_id, [])

            for tx in reversed(txs):
                tx_hash = tx.get("hash") or tx.get("id")
                if not tx_hash or tx_hash in seen:
                    continue
                seen.add(tx_hash)

                text = format_tx_simple(tx, wallet)
                if not text:
                    continue

                history.append(text)
                if len(history) > 100:
                    history.pop(0)

                if users_notify.get(user_id, True):
                    try:
                        await bot.send_message(chat_id=user_id, text="💥 *Новая транзакция*\n" + text, parse_mode="Markdown")
                    except Exception:
                        pass

        await asyncio.sleep(CHECK_INTERVAL)

# -------------------- Команды --------------------
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    users_notify.setdefault(user_id, True)
    await message.answer(
        "Бот запущен.\nУстановите кошелек: /setwallet <адрес>\nКнопки: Баланс, История, Вкл/Выкл уведомлений",
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

# -------------------- Callback --------------------
@dp.callback_query(F.data == "balance")
async def cb_balance(call: types.CallbackQuery):
    wallet = users_wallets.get(call.from_user.id)
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
    history = users_history.get(call.from_user.id, [])
    if not history:
        await call.message.answer("История пуста.")
        return
    for item in history[-10:]:
        await call.message.answer(item, parse_mode="Markdown")

@dp.callback_query(F.data == "toggle_notify")
async def cb_toggle(call: types.CallbackQuery):
    user_id = call.from_user.id
    users_notify[user_id] = not users_notify.get(user_id, True)
    state = "включены" if users_notify[user_id] else "выключены"
    await call.message.answer(f"Уведомления {state}.")

# -------------------- Запуск --------------------
async def main():
    asyncio.create_task(monitor_all_wallets())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
