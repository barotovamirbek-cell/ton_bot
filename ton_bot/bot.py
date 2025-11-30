# bot.py
import os
import asyncio
import json
import time
from typing import Optional, List, Dict, Any

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.filters import Command
from aiogram.client.bot import DefaultBotProperties

# -------------------------
# Настройки (через env)
# -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TON_API_KEY = os.getenv("TON_API_KEY", "")  # optional (toncenter)
TONCENTER_BASE = os.getenv("TONCENTER_BASE", "https://toncenter.com/api/v2")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 8))
STORAGE_FILE = os.getenv("STORAGE_FILE", "state.json")

if not TELEGRAM_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN не задана в окружении")

HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

# -------------------------
# Утилиты: escape + storage
# -------------------------
def escape_html(text: Optional[str]) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def load_state() -> dict:
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict):
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()
if "chat_monitors" not in state:
    state["chat_monitors"] = {}

def get_monitor(chat_id: int) -> Optional[dict]:
    return state["chat_monitors"].get(str(chat_id))

def set_monitor(chat_id: int, address: str, last_lt: Optional[str] = None):
    state["chat_monitors"][str(chat_id)] = {"address": address, "last_lt": last_lt, "active": True}
    save_state(state)

def stop_monitor(chat_id: int):
    mon = state["chat_monitors"].get(str(chat_id))
    if mon:
        mon["active"] = False
        save_state(state)

# -------------------------
# HTTP helpers
# -------------------------
async def http_get(session: aiohttp.ClientSession, path: str, params: dict = None) -> Optional[dict]:
    url = f"{TONCENTER_BASE}/{path}"
    try:
        async with session.get(url, params=params, headers=HEADERS, timeout=25) as resp:
            return await resp.json()
    except Exception as e:
        print("http_get error:", e)
        return None

# -------------------------
# API wrappers (Toncenter v2 style) - resilient
# -------------------------
async def api_get_address_info(session: aiohttp.ClientSession, address: str) -> dict:
    res = await http_get(session, "getAddressInformation", {"address": address})
    return res or {}

async def api_get_transactions(session: aiohttp.ClientSession, address: str, limit: int = 20, to_lt: Optional[str] = None) -> List[dict]:
    params = {"address": address, "limit": limit}
    if to_lt:
        params["to_lt"] = to_lt
    res = await http_get(session, "getTransactions", params)
    if not res:
        return []
    # Toncenter may return {"ok": True, "result": [...]}
    return res.get("result", []) if isinstance(res, dict) else []

async def api_get_jettons(session: aiohttp.ClientSession, address: str) -> List[dict]:
    # Toncenter v2 may include tokens inside getAddressInformation, try that first
    info = await api_get_address_info(session, address)
    tokens = []
    for tok in info.get("result", {}).get("tokens", []) if isinstance(info.get("result", {}), dict) else []:
        try:
            sym = tok.get("symbol") or tok.get("name") or "UNKNOWN"
            bal = int(tok.get("balance", 0))
            decimals = int(tok.get("decimals", 0)) if tok.get("decimals") else None
            tokens.append({"symbol": sym, "balance": bal, "decimals": decimals})
        except Exception:
            continue
    return tokens

# -------------------------
# Helpers: LT extraction and deep tx formatting
# -------------------------
def extract_lt(tx: Dict[str, Any]) -> Optional[str]:
    # try many places
    if not isinstance(tx, dict):
        return None
    if tx.get("lt"):
        return str(tx.get("lt"))
    tr_id = tx.get("transaction_id") or {}
    if isinstance(tr_id, dict) and tr_id.get("lt"):
        return str(tr_id.get("lt"))
    in_msg = tx.get("in_msg") or {}
    if isinstance(in_msg, dict) and in_msg.get("lt"):
        return str(in_msg.get("lt"))
    for m in tx.get("out_msgs", []) or []:
        if isinstance(m, dict) and m.get("lt"):
            return str(m.get("lt"))
    return None

