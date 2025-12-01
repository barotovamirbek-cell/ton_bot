# bot.py
import os
import asyncio
import requests
from typing import Dict, Any, List, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config  # здесь хранится TON_API_KEY

# -------------------- Настройки --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TONAPI_HEADERS = {"Authorization": f"Bearer {config.TON_API_KEY}"}
TONAPI_BASE = "https://tonapi.io/v2/accounts"

CHECK_INTERVAL = 10  # сек — интервал проверки транзакций

# -------------------- Хранилище в памяти --------------------
users_wallets: Dict[int, str] = {}
users_notify: Dict[int, bool] = {}
users_seen_txs: Dict[int, set] = {}
users_history: Dict[int, List[str]] = {}

# -------------------- UI --------------------
def main_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
            InlineKeyboardButton(text="📜 История", callback_data="history")
        ],
        [
            InlineKeyboardButton(text="🔔 Вкл/Выкл уведомления", callback_data="toggle_notify")
        ]
    ])
    return kb

# -------------------- TON API (синхронные) --------------------
def _tonapi_get_account(wallet: str) -> Dict[str, Any]:
    url = f"{TONAPI_BASE}/{wallet}"
    r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def _tonapi_get_transactions(wallet: str, limit: int = 20) -> List[Dict[str, Any]]:
    url = f"{TONAPI_BASE}/{wallet}/transactions?limit={limit}"
    r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("transactions", [])

# -------------------- Async обертки --------------------
async def tonapi_get_account(wallet: str) -> Optional[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_tonapi_get_account, wallet)
    except Exception:
        return None

async def tonapi_get_transactions(wallet: str, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_tonapi_get_transactions, wallet, limit)
    except Exception:
        return []

# -------------------- Балансы --------------------
def parse_account_balances(account_json: Dict[str, Any]) -> Dict[str, float]:
    balances: Dict[str, float] = {}

    # TON
    ton_balance = float(account_json.get("balance", 0))
    if ton_balance > 1e6:
        ton_balance = ton_balance / 1e9
    balances["TON"] = round(ton_balance, 9)

    # Токены
    token_lists = []
    for key in ["jettons", "tokens", "wallets"]:
        if key in account_json and isinstance(account_json[key], list):
            token_lists = account_json[key]
            break

    for token in token_lists:
        sym = token.get("symbol") or token.get("name") or "TOKEN"
        amt = 0.0
        if isinstance(token.get("balance"), dict):
            try:
                amount_raw = int(token["balance"].get("amount", 0))
                decimals = int(token["balance"].get("decimals", 0) or 0)
                amt = amount_raw / (10 ** decimals) if decimals else float(amount_raw)
            except:
                amt = 0.0
        else:
            try:
                amt = float(token.get("balance") or 0)
            except:
                amt = 0.0
        balances[sym] = round(amt, 9)

    return balances

# -------------------- Формат транзакции --------------------
def format_tx_simple(tx: Dict[str, Any], watched_wallet: str) -> str:
    tx_hash = tx.get("hash") or tx.get("id") or ""
    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", []) or tx.get("out_msg", [])
    incoming_flag = tx.get("incoming")

    # От/Кому
    from_addr = in_msg.get("source") if in_msg else tx.get("from")
    to_addr = (in_msg.get("destination") if in_msg else tx.get("to")) or (out_msgs[0].get("destination") if out_msgs else None)

    direction = "Неизвестно"
    if watched_wallet and to_addr and watched_wallet.lower() == to_addr.lower():
        direction = "Приход"
    elif watched_wallet and from_addr and watched_wallet.lower() == from_addr.lower():
        direction = "Отправка"
    elif incoming_flag is True:
        direction = "Приход"
    elif incoming_flag is False:
        direction = "Отправка"

    # Токен и сумма
    token_name = "TON"
    amount = None
    token_balances = tx.get("token_balances") or tx.get("tokens") or []
    if token_balances:
        tok = token_balances[0]
        token_name = tok.get("symbol") or tok.get("name") or "TOKEN"
        try:
            amt_raw = int(tok.get("balance", 0))
            decimals = int(tok.get("decimals", 0) or 0)
            amount = amt_raw / (10 ** decimals) if decimals else float(amt_raw)
        except:
            amount = float(tok.get("balance", 0) or 0)
    else:
        try:
            val = int(in_msg.get("value", 0) if in_msg else 0) or (int(out_msgs[0].get("value", 0)) if out_msgs else 0)
            amount = val / 1e9
        except:
            amount = None

    amount_str = str(round(amount, 9)) if amount is not None else "—"

    return (
        "💥 *Новая транзакция*\n"
        f"Хэш: `{tx_hash}`\n"
        f"Тип: {direction}\n"
        f"От: `{from_addr or '—'}`\n"
        f"Кому: `{to_addr or '—'}`\n"
        f"Валюта: {token_name}\n"
        f"Количество: {amount_str}"
    )

# -------------------- Фоновые проверки --------------------
async def monitor_all_wallets():
    await asyncio.sleep(2)
    while True:
        for user_id, wallet in list(users_wallets.items()):
            if not wallet:
                continue
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
                text = format_tx_simple(tx, wallet)
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
        "/setwallet <адрес_кошелька> — установить/изменить адрес кошелька\n"
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
    lines = [f"{k}: {v}" for k, v in sorted(balances.items(), key=lambda kv: (kv[0] != "TON", -kv[1]))]
    text = "💰 Баланс кошелька:\n" + "\n".join(lines)
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
    users_notify[user_id] = not users_notify.get(user_id, True)
    state = "включены" if users_notify[user_id] else "выключены"
    await call.message.answer(f"Уведомления {state}.")

# -------------------- Запуск --------------------
async def main():
    asyncio.create_task(monitor_all_wallets())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
