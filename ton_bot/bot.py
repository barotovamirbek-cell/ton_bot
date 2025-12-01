import os
import asyncio
import requests
from typing import Dict, List, Any, Optional

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

CHECK_INTERVAL = 10  # интервал проверки транзакций в секундах

# -------------------- Хранилище в памяти --------------------
users_wallets: Dict[int, str] = {}           # user_id -> wallet_address
users_notify: Dict[int, bool] = {}           # user_id -> уведомления
users_seen_txs: Dict[int, set] = {}          # user_id -> tx_hash
users_history: Dict[int, List[str]] = {}     # user_id -> история сообщений

# -------------------- UI --------------------
def main_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("📜 История", callback_data="history")],
        [InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="toggle_notify")]
    ])
    return kb

# -------------------- TonAPI функции --------------------
def _tonapi_get_account(wallet: str) -> Dict[str, Any]:
    url = f"{TONAPI_BASE}/{wallet}"
    r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def _tonapi_get_transactions(wallet: str, limit: int = 50) -> List[Dict[str, Any]]:
    url = f"{TONAPI_BASE}/{wallet}/transactions?limit={limit}"
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

# -------------------- Парсинг баланса --------------------
def parse_account_balances(account_json: Dict[str, Any]) -> Dict[str, float]:
    balances: Dict[str, float] = {}
    ton_balance = 0.0
    try:
        ton_balance = float(account_json.get("balance", 0))
        if ton_balance > 1e6:
            ton_balance = ton_balance / 1e9
    except Exception:
        pass
    balances["TON"] = round(ton_balance, 9)
    return balances

# -------------------- Формат транзакций --------------------
def format_tx(tx: Dict[str, Any], wallet: str) -> str:
    tx_hash = tx.get("hash") or tx.get("id", "—")
    incoming = tx.get("incoming", None)
    amount = 0.0
    token_name = "TON"

    # amount
    if tx.get("in_msg") and tx["in_msg"].get("value"):
        amount = int(tx["in_msg"]["value"]) / 1e9
    elif tx.get("out_msgs") and tx["out_msgs"]:
        amount = int(tx["out_msgs"][0].get("value", 0)) / 1e9
    elif tx.get("amount"):
        amount = float(tx.get("amount", 0))

    # direction
    from_addr = tx.get("from") or (tx.get("in_msg") or {}).get("source") or "—"
    to_addr = tx.get("to") or (tx.get("in_msg") or {}).get("destination") or "—"
    direction = "Приход" if wallet.lower() == to_addr.lower() else "Отправка"

    text = (
        f"💥 *Новая транзакция*\n"
        f"Хэш: `{tx_hash}`\n"
        f"Тип: {direction}\n"
        f"От: `{from_addr}`\n"
        f"Кому: `{to_addr}`\n"
        f"Валюта: {token_name}\n"
        f"Количество: {amount}"
    )
    return text

# -------------------- Мониторинг --------------------
async def monitor_wallets():
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

                text = format_tx(tx, wallet)
                history.append(text)
                if len(history) > 100:
                    history.pop(0)

                if users_notify.get(user_id, True):
                    try:
                        await bot.send_message(user_id, text, parse_mode="Markdown")
                    except Exception:
                        pass
        await asyncio.sleep(CHECK_INTERVAL)

# -------------------- Хендлеры --------------------
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    users_notify.setdefault(user_id, True)
    await message.answer(
        "Бот запущен.\nУстановите кошелек: /setwallet <адрес>\nКоманды:\n/setwallet <адрес> — установить/изменить адрес\n",
        reply_markup=main_keyboard()
    )

@dp.message(F.text.startswith("/setwallet"))
async def cmd_setwallet(message: types.Message):
    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /setwallet <адрес_кошелька>")
        return
    wallet = parts[1].strip()
    users_wallets[user_id] = wallet
    users_seen_txs[user_id] = set()
    users_history[user_id] = []
    users_notify.setdefault(user_id, True)
    await message.answer(f"Адрес кошелька установлен: `{wallet}`", parse_mode="Markdown")

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
    text = "\n".join([f"{k}: {v}" for k, v in balances.items()])
    await call.message.answer(f"💰 Баланс:\n{text}")

@dp.callback_query(F.data == "history")
async def cb_history(call: types.CallbackQuery):
    user_id = call.from_user.id
    history = users_history.get(user_id, [])
    if not history:
        await call.message.answer("История пуста.")
        return
    last = history[-10:]
    for item in last:
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
    asyncio.create_task(monitor_wallets())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