def format_amount_from_value(value: Optional[Any]) -> str:
    try:
        v = int(value or 0)
        return f"{v / 1_000_000_000:.9f} TON".rstrip("0").rstrip(".")
    except Exception:
        return "0 TON"

def fmt_tx_detailed(tx: Dict[str, Any], address: str) -> str:
    # produce a multi-line, very detailed representation
    lt = extract_lt(tx) or "N/A"
    txid = None
    if tx.get("id"):
        txid = tx.get("id")
    elif isinstance(tx.get("transaction_id"), dict):
        txid = tx.get("transaction_id").get("hash") or tx.get("transaction_id").get("lt")
    txid = txid or "N/A"

    utime = tx.get("utime") or tx.get("created_at") or int(time.time())
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(utime)))

    # Determine direction and amounts
    analysis = analyze_transaction_for_address(tx, address)
    net_nano = analysis.get("net", 0)
    direction = analysis.get("direction", "self/none")

    # fee (if available)
    fee_nano = None
    # Toncenter may include fee in result.transaction.fee or tx.get('fee')
    if isinstance(tx.get("fee"), (int, str)):
        try:
            fee_nano = int(tx.get("fee"))
        except Exception:
            fee_nano = None
    else:
        # try deeper
        fee_candidate = tx.get("utime")  # placeholder; many formats don't include fee explicitly
        fee_nano = None

    # from/to
    in_msg = tx.get("in_msg") or {}
    out_msgs = tx.get("out_msgs") or []

    src = escape_html(in_msg.get("source") or (out_msgs[0].get("source") if out_msgs else "Unknown"))
    dst = escape_html(in_msg.get("destination") or (out_msgs[0].get("destination") if out_msgs else "Unknown"))

    # jetton/token details (if present in messages)
    jetton_lines = []
    # Some APIs include token info in message.token_balances or tx.get('tokens')
    # we try to detect various possibilities
    if in_msg:
        for tok in in_msg.get("token_balances", []) or []:
            sym = tok.get("symbol") or tok.get("token") or "TOKEN"
            val = tok.get("value") or tok.get("balance") or 0
            jetton_lines.append(f"{escape_html(str(sym))}: {escape_html(str(val))}")
    # scan out_msgs for token changes
    for m in out_msgs:
        for tok in m.get("token_balances", []) or []:
            sym = tok.get("symbol") or tok.get("token") or "TOKEN"
            val = tok.get("value") or tok.get("balance") or 0
            jetton_lines.append(f"{escape_html(str(sym))}: {escape_html(str(val))}")

    # body (if present) — don't print raw binary; just indicate presence or small text
    body_note = ""
    body = in_msg.get("body") if isinstance(in_msg, dict) else None
    if body:
        # body could be hex BOC; we'll show first 200 chars escaped
        try:
            body_str = str(body)
            if len(body_str) > 200:
                body_note = escape_html(body_str[:200]) + "..."
            else:
                body_note = escape_html(body_str)
        except Exception:
            body_note = "(body present)"

    lines = []
    lines.append(f"<b>TX</b> LT={escape_html(str(lt))}  |  <b>ID</b> {escape_html(str(txid))}")
    lines.append(f"<b>Time:</b> {escape_html(ts)}")
    lines.append(f"<b>Direction:</b> {escape_html(direction)}")
    lines.append(f"<b>Net:</b> {format_amount_from_value(net_nano)}")
    if fee_nano is not None:
        try:
            lines.append(f"<b>Fee:</b> {format_amount_from_value(fee_nano)}")
        except Exception:
            pass
    lines.append(f"<b>From:</b> <code>{src}</code>")
    lines.append(f"<b>To:</b> <code>{dst}</code>")

    if jetton_lines:
        lines.append("<b>Jettons / token changes:</b>")
        for jl in jetton_lines:
            lines.append(escape_html(jl))

    if body_note:
        lines.append("<b>Body:</b>")
        lines.append(body_note)

    # If there are raw messages in out_msgs, list count
    if out_msgs:
        lines.append(f"<b>Out messages:</b> {len(out_msgs)}")

    return "\n".join(lines)

