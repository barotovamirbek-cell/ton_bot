# bot.py
import os
import time
import requests
import asyncio
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

# -------------------- Хранилище в памяти --------------------
# user_id -> wallet_address
users_wallets: Dict[int, str] = {}
# user_id -> bool (уведомления включены)
users_notify: Dict[int, bool] = {}
# user_id -> set(tx_hash)
users_seen_txs: Dict[int, set] = {}
# user_id -> list formatted history (строки)
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

# -------------------- TON API (синхронные, вызываются в потоке) --------------------
def _tonapi_get_account(wallet: str) -> Dict[str, Any]:
    """Возвращает JSON аккаунта от TonAPI v2 (синхронно)."""
    url = f"{TONAPI_BASE}/{wallet}"
    r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

def _tonapi_get_transactions(wallet: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Возвращает транзакции аккаунта (синхронно)."""
    url = f"{TONAPI_BASE}/{wallet}/transactions?limit={limit}"
    r = requests.get(url, headers=TONAPI_HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("transactions", [])

# -------------------- Обёртки async -> sync --------------------
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

# -------------------- Парсеры (баланс и токены) --------------------
def parse_account_balances(account_json: Dict[str, Any]) -> Dict[str, float]:
    """
    Возвращает словарь {token_symbol: amount} включая TON.
    Структура TonAPI может отличаться, обработаем самые вероятные поля.
    """
    balances: Dict[str, float] = {}

    # TON balance
    ton_balance = 0.0
    if "balance" in account_json:
        try:
            ton_balance = float(account_json.get("balance", 0))
            # TonAPI иногда возвращает баланс в нано- или в уже human-readable.
            # Если число большое (>1e6) — скорее всего это nanoton -> переведём в TON
            if ton_balance > 1e6:
                ton_balance = ton_balance / 1e9
        except Exception:
            ton_balance = 0.0
    balances["TON"] = round(ton_balance, 9)

    # Jettons / tokens
    # TonAPI v2 может содержать ключ "jettons" или "tokens" или "wallets"
    token_lists = []
    if "jettons" in account_json and isinstance(account_json["jettons"], list):
        token_lists = account_json["jettons"]
    elif "tokens" in account_json and isinstance(account_json["tokens"], list):
        token_lists = account_json["tokens"]
    elif "wallets" in account_json and isinstance(account_json["wallets"], list):
        token_lists = account_json["wallets"]

    for token in token_lists:
        # Попробуем извлечь имя/символ и баланс + decimals
        sym = token.get("symbol") or token.get("name") or token.get("jetton", {}).get("symbol") or "TOKEN"
        # баланс может быть в token["balance"] либо token["balance"]["amount"]
        amt = 0.0
        if isinstance(token.get("balance"), dict):
            # { "amount": "...", "decimals": N }
            try:
                amount_raw = int(token["balance"].get("amount", 0))
                decimals = int(token["balance"].get("decimals", 0)) if token["balance"].get("decimals") is not None else 0
                if decimals:
                    amt = amount_raw / (10 ** decimals)
                else:
                    amt = float(amount_raw)
            except Exception:
                amt = 0.0
        else:
            # пытаемся взять простое поле
            try:
                raw = token.get("balance") or token.get("amount") or token.get("value")
                if raw is None:
                    amt = 0.0
                else:
                    amt = float(raw)
            except Exception:
                amt = 0.0

        # округлим до 9 знаков, если нужно
        balances[sym] = round(amt, 9)

    return balances

# -------------------- Форматирование транзакции --------------------
def format_tx_simple(tx: Dict[str, Any], watched_wallet: str) -> str:
    """
    Формат сообщения уведомления/истории по транзакции.
    Учтёт токен (если есть) и количество.
    """
    # Поля структуры TonAPI могут быть разными — обрабатываем наиболее ожидаемые
    tx_hash = tx.get("hash") or tx.get("id") or ""
    # direction: определяем по полям 'in_msg'/'out_msgs' или по 'incoming' флагу
    in_msg = tx.get("in_msg", {})
    out_msgs = tx.get("out_msgs", []) or tx.get("out_msg", []) or []
    incoming_flag = tx.get("incoming")  # некоторый API возвращает это
    # try detect amount & token
    token_name = "TON"
    amount = None

    # check token transfers list
    if "token_balances" in tx and isinstance(tx["token_balances"], list) and tx["token_balances"]:
        # возьмём первый токенный элемент (может быть несколько)
        tok = tx["token_balances"][0]
        token_name = tok.get("symbol") or tok.get("name") or token_name
        try:
            amount = int(tok.get("balance", 0)) / (10 ** int(tok.get("decimals", 0)))
        except Exception:
            amount = float(tok.get("balance", 0) or 0)
    else:
        # fallback to ton value
        try:
            if in_msg and in_msg.get("value"):
                amount = int(in_msg.get("value", 0)) / 1e9
            elif out_msgs and out_msgs[0].get("value"):
                amount = int(out_msgs[0].get("value", 0)) / 1e9
            elif tx.get("amount") is not None:
                amount = float(tx.get("amount"))
        except Exception:
            amount = None

    # determine from/to
    from_addr = None
    to_addr = None
    if in_msg:
        from_addr = in_msg.get("source")
        to_addr = in_msg.get("destination")
    if not from_addr and tx.get("from"):
        from_addr = tx.get("from")
    if not to_addr and tx.get("to"):
        to_addr = tx.get("to")
    # If still not found, try out_msgs
    if not to_addr and out_msgs:
        to_addr = out_msgs[0].get("destination")

    # determine direction for watched wallet
    direction = "Неизвестно"
    if watched_wallet and to_addr and watched_wallet.lower() == to_addr.lower():
        direction = "Приход"
    elif watched_wallet and from_addr and watched_wallet.lower() == from_addr.lower():
        direction = "Отправка"
    elif incoming_flag is True:
        direction = "Приход"
    elif incoming_flag is False:
        direction = "Отправка"

    amount_str = str(amount) if amount is not None else "—"

    parts = [
        f"Хэш: `{tx_hash}`",
        f"Тип: {direction}",
        f"От: `{from_addr or '—'}`",
        f"Кому: `{to_addr or '—'}`",
        f"Валюта: {token_name}",
        f"Количество: {amount_str}"
    ]
    return "\n".join(parts)

# -------------------- Фон: проверка транзакций для всех пользователей --------------------
async def monitor_all_wallets():
    await asyncio.sleep(2)
    while True:
        # копия ключей чтобы можно было менять словарь из команд без ошибок
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

            # транзакции приходят от новых к старым — пройдём с конца чтобы отправлять в порядке
            for tx in reversed(txs):
                tx_hash = tx.get("hash") or tx.get("id")
                if not tx_hash:
                    continue
                if tx_hash in seen:
                    continue
                # Новая транзакция для этого пользователя
                seen.add(tx_hash)

                # Формируем текст уведомления (включает название токена и количество)
                text = "💥 *Новая транзакция*\n"
                text += format_tx_simple(tx, wallet)

                # Сохраним в локальную историю (строка)
                history.append(text)
                # ограничим длину истории чтобы не расти бесконечно
                if len(history) > 100:
                    history.pop(0)

                # Отправляем уведомление только если включено
                if users_notify.get(user_id, True):
                    try:
                        await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                    except Exception:
                        # не падаем на ошибке отправки
                        pass
        await asyncio.sleep(CHECK_INTERVAL)

# -------------------- Хендлеры команд --------------------
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    # по умолчанию уведомления включены
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
    # при смене адреса очищаем виденные транзакции и историю — чтобы отслеживать с нуля
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
    # форматируем: имя — количество
    lines = []
    # Сортируем: токены с ненулевым балансом первыми, TON внутри
    for k, v in sorted(balances.items(), key=lambda kv: (kv[0] != "TON", -float(kv[1]) if isinstance(kv[1], (int,float)) else 0)):
        lines.append(f"{k}: {v}")
    text = "💰 Баланс кошелька:\n" + "\n".join(lines)
    await call.message.answer(text)

@dp.callback_query(F.data == "history")
async def cb_history(call: types.CallbackQuery):
    user_id = call.from_user.id
    history = users_history.get(user_id, [])
    if not history:
        await call.message.answer("История пуста.")
        return
    # показываем последние 10 записей
    last = history[-10:]
    # отправляем по одному (чтобы не превышать длину сообщений)
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
    # старт фонового мониторинга
    asyncio.create_task(monitor_all_wallets())
    # старт polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
