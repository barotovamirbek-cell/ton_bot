# bot.py — TONAPI notifier (aiogram 3.x) — polling, jettons ON, no persistent history file
# Requirements:
#   aiogram==3.1.2
#   requests>=2.31.0
#
# Usage:
#   set environment variable BOT_TOKEN (Telegram bot token)
#   optionally set TONAPI_KEY (otherwise fallback to the provided key)
#   python bot.py

import os
import asyncio
import logging
from typing import Dict, Optional, List

import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ton_bot")

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is required")

# Prefer env TONAPI_KEY, fallback to key you gave earlier so it works immediately.
TONAPI_KEY = os.getenv("TONAPI_KEY",
                       "AGVQICJNL2JNYSAAAAADLI55JSSIIXAJEO67ABA5ZGHXIBDDO3BBBB2I7GFC5N2NZV3VPKA")
TONAPI_HEADERS = {"Authorization": f"Bearer {TONAPI_KEY}"}

CHECK_INTERVAL = 5  # seconds between checks

# ---------------- BOT ----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# in-memory user data: chat_id -> {"wallet": str|None, "notify": bool, "last_hash": Optional[str]}
user_data: Dict[int, Dict] = {}


# ---------------- UI ----------------
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


# ---------------- TONAPI helpers ----------------
def tonapi_account(address: str) -> Optional[dict]:
    """Fetch account info from tonapi (balance + jettons)."""
    url = f"https://tonapi.io/v2/accounts/{address}"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug("tonapi_account error for %s: %s", address, e)
        return None


def tonapi_transactions(address: str, limit: int = 5) -> List[dict]:
    """Fetch transactions (explorer) from tonapi."""
    url = f"https://tonapi.io/v2/explorer/getTransactions?address={address}&limit={limit}"
    try:
        r = requests.get(url, headers=TONAPI_HEADERS, timeout=8)
        r.raise_for_status()
        js = r.json()
        # tonapi sometimes returns list under "transactions" or "result"."transactions"
        if isinstance(js, dict):
            if "transactions" in js:
                return js.get("transactions") or []
            if "result" in js and isinstance(js["result"], dict):
                return js["result"].get("transactions") or []
        # fallback
        return []
    except Exception as e:
        log.debug("tonapi_transactions error for %s: %s", address, e)
        return []


# ---------------- Parsers/Formatters ----------------
def format_account_tokens(js: dict) -> List[str]:
    """Return list of 'SYMBOL: amount' lines from account JSON (tonapi variations handled)."""
    out: List[str] = []
    if not js:
        return out

    # tonapi wraps account under 'account' or returns directly
    acct = js.get("account") or js.get("result") or js

    # Balance (TON)
    bal = None
    if isinstance(acct, dict) and "balance" in acct:
        try:
            bal = int(acct.get("balance", 0)) / 1e9
        except:
            bal = None
    if bal is not None:
        out.append(f"TON: {bal}")

    # Jettons: common tonapi format: acct.get("jettons") => list of dicts with 'jetton' and 'balance'
    jets = acct.get("jettons") if isinstance(acct, dict) else None
    if jets and isinstance(jets, list):
        for j in jets:
            try:
                if isinstance(j.get("jetton"), dict):
                    jt = j["jetton"]
                    name = jt.get("name") or jt.get("symbol") or jt.get("address") or "JETTON"
                    decimals = int(jt.get("decimals") or 9)
                    balance_raw = int(j.get("balance", 0))
                    amount = balance_raw / (10 ** decimals)
                    out.append(f"{name}: {amount}")
                else:
                    # fallback
                    name = j.get("name") or j.get("symbol") or "JETTON"
                    decimals = int(j.get("decimals", 9))
                    balance_raw = int(j.get("balance", 0))
                    amount = balance_raw / (10 ** decimals)
                    out.append(f"{name}: {amount}")
            except Exception:
                continue

    # tokens field (compatibility)
    tokens = acct.get("tokens") if isinstance(acct, dict) else None
    if tokens and isinstance(tokens, list):
        for t in tokens:
            try:
                name = t.get("name") or t.get("symbol") or "TOKEN"
                decimals = int(t.get("decimals", 9))
                balance_raw = int(t.get("balance", 0))
                amount = balance_raw / (10 ** decimals)
                out.append(f"{name}: {amount}")
            except Exception:
                continue

    return out


