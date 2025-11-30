# bot.py -- minimal TONAPI notifier bot (aiogram 3.x)
import os
import asyncio
import logging
import time
import requests
from typing import Dict, List, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ton_bot")

# ---- CONFIG ----
# Telegram token (must be in environment variable BOT_TOKEN)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is required")

# TONAPI key: prefer env, fallback to the key you provided so it works immediately.
TONAPI_KEY = os.getenv("TONAPI_KEY",
    "AGVQICJNL2JNYSAAAAADLI55JSSIIXAJEO67ABA5ZGHXIBDDO3BBBB2I7GFC5N2NZV3VPKA"
)

TONAPI_HEADERS = {"Authorization": f"Bearer {TONAPI_KEY}"}

# Poll interval (seconds) for checking new txs
CHECK_INTERVAL = 5

# ---- BOT / DISPATCHER ----
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---- In-memory storage (simple, fileless) ----
# user_data: chat_id -> {"wallet": str | None, "notify": bool, "last_hash": Optional[str]}
user_data: Dict[int, Dict] = {}

# ---- Keyboards ----
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
                InlineKeyboardButton(text="📜 История", callback_data="history")
            ],
            [
                InlineKeyboardButton(text="🔔 Вкл/Выкл уведомления", callback_data="toggle")
            ]
        ]
    )

# ---- TONAPI helpers ----

def tonapi_get_account(address: str) -> Optional[dict]:
    """Get account info (balance, jettons, etc) from tonapi."""
    url = f"https://tonapi.io/v2/accounts/{address}"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("tonapi_get_account error: %s", e)
        return None

def tonapi_get_transactions(address: str, limit: int = 10) -> List[dict]:
    """Get transactions from tonapi explorer endpoint."""
    url = f"https://tonapi.io/v2/explorer/getTransactions?address={address}&limit={limit}"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=8)
        r.raise_for_status()
        js = r.json()
        return js.get("transactions", []) or js.get("result", {}).get("transactions", []) or []
    except Exception as e:
        log.debug("tonapi_get_transactions error: %s", e)
        return []

# ---- Formatters ----

def format_tokens_from_account(js: dict) -> List[str]:
    """Return list of 'SYMBOL: amount' from account JSON (handles different shapes)."""
    out = []
    if not js:
        return out

    # ton balance usually in js.get("balance")
    # Many tonapi responses wrap account in "account" or "result"
    # Try common places:
    account = js.get("account") or js.get("result") or js
    # balance
    bal = None
    if isinstance(account, dict):
        if "balance" in account:
            try:
                bal = int(account.get("balance", 0)) / 1e9
            except:
                bal = None

    if bal is not None:
        out.append(f"TON: {bal}")

    # jettons: try several possible fields
    # tonapi often returns 'jettons' as list of dicts with keys 'jetton' and 'balance'
    jets = account.get("jettons") if isinstance(account, dict) else None
    if jets and isinstance(jets, list):
        for j in jets:
            # jetton may be object or include 'jetton' key
            if "jetton" in j and isinstance(j["jetton"], dict):
                name = j["jetton"].get("name") or j["jetton"].get("symbol") or j["jetton"].get("address", "JETTON")
                decimals = int(j["jetton"].get("decimals") or j.get("decimals") or 9)
                balance = int(j.get("balance", 0)) / (10 ** decimals)
                out.append(f"{name}: {balance}")
            else:
                # fallback
                name = j.get("name") or j.get("symbol") or j.get("address") or "JETTON"
                decimals = int(j.get("decimals", 9))
                balance = int(j.get("balance", 0)) / (10 ** decimals)
                out.append(f"{name}: {balance}")

    # some responses include 'tokens' list (toncenter style compatibility)
    tokens = account.get("tokens") if isinstance(account, dict) else None
    if tokens and isinstance(tokens, list):
        for t in tokens:
            name = t.get("name") or t.get("symbol") or "TOKEN"
            decimals = int(t.get("decimals", 9))
            balance = int(t.get("balance", 0)) / (10 ** decimals)
            out.append(f"{name}: {balance}")

    return out