# reuse analyze_transaction_for_address from earlier (keeps original logic)
def analyze_transaction_for_address(tx: dict, address: str) -> dict:
    incoming = outgoing = 0
    in_msg = tx.get("in_msg")
    if in_msg:
        try:
            val = int(in_msg.get("value", 0) or 0)
        except Exception:
            val = 0
        src = in_msg.get("source")
        dest = in_msg.get("destination")
        if dest and dest.lower() == address.lower():
            incoming += val
        if src and src.lower() == address.lower():
            outgoing += val
    out_msgs = tx.get("out_msgs") or []
    for m in out_msgs:
        try:
            val = int(m.get("value", 0) or 0)
        except Exception:
            val = 0
        src = m.get("source")
        dest = m.get("destination")
        if dest and dest.lower() == address.lower():
            incoming += val
        if src and src.lower() == address.lower():
            outgoing += val
    net = incoming - outgoing
    direction = "incoming" if net > 0 else ("outgoing" if net < 0 else "self/none")
    return {"incoming": incoming, "outgoing": outgoing, "net": net, "direction": direction}

# -------------------------
# Инициализация бота
# -------------------------
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# -------------------------
# Команды
# -------------------------
@dp.message(Command(commands=["start"]))
async def cmd_start(msg: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="/balance")
    kb.button(text="/transactions")
    kb.button(text="/monitor_start")
    kb.button(text="/monitor_stop")
    await msg.answer(
        "Привет! Я бот для детальной истории TON.\n\n"
        "Команды:\n"
        "/balance - показать баланс\n"
        "/transactions [N] - показать последние N транзакций (N<=50)\n"
        "/setaddr <address> - установить адрес для этого чата\n"
        "/monitor_start - включить уведомления о новых транзакциях\n"
        "/monitor_stop - отключить уведомления\n",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )

@dp.message(Command(commands=["setaddr"]))
async def cmd_setaddr(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Использование: /setaddr <TON address>")
        return
    addr = parts[1].strip()
    mon = get_monitor(msg.chat.id)
    last_lt = mon["last_lt"] if mon else None
    set_monitor(msg.chat.id, addr, last_lt)
    await msg.answer(f"Адрес установлен: <code>{escape_html(addr)}</code>")

@dp.message(Command(commands=["balance"]))
async def cmd_balance(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    addr = mon["address"] if mon and mon.get("address") else None
    if not addr:
        await msg.answer("Сначала установите адрес: /setaddr <address>")
        return
    async with aiohttp.ClientSession() as sess:
        info = await api_get_address_info(sess, addr)
    # try to read balance robustly
    balance = None
    try:
        if info.get("ok") and isinstance(info.get("result"), dict):
            b = info["result"].get("balance")
            if isinstance(b, (str, int)):
                balance = int(b)
    except Exception:
        balance = None
    if balance is None:
        await msg.answer(f"Не удалось получить баланс для <code>{escape_html(addr)}</code>")
    else:
        await msg.answer(f"Баланс для <code>{escape_html(addr)}</code>: <b>{format_amount_from_value(balance)}</b>")

@dp.message(Command(commands=["transactions"]))
async def cmd_transactions(msg: types.Message):
    parts = msg.text.split()
    n = 10
    if len(parts) >= 2:
        try:
            n = min(50, max(1, int(parts[1])))
        except:
            n = 10
    mon = get_monitor(msg.chat.id)
    addr = mon["address"] if mon and mon.get("address") else None
    if not addr:
        await msg.answer("Сначала установите адрес: /setaddr <address>")
        return
    async with aiohttp.ClientSession() as sess:
        txs = await api_get_transactions(sess, addr, limit=n)
    if not txs:
        await msg.answer(f"Транзакций для <code>{escape_html(addr)}</code> не найдено")
        return
    # produce detailed blocks
    for tx in txs:
        text = fmt_tx_detailed(tx, addr)
        # send each transaction as separate message to avoid 4096 char limit
        try:
            await msg.answer(text)
        except Exception as e:
            # fallback: send shorter summary
            lt = extract_lt(tx) or "N/A"
            await msg.answer(f"LT={escape_html(str(lt))} | {escape_html(str(tx.get('utime') or ''))}")

@dp.message(Command(commands=["monitor_start"]))
async def cmd_monitor_start(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала установите адрес: /setaddr <address>")
        return
    state["chat_monitors"][str(msg.chat.id)]["active"] = True
    save_state(state)
    await msg.answer(f"Мониторинг включён для <code>{escape_html(mon['address'])}</code>")

@dp.message(Command(commands=["monitor_stop"]))
async def cmd_monitor_stop(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon:
        await msg.answer("Монитор не был настроен.")
        return
    state["chat_monitors"][str(msg.chat.id)]["active"] = False
    save_state(state)
    await msg.answer(f"Мониторинг отключён для <code>{escape_html(mon['address'])}</code>")

@dp.message(Command(commands=["tokens"]))
async def cmd_tokens(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    addr = mon["address"] if mon and mon.get("address") else None
    if not addr:
        await msg.answer("Сначала установите адрес: /setaddr <address>")
        return
    async with aiohttp.ClientSession() as sess:
        tokens = await api_get_jettons(sess, addr)
    if not tokens:
        await msg.answer("Токенов не найдено или ошибка API.")
        return
    lines = []
    for t in tokens:
        sym = escape_html(str(t.get("symbol", "UNKNOWN")))
        bal = t.get("balance", 0)
        dec = t.get("decimals")
        if dec:
            try:
                bal_display = int(bal) / (10 ** int(dec))
            except:
                bal_display = int(bal)
        else:
            bal_display = bal
        lines.append(f"{sym}: {escape_html(str(bal_display))}")
    await msg.answer("<b>Jettons / tokens:</b>\n" + "\n".join(lines))

# -------------------------
# Background poll loop (monitor)
# -------------------------
async def poll_loop():
    async with aiohttp.ClientSession() as sess:
        while True:
            monitors = dict(state.get("chat_monitors", {}))
            for chat_id_str, info in monitors.items():
                try:
                    chat_id = int(chat_id_str)
                except:
                    continue
                address = info.get("address")
                last_lt = info.get("last_lt")
                active = info.get("active", False)
                if not active or not address:
                    continue
                try:
                    txs = await api_get_transactions(sess, address, limit=20)
                    if not txs:
                        continue
                    # determine newest LT using extract_lt
                    newest_lt = extract_lt(txs[0]) or None
                    if not newest_lt:
                        # nothing to do
                        continue
                    if not last_lt:
                        # initialize
                        state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
                        save_state(state)
                        continue
                    # collect new txs with lt > last_lt
                    new_items = []
                    for tx in txs:
                        tx_lt = extract_lt(tx)
                        try:
                            if tx_lt and int(tx_lt) > int(last_lt):
                                new_items.append(tx)
                        except:
                            # if non-int compare lexicographically
                            if tx_lt and str(tx_lt) > str(last_lt):
                                new_items.append(tx)
                    # sort ascending by lt
                    new_items = sorted(new_items, key=lambda t: int(extract_lt(t) or "0"))
                    for tx in new_items:
                        text = fmt_tx_detailed(tx, address)
                        try:
                            await bot.send_message(chat_id, text)
                        except Exception as e:
                            # fallback: short text
                            lt = extract_lt(tx) or "N/A"
                            await bot.send_message(chat_id, f"Новая транзакция LT={escape_html(str(lt))}")
                    if new_items:
                        last_seen = extract_lt(new_items[-1])
                        state["chat_monitors"][chat_id_str]["last_lt"] = last_seen
                        save_state(state)
                except Exception as e:
                    print("poll error for", address, e)
            await asyncio.sleep(POLL_INTERVAL)

# -------------------------
# Запуск
# -------------------------
async def main():
    # start poll loop
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