def parse_tx_direction_and_party(tx: dict, my_wallet: str) -> (str, str):
    """
    Determine whether tx is incoming or outgoing relative to my_wallet.
    Return (direction, other_party_address)
    direction: "Приход" or "Отправка" (received / sent)
    other_party: counterparty address (sender for incoming, recipient for outgoing)
    """
    in_msg = tx.get("in_msg") or {}
    src = in_msg.get("source") or tx.get("source")
    dst = in_msg.get("destination") or tx.get("destination")
    # normalize lower-case comparison
    try:
        if src and isinstance(src, str) and src == my_wallet:
            # sender is me => I sent funds => outgoing
            other = dst or "Unknown"
            return "Отправка", other
        else:
            # otherwise assume incoming if destination equals my_wallet
            if dst and isinstance(dst, str) and dst == my_wallet:
                other = src or "Unknown"
                return "Приход", other
    except Exception:
        pass
    # fallback: if tx has 'out_msgs' etc, try other heuristics
    # default to Приход with sender from in_msg.source
    return "Приход", src or dst or "Unknown"


def parse_tokens_from_tx(tx: dict) -> List[Dict[str, str]]:
    """
    Return list of token dicts found in tx: [{'symbol': 'TON', 'amount': '1.23'}, ...]
    Handles TON (in_msg.value) and jettons/token_balances.
    """
    results = []
    in_msg = tx.get("in_msg") or {}

    # TON value
    try:
        val = int(in_msg.get("value", 0)) / 1e9
    except Exception:
        val = 0
    if val and val != 0:
        results.append({"symbol": "TON", "amount": str(val)})

    # Check common jetton/token fields
    # possible places: in_msg.get("jettons"), in_msg.get("jettonTransfers"), tx.get("token_balances"), tx.get("jettons")
    candidates = []
    for key in ("jettons", "jettonTransfers"):
        v = in_msg.get(key) or tx.get(key)
        if v:
            candidates.append(v)
    # token_balances sometimes at tx level
    tb = tx.get("token_balances") or tx.get("tokens") or tx.get("tokenBalance")
    if tb:
        candidates.append(tb)

    # iterate lists and extract amount/name
    for cand in candidates:
        if not isinstance(cand, list):
            continue
        for entry in cand:
            try:
                # common tonapi structure: { "jetton": {...}, "amount": 12345 }
                if isinstance(entry, dict) and "jetton" in entry and isinstance(entry["jetton"], dict):
                    jt = entry["jetton"]
                    name = jt.get("name") or jt.get("symbol") or jt.get("address") or "JETTON"
                    decimals = int(jt.get("decimals") or entry.get("decimals") or 9)
                    amount_raw = int(entry.get("amount", entry.get("balance", 0)))
                    amount = amount_raw / (10 ** decimals)
                    results.append({"symbol": name, "amount": str(amount)})
                else:
                    # fallback: fields 'name'/'symbol' and 'amount'/'balance'
                    name = entry.get("name") or entry.get("symbol") or entry.get("token") or entry.get("address") or "TOKEN"
                    decimals = int(entry.get("decimals", 9))
                    amount_raw = int(entry.get("amount", entry.get("balance", 0)))
                    amount = amount_raw / (10 ** decimals)
                    results.append({"symbol": name, "amount": str(amount)})
            except Exception:
                continue

    return results