def parse_tokens_from_tx(tx: dict) -> str:
    """Parse a tx object and return a nice multi-line string of tokens + amounts."""
    lines = []
    in_msg = tx.get("in_msg") or {}
    # TON value
    try:
        value = int(in_msg.get("value", 0)) / 1e9
    except:
        value = 0
    if value:
        lines.append(f"TON: {value}")

    # jettons inside in_msg (tonapi often uses 'jettons' or 'jettonTransfers' or 'token_balances')
    # Check common places
    jettons = in_msg.get("jettons") or in_msg.get("jettonTransfers") or tx.get("jettons") or tx.get("token_balances") or []
    if isinstance(jettons, list):
        for j in jettons:
            # jetton may be { 'jetton': {...}, 'amount': ... } or { 'name':..., 'amount':...}
            if "jetton" in j and isinstance(j["jetton"], dict):
                name = j["jetton"].get("name") or j["jetton"].get("symbol") or j["jetton"].get("address", "JETTON")
                decimals = int(j["jetton"].get("decimals") or j.get("decimals") or 9)
                amount = int(j.get("amount", j.get("balance", 0))) / (10 ** decimals)
                lines.append(f"{name}: {amount}")
            else:
                # fallback fields
                name = j.get("name") or j.get("symbol") or j.get("token") or "TOKEN"
                decimals = int(j.get("decimals", 9))
                amount = int(j.get("amount", j.get("balance", 0))) / (10 ** decimals)
                lines.append(f"{name}: {amount}")

    return "\n".join(lines) if lines else "No token info"

# ---- Background checker ----

async def monitor_loop():
    log.info("Monitor loop started (interval %s sec)", CHECK_INTERVAL)
    while True:
        try:
            # iterate over a snapshot copy so additions/removals during loop are safe
            for chat_id, info in list(user_data.items()):
                wallet = info.get("wallet")
                if not wallet:
                    continue

                # get latest txs (limit=1 is fine for notifications)
                txs = tonapi_get_transactions(wallet, limit=1)
                if not txs:
                    continue
                tx = txs[0]
                tx_hash = tx.get("hash")
                if not tx_hash:
                    continue

                last = info.get("last_hash")
                if last != tx_hash:
                    # new tx: prepare message
                    sender = (tx.get("in_msg") or {}).get("source") or tx.get("source") or "Unknown"
                    tokens_txt = parse_tokens_from_tx(tx)
                    msg = f"🔥 <b>Новая транзакция</b>\n👜 <b>Адрес:</b> <code>{wallet}</code>\n👤 <b>От:</b> <code>{sender}</code>\n\n{tokens_txt}"
                    # send if notifications enabled
                    if info.get("notify", True):
                        try:
                            await bot.send_message(chat_id, msg, parse_mode="HTML")
                        except Exception as e:
                            log.debug("Failed to send msg to %s: %s", chat_id, e)
                    # update last_hash
                    user_data[chat_id]["last_hash"] = tx_hash
            # sleep
        except Exception as e:
            log.exception("Error in monitor loop: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)

# ---- Handlers ----

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    cid = message.chat.id
    user_data.setdefault(cid, {"wallet": None, "notify": True, "last_hash": None})
    await message.answer(
        "👋 Бот активирован.\n\n"
        "• Установите кошелёк: /setwallet <адрес>\n"
        "• Или нажмите кнопки: Баланс / История / Вкл/Выкл уведомления",
        reply_markup=main_keyboard()
    )

@dp.message(Command("setwallet"))
async def cmd_setwallet(message: types.Message):
    cid = message.chat.id
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /setwallet <TON_ADDRESS>")
        return
    addr = parts[1].strip()
    user_data.setdefault(cid, {})["wallet"] = addr
    user_data[cid]["last_hash"] = None
    user_data[cid]["notify"] = True
    await message.answer(f"✅ Адрес установлен: <code>{addr}</code>", parse_mode="HTML")

@dp.callback_query()
async def callbacks(call: types.CallbackQuery):
    cid = call.message.chat.id
    info = user_data.get(cid)
    if not info or not info.get("wallet"):
        await call.message.answer("Сначала установите кошелёк: /setwallet <адрес>")
        return

    data = call.data
    wallet = info["wallet"]

    if data == "balance":
        acct = tonapi_get_account(wallet)
        if not acct:
            await call.message.answer("Ошибка получения данных аккаунта.")
            return
        tokens = format_tokens_from_account(acct)
        text = "💰 <b>Баланс:</b>\n" + "\n".join(tokens)
        await call.message.answer(text, parse_mode="HTML")

    elif data == "history":
        txs = tonapi_get_transactions(wallet, limit=5)
        if not txs:
            await call.message.answer("Нет транзакций.")
            return
        text_lines = ["📜 <b>Последние транзакции:</b>\n"]
        for tx in txs:
            sender = (tx.get("in_msg") or {}).get("source") or tx.get("source") or "Unknown"
            th = tx.get("hash", "")[:12]
            tokens_txt = parse_tokens_from_tx(tx)
            text_lines.append(f"🔗 <code>{th}</code> From: <code>{sender}</code>\n{tokens_txt}\n")
        await call.message.answer("\n".join(text_lines), parse_mode="HTML")

    elif data == "toggle":
        user_data.setdefault(cid, {})["notify"] = not user_data.get(cid, {}).get("notify", True)
        state = "включены" if user_data[cid]["notify"] else "выключены"
        await call.message.answer(f"🔔 Уведомления {state}")

# ---- Startup ----
async def main():
    # start monitor background task
    asyncio.create_task(monitor_loop())
    log.info("Starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
