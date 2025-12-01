# bot.py
import os
import asyncio
from typing import Dict, Any, List, Optional
import requests

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
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton("💰 Баланс", callback_data="balance"),
            InlineKeyboardButton("📜 История", callback_data="history")
        ],
        [
            InlineKeyboardButton("🔔 Вкл/Выкл уведомления", callback_data="toggle_notify")
        ]
    ])

# -------------------- TON API --------------------
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

# -------------------- Парсеры --------------------
def parse_account_balances(account_json: Dict[str, Any]) -> Dict[str, float]:
    balances: Dict[str, float] = {}

    # TON
    ton_balance = float(account_json.get("balance", 0))
    if ton_balance > 1e6:
        ton_balance /= 1e9
    balances["TON"] = round(ton_balance, 9)

    # Все токены
    token_lists = []
    for key in ("jettons", "tokens", "wallets", "balances"):
        if key in account_json and isinstance(account_json[key], list):
            token_lists = account_json[key]
            break

    for token in token_lists:
        sym = token.get("symbol") or token.get("name") or "TOKEN"
        amt = 0.0
        if isinstance(token.get("balance"), dict):
            try:
                raw = int(token["balance"].get("amount", 0))
                decimals = int(token["balance"].get("decimals", 0))
                amt = raw / (10 ** decimals) if decimals else float(raw)
            except Exception:
                amt = 0.0
        else:
            try:
                amt = float(token.get("balance") or token.get("amount") or token.get("value") or 0)
            except Exception:
                amt = 0.0
        balances[sym] = round(amt, 9)

    return balances

def format_tx_simple(tx: Dict[str, Any], watched_wallet: str) -> str:
    tx_hash = tx.get("hash") or tx.get("id") or ""
    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", []) or tx.get("out_msg", []) or []
    incoming_flag = tx.get("incoming")

    # from/to
    from_addr = in_msg.get("source") or tx.get("from")
    to_addr = in_msg.get("destination") or tx.get("to")
    if not to_addr and out_msgs:
        to_addr = out_msgs[0].get("destination")

    # direction
    direction = "Неизвестно"
    if watched_wallet and to_addr and watched_wallet.lower() == to_addr.lower():
        direction = "Приход"
    elif watched_wallet and from_addr and watched_wallet.lower() == from_addr.lower():
        direction = "Отправка"
    elif incoming_flag is True:
        direction = "Приход"
    elif incoming_flag is False:
        direction = "Отправка"

    # amount и токены
    if "token_balances" in tx and isinstance(tx["token_balances"], list) and tx["token_balances"]:
        parts_token = []
        for tok in tx["token_balances"]:
            token_name = tok.get("symbol") or tok.get("name") or "TOKEN"
            try:
                amt = int(tok.get("balance", 0)) / (10 ** int(tok.get("decimals", 0)))
            except Exception:
                amt = float(tok.get("balance", 0) or 0)
            parts_token.append(f"{token_name}: {amt}")
        amount_str = ", ".join(parts_token)
    else:
        try:
            if in_msg and in_msg.get("value"):
                amount_str = str(int(in_msg.get("value")) / 1e9)
            elif out_msgs and out_msgs[0].get("value"):
                amount_str = str(int(out_msgs[0].get("value")) / 1e9)
            else:
                amount_str = "—"
        except Exception:
            amount_str = "—"

    parts = [
        f"Хэш: `{tx_hash}`",
        f"Тип: {direction}",
        f"От: `{from_addr or '—'}`",
        f"Кому: `{to_addr or '—'}`",
        f"Валюта: {amount_str}",
        f"Количество: {amount_str}"
    ]
    return "\n".join(parts)

# -------------------- Мониторинг --------------------
async def monitor_all_wallets():
    await asyncio.sleep(2)
    while True:
        user_ids = list(users_wallets.keys())
        for user_id in user_ids:
            wallet = users_wallets.get(user_id)
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
                text = "💥 *Новая транзакция*\n" + format_tx_simple(tx, wallet)
                history.append(text)
                if len(history) > 100:
                    history.pop(0)
                if users_notify.get(user_id, True):
                    try:
                        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                    except Exception:
                        pass
        await asyncio.sleep(CHECK_INTERVAL)

# -------------------- Хендлеры --------------------
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
    lines = [f"{k}: {v}" for k, v in balances.items()]
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