def format_notification(direction: str, counterparty: str, tokens: List[Dict[str, str]], wallet: str) -> str:
    """
    Build notification text in required format:
    Новая транзакция
    Тип: Приход/Отправка
    Кошелёк: <counterparty address>
    Валюта: <token>
    Количество: <amount>
    (multiple tokens: repeated blocks)
    """
    lines = ["🔥 <b>Новая транзакция</b>"]
    lines.append(f"Тип: {direction}")
    lines.append(f"Кошелёк: <code>{counterparty}</code>")
    if not tokens:
        lines.append("Валюта: unknown")
        lines.append("Количество: unknown")
    else:
        for t in tokens:
            sym = t.get("symbol", "TOKEN")
            amt = t.get("amount", "0")
            lines.append(f"Валюта: {sym}")
            lines.append(f"Количество: {amt}")
    return "\n".join(lines)


# ---------------- Monitor Loop ----------------
async def monitor_loop():
    log.info("Monitor started, interval=%s sec", CHECK_INTERVAL)
    while True:
        try:
            # snapshot of users to avoid mutation issues
            for chat_id, info in list(user_data.items()):
                wallet = info.get("wallet")
                if not wallet:
                    continue
                txs = tonapi_transactions(wallet, limit=1)
                if not txs:
                    continue
                tx = txs[0]
                tx_hash = tx.get("hash")
                if not tx_hash:
                    continue
                if info.get("last_hash") != tx_hash:
                    # new tx
                    direction, counterparty = parse_tx_direction_and_party(tx, wallet)
                    tokens = parse_tokens_from_tx(tx)
                    msg = format_notification(direction, counterparty, tokens, wallet)
                    # send if notifications enabled
                    if info.get("notify", True):
                        try:
                            await bot.send_message(chat_id, msg, parse_mode="HTML")
                        except Exception as e:
                            log.debug("send_message failed: %s", e)
                    # update last_hash
                    user_data.setdefault(chat_id, {})["last_hash"] = tx_hash
        except Exception as e:
            log.exception("Error in monitor loop: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)


# ---------------- Handlers ----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    cid = message.chat.id
    user_data.setdefault(cid, {"wallet": None, "notify": True, "last_hash": None})
    await message.answer(
        "👋 Бот включён.\n\n"
        "• Установите кошелёк: /setwallet <адрес>\n"
        "• Или используйте кнопки: Баланс / История / Вкл/Выкл уведомления",
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
        acct = tonapi_account(wallet)
        if not acct:
            await call.message.answer("Ошибка получения данных аккаунта.")
            return
        lines = format_account_tokens(acct)
        if not lines:
            await call.message.answer("Баланс: 0 TON")
            return
        await call.message.answer("💰 Баланс:\n" + "\n".join(lines))

    elif data == "history":
        txs = tonapi_transactions(wallet, limit=5)
        if not txs:
            await call.message.answer("📜 История пустая.")
            return
        parts = []
        for tx in txs:
            th = tx.get("hash", "")[:12]
            direction, cp = parse_tx_direction_and_party(tx, wallet)
            tokens = parse_tokens_from_tx(tx)
            # compact tokens into string lines
            token_lines = []
            for t in tokens:
                token_lines.append(f"{t['symbol']}: {t['amount']}")
            tokens_text = "\n".join(token_lines) if token_lines else "No token info"
            parts.append(f"🔗 <code>{th}</code>\nТип: {direction}\nПартнёр: <code>{cp}</code>\n{tokens_text}\n")
        await call.message.answer("\n".join(parts), parse_mode="HTML")

    elif data == "toggle":
        user_data.setdefault(cid, {})["notify"] = not user_data.get(cid, {}).get("notify", True)
        state = "включены" if user_data[cid]["notify"] else "выключены"
        await call.message.answer(f"🔔 Уведомления {state}")


# ---------------- Startup ----------------
async def main():
    # start background monitor
    asyncio.create_task(monitor_loop())
    log.info("Starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
