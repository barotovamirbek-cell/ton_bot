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

CHECK_INTERVAL = 10  # интервал проверки

# -------------------- Хранилище --------------------
users_wallets: Dict[int, str] = {}
users_notify: Dict[int, bool] = {}
users_seen_txs: Dict[int, set] = {}
users_history: Dict[int, List[str]] = {}

# -------------------- UI --------------------
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
            [InlineKeyboardButton(text="📜 История", callback_data="history")],
            [InlineKeyboardButton(text="🔔 Вкл/Выкл уведомления", callback_data="toggle_notify")]
        ]
    )

# -------------------- TonAPI --------------------
def safe_json(response: requests.Response) -> Optional[dict]:
    """TonAPI иногда возвращает мусор — безопасный разбор."""
    try:
        return response.json()
    except Exception:
        print("TonAPI вернул мусор:", response.text[:200])
        return None

def _get_account(wallet: str) -> Optional[dict]:
    url = f"{TONAPI_BASE}/{wallet}"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
        return safe_json(r)
    except Exception as e:
        print("Ошибка запроса:", e)
        return None

def _get_transactions(wallet: str) -> List[dict]:
    url = f"{TONAPI_BASE}/{wallet}/transactions?limit=100"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    except Exception as e:
        print("Сеть упала:", e)
        return []

    data = safe_json(r)
    if not data or "transactions" not in data:
        return []

    return data["transactions"]

async def get_account(wallet: str):
    return await asyncio.to_thread(_get_account, wallet)

async def get_transactions(wallet: str):
    return await asyncio.to_thread(_get_transactions, wallet)

# -------------------- Баланс --------------------
def parse_balance(account_json: Dict[str, Any]) -> float:
    if not account_json:
        return 0.0

    try:
        bal = float(account_json.get("balance", 0))
        return round(bal / 1e9, 6)
    except:
        return 0.0

# -------------------- Транзакции --------------------
def format_tx(tx: Dict[str, Any], wallet: str) -> str:
    tx_hash = tx.get("hash", "—")
    from_addr = tx.get("from", "—")
    to_addr = tx.get("to", "—")

    incoming = wallet.lower() == (to_addr or "").lower()
    outgoing = wallet.lower() == (from_addr or "").lower()

    amount = 0
    if incoming:
        amount = int(tx.get("in_msg", {}).get("value", 0)) / 1e9
    elif outgoing:
        msgs = tx.get("out_msgs", [])
        if msgs:
            amount = int(msgs[0].get("value", 0)) / 1e9

    direction = "Покупка" if incoming else "Продажа" if outgoing else "Перевод"

    return (
        f"💥 *Новая транзакция*\n"
        f"Хэш: `{tx_hash}`\n"
        f"Тип: {direction}\n"
        f"От: `{from_addr}`\n"
        f"Кому: `{to_addr}`\n"
        f"TON: {amount}"
    )

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
                tx_hash = tx.get("hash")
                if not tx_hash or tx_hash in seen:
                    continue

                seen.add(tx_hash)
                msg = format_tx(tx, wallet)
                history.append(msg)

                if len(history) > 100:
                    history.pop(0)

                if users_notify.get(user_id, True):
                    try:
                        await bot.send_message(user_id, msg, parse_mode="Markdown")
                    except:
                        pass

        await asyncio.sleep(CHECK_INTERVAL)

# -------------------- Хендлеры --------------------
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    users_notify.setdefault(uid, True)
    await message.answer(
        "Бот запущен.\n"
        "Установите кошелек: /setwallet <адрес>\n",
        reply_markup=main_keyboard()
    )

@dp.message(F.text.startswith("/setwallet"))
async def cmd_setwallet(message: types.Message):
    uid = message.from_user.id
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer("Использование: /setwallet <адрес>")
        return

    wallet = parts[1].strip()

    users_wallets[uid] = wallet
    users_seen_txs[uid] = set()
    users_history[uid] = []

    await message.answer(f"Кошелёк установлен: `{wallet}`", parse_mode="Markdown")

@dp.callback_query(F.data == "balance")
async def cb_balance(call: types.CallbackQuery):
    uid = call.from_user.id
    w = users_wallets.get(uid)

    if not w:
        return await call.message.answer("Сначала укажите /setwallet")

    acc = await get_account(w)
    bal = parse_balance(acc)

    await call.message.answer(f"💰 Баланс: {bal} TON")

@dp.callback_query(F.data == "history")
async def cb_history(call: types.CallbackQuery):
    uid = call.from_user.id
    h = users_history.get(uid, [])

    if not h:
        return await call.message.answer("История пуста.")

    for m in h[-10:]:
        await call.message.answer(m, parse_mode="Markdown")

@dp.callback_query(F.data == "toggle_notify")
async def cb_toggle(call: types.CallbackQuery):
    uid = call.from_user.id
    cur = users_notify.get(uid, True)
    users_notify[uid] = not cur

    await call.message.answer(
        f"Уведомления {'включены' if users_notify[uid] else 'выключены'}"
    )

# -------------------- Запуск --------------------
async def main():
    asyncio.create_task(monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
