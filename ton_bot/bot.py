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

CHECK_INTERVAL = 10  # сек — интервал проверки транзакций

# -------------------- Хранилище --------------------
users_wallets: Dict[int, str] = {}
users_notify: Dict[int, bool] = {}
users_seen_txs: Dict[int, set] = {}
users_history: Dict[int, List[str]] = {}

# -------------------- UI --------------------
def main_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
            [InlineKeyboardButton(text="📜 История", callback_data="history")],
            [InlineKeyboardButton(text="🔔 Вкл/Выкл уведомления", callback_data="toggle_notify")]
        ]
    )
    return kb

# -------------------- TonAPI --------------------
def _get_account(wallet: str) -> Optional[Dict[str, Any]]:
    url = f"{TONAPI_BASE}/{wallet}"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
        return r.json()
    except Exception:
        return None

def _get_transactions(wallet: str) -> List[Dict[str, Any]]:
    url = f"{TONAPI_BASE}/{wallet}/transactions?limit=100"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
        data = r.json()
        return data.get("transactions", [])
    except Exception:
        print(f"Ошибка JSON TonAPI для {wallet}: {r.text}")
        return []

async def get_account(wallet: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_get_account, wallet)

async def get_transactions(wallet: str) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_get_transactions, wallet)

# -------------------- Баланс --------------------
def parse_balance(account_json: Dict[str, Any]) -> float:
    try:
        balance = float(account_json.get("balance", 0))
        if balance > 1e6:
            balance = balance / 1e9
        return round(balance, 9)
    except Exception:
        return 0.0

# -------------------- Формат транзакции --------------------
def format_tx(tx: Dict[str, Any], wallet: str) -> str:
    tx_hash = tx.get("hash") or tx.get("id") or "—"
    incoming = False
    amount = 0.0
    from_addr = tx.get("from") or "—"
    to_addr = tx.get("to") or "—"

    # определяем направление
    if wallet.lower() == (to_addr or "").lower():
        incoming = True
        if tx.get("in_msg", {}).get("value"):
            amount = int(tx["in_msg"]["value"]) / 1e9
    elif wallet.lower() == (from_addr or "").lower():
        incoming = False
        if tx.get("out_msgs") and tx["out_msgs"][0].get("value"):
            amount = int(tx["out_msgs"][0]["value"]) / 1e9
    else:
        incoming = None

    direction = "Покупка" if incoming else "Продажа" if incoming is False else "Перевод"
    amount_str = str(amount) if amount else "—"

    text = (
        f"💥 *Новая транзакция*\n"
        f"Хэш: `{tx_hash}`\n"
        f"Тип: {direction}\n"
        f"От: `{from_addr}`\n"
        f"Кому: `{to_addr}`\n"
        f"Валюта: TON\n"
        f"Количество: {amount_str}"
    )
    return text

# -------------------- Мониторинг --------------------
async def monitor():
    await asyncio.sleep(2)
    while True:
        for user_id, wallet in users_wallets.items():
            txs = await get_transactions(wallet)
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
                        await bot.send_message(user_id, text=text, parse_mode="Markdown")
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

# -------------------- Callback --------------------
@dp.callback_query(F.data == "balance")
async def cb_balance(call: types.CallbackQuery):
    user_id = call.from_user.id
    wallet = users_wallets.get(user_id)
    if not wallet:
        await call.message.answer("Сначала установите адрес через /setwallet")
        return
    account = await get_account(wallet)
    if not account:
        await call.message.answer("Не удалось получить данные кошелька.")
        return
    balance = parse_balance(account)
    await call.message.answer(f"💰 Баланс кошелька: {balance} TON")

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
    asyncio.create_task(monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
