import os
import asyncio
import requests
from typing import Dict, List, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
import config

# ----------------------------------------
# Конфиг
# ----------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

TONAPI_HEADERS = {"Authorization": f"Bearer {config.TON_API_KEY}"}
TONAPI_URL = "https://tonapi.io/v2/accounts"

CHECK_INTERVAL = 10  # каждые 10 сек

# ----------------------------------------
# Данные пользователей
# ----------------------------------------
user_wallet: Dict[int, str] = {}
user_seen: Dict[int, set] = {}
user_notify: Dict[int, bool] = {}
user_history: Dict[int, List[str]] = {}

# ----------------------------------------
# Клавиатура
# ----------------------------------------
def main_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 Баланс", callback_data="balance")
    kb.button(text="📜 История", callback_data="history")
    kb.button(text="🔔 Уведомления", callback_data="toggle_notify")
    kb.adjust(1)
    return kb.as_markup()

# ----------------------------------------
# TonAPI запросы
# ----------------------------------------
def _get_account(wallet: str):
    url = f"{TONAPI_URL}/{wallet}"
    r = requests.get(url, headers=TONAPI_HEADERS)
    return r.json()

def _get_transactions(wallet: str):
    url = f"{TONAPI_URL}/{wallet}/transactions?limit=50"
    r = requests.get(url, headers=TONAPI_HEADERS)
    return r.json().get("transactions", [])

async def get_account(wallet: str):
    return await asyncio.to_thread(_get_account, wallet)

async def get_transactions(wallet: str):
    return await asyncio.to_thread(_get_transactions, wallet)

# ----------------------------------------
# Формат транзакции
# ----------------------------------------
def format_tx(tx: dict, wallet: str) -> str:
    tx_id = tx.get("hash") or tx.get("id", "—")

    # сторона
    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", [])

    amount = 0
    if "value" in in_msg:
        amount = int(in_msg["value"]) / 1e9
    elif out_msgs:
        amount = int(out_msgs[0].get("value", 0)) / 1e9

    from_addr = in_msg.get("source", "—")
    to_addr = in_msg.get("destination", "—")

    direction = "➡️ Приход" if to_addr.lower() == wallet.lower() else "⬅️ Отправка"

    return (
        f"💥 *Новая транзакция*\n"
        f"ID: `{tx_id}`\n"
        f"{direction}\n"
        f"От: `{from_addr}`\n"
        f"Кому: `{to_addr}`\n"
        f"TON: *{amount}*\n"
    )

# ----------------------------------------
# Мониторинг
# ----------------------------------------
async def monitor():
    await asyncio.sleep(3)
    while True:
        for user_id, wallet in user_wallet.items():
            txs = await get_transactions(wallet)
            if not txs:
                continue

            seen = user_seen.setdefault(user_id, set())
            hist = user_history.setdefault(user_id, [])

            for tx in reversed(txs):
                tx_id = tx.get("hash") or tx.get("id")
                if not tx_id or tx_id in seen:
                    continue

                seen.add(tx_id)
                text = format_tx(tx, wallet)
                hist.append(text)

                if len(hist) > 100:
                    hist.pop(0)

                if user_notify.get(user_id, True):
                    try:
                        await bot.send_message(user_id, text, parse_mode="Markdown")
                    except:
                        pass

        await asyncio.sleep(CHECK_INTERVAL)

# ----------------------------------------
# Команды
# ----------------------------------------
@dp.message(F.text == "/start")
async def start(message: types.Message):
    uid = message.from_user.id
    user_notify.setdefault(uid, True)
    await message.answer(
        "Привет. Установи TON адрес:\n/setwallet <адрес>",
        reply_markup=main_keyboard()
    )

@dp.message(F.text.startswith("/setwallet"))
async def setwallet(message: types.Message):
    uid = message.from_user.id
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer("Формат: /setwallet EQxxxxxxxxxxxx")
        return

    w = parts[1].strip()
    user_wallet[uid] = w
    user_seen[uid] = set()
    user_history[uid] = []
    user_notify.setdefault(uid, True)

    await message.answer(f"Кошелёк установлен:\n`{w}`", parse_mode="Markdown")

@dp.callback_query(F.data == "balance")
async def balance(call: types.CallbackQuery):
    uid = call.from_user.id
    wallet = user_wallet.get(uid)

    if not wallet:
        await call.message.answer("Сначала установи кошелёк /setwallet")
        return

    acc = await get_account(wallet)
    bal = int(acc.get("balance", 0)) / 1e9

    await call.message.answer(f"💰 Баланс: *{bal} TON*", parse_mode="Markdown")

@dp.callback_query(F.data == "history")
async def history(call: types.CallbackQuery):
    uid = call.from_user.id
    hist = user_history.get(uid, [])

    if not hist:
        await call.message.answer("История пуста.")
        return

    for msg in hist[-10:]:
        await call.message.answer(msg, parse_mode="Markdown")

@dp.callback_query(F.data == "toggle_notify")
async def toggle_notify(call: types.CallbackQuery):
    uid = call.from_user.id
    user_notify[uid] = not user_notify.get(uid, True)
    state = "ВКЛ" if user_notify[uid] else "ВЫКЛ"
    await call.message.answer(f"Уведомления: {state}")

# ----------------------------------------
# Запуск
# ----------------------------------------
async def main():
    asyncio.create_task(monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
