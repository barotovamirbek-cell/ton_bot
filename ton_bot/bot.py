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
# Настройки (env)
# -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TON_API_KEY = os.getenv("TON_API_KEY", "")  # optional
TONCENTER_BASE = os.getenv("TONCENTER_BASE", "https://toncenter.com/api/v2")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 8))
STATE_FILE = os.getenv("STORAGE_FILE", "state.json")

if not TELEGRAM_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN не задана в окружении")

HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

# -------------------------
# Безопасное экранирование HTML для Telegram
# -------------------------
def escape_html(text: Optional[str]) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def safe(text: Optional[str]) -> str:
    return escape_html(text)

# -------------------------
# Умный state storage: файл -> fallback in-memory
# -------------------------
_in_memory_state: Dict[str, Any] = {"chat_monitors": {}}
_state_file_writable = True

def _try_load_state() -> Dict[str, Any]:
    global _state_file_writable
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            # try create empty file
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"chat_monitors": {}}, f)
                return {"chat_monitors": {}}
            except Exception:
                _state_file_writable = False
                return _in_memory_state
    except Exception:
        _state_file_writable = False
        return _in_memory_state

def _try_save_state(state: Dict[str, Any]):
    global _state_file_writable, _in_memory_state
    if _state_file_writable:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            return
        except Exception as e:
            print("Warning: cannot write state file, switching to memory-only. Error:", e)
            _state_file_writable = False
            _in_memory_state = state
    else:
        # keep in memory
        _in_memory_state = state

state = _try_load_state()
if "chat_monitors" not in state:
    state["chat_monitors"] = {}
_try_save_state(state)

# helpers for state
def get_monitor(chat_id: int) -> Optional[dict]:
    return state.get("chat_monitors", {}).get(str(chat_id))

def set_monitor(chat_id: int, address: str, last_lt: Optional[str] = None, active: bool = True):
    state.setdefault("chat_monitors", {})
    state["chat_monitors"][str(chat_id)] = {"address": address, "last_lt": last_lt, "active": active}
    _try_save_state(state)

def stop_monitor(chat_id: int):
    mon = state.get("chat_monitors", {}).get(str(chat_id))
    if mon:
        mon["active"] = False
        _try_save_state(state)

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
# Toncenter wrappers (resilient)
# -------------------------
async def api_get_address_info(session: aiohttp.ClientSession, address: str) -> dict:
    return await (http_get(session, "getAddressInformation", {"address": address}) or {})

async def api_get_transactions(session: aiohttp.ClientSession, address: str, limit: int = 20, to_lt: Optional[str] = None) -> List[dict]:
    params = {"address": address, "limit": limit}
    if to_lt:
        params["to_lt"] = to_lt
    res = await http_get(session, "getTransactions", params)
    if not res:
        return []
    if isinstance(res, dict) and res.get("ok"):
        return res.get("result", []) or []
    return []

async def api_get_jetton_balances(session: aiohttp.ClientSession, address: str) -> List[dict]:
    # Toncenter offers getJettonBalances or similar endpoints in some setups; try robustly
    res = await http_get(session, "getJettonBalances", {"address": address})
    if res and isinstance(res, dict) and res.get("ok"):
        return res.get("result", []) or []
    # fallback: try to read tokens from getAddressInformation
    info = await api_get_address_info(session, address)
    tokens = []
    if info.get("ok") and isinstance(info.get("result"), dict):
        for t in info["result"].get("tokens", []) or []:
            tokens.append(t)
    return tokens

async def api_get_jetton_transfers(session: aiohttp.ClientSession, address: str, limit: int = 20) -> List[dict]:
    res = await http_get(session, "getJettonTransfers", {"address": address, "limit": limit})
    if res and isinstance(res, dict) and res.get("ok"):
        return res.get("result", []) or []
    return []

# -------------------------
# Utils: LT extraction & tx formatting
# -------------------------
def extract_lt(tx: Dict[str, Any]) -> Optional[str]:
    if not isinstance(tx, dict):
        return None
    if tx.get("lt"):
        return str(tx.get("lt"))
    tr = tx.get("transaction_id") or {}
    if isinstance(tr, dict) and tr.get("lt"):
        return str(tr.get("lt"))
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

def analyze_transaction_for_address(tx: dict, address: str) -> dict:
    incoming = outgoing = 0
    in_msg = tx.get("in_msg")
    if in_msg:
        try:
            val = int(in_msg.get("value", 0) or 0)
        except:
            val = 0
        src = in_msg.get("source")
        dst = in_msg.get("destination")
        if dst and dst.lower() == address.lower():
            incoming += val
        if src and src.lower() == address.lower():
            outgoing += val
    for m in tx.get("out_msgs", []) or []:
        try:
            val = int(m.get("value", 0) or 0)
        except:
            val = 0
        src = m.get("source")
        dst = m.get("destination")
        if dst and dst.lower() == address.lower():
            incoming += val
        if src and src.lower() == address.lower():
            outgoing += val
    net = incoming - outgoing
    direction = "incoming" if net > 0 else ("outgoing" if net < 0 else "self/none")
    return {"incoming": incoming, "outgoing": outgoing, "net": net, "direction": direction}

def fmt_tx_detailed(tx: Dict[str, Any], address: str) -> str:
    lt = extract_lt(tx) or "N/A"
    txid = "N/A"
    if tx.get("id"):
        txid = tx.get("id")
    elif isinstance(tx.get("transaction_id"), dict):
        txid = tx.get("transaction_id").get("hash") or tx.get("transaction_id").get("lt") or "N/A"
    utime = tx.get("utime") or tx.get("created_at") or int(time.time())
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(utime)))
    analysis = analyze_transaction_for_address(tx, address)
    net_nano = analysis.get("net", 0)
    direction = analysis.get("direction", "self/none")
    in_msg = tx.get("in_msg") or {}
    out_msgs = tx.get("out_msgs") or []
    src = escape_html(in_msg.get("source") or (out_msgs[0].get("source") if out_msgs else "Unknown"))
    dst = escape_html(in_msg.get("destination") or (out_msgs[0].get("destination") if out_msgs else "Unknown"))
    jetton_lines = []
    if in_msg and isinstance(in_msg, dict):
        for tok in in_msg.get("token_balances", []) or []:
            sym = tok.get("symbol") or tok.get("token") or "TOKEN"
            val = tok.get("value") or tok.get("balance") or 0
            jetton_lines.append(f"{escape_html(str(sym))}: {escape_html(str(val))}")
    for m in out_msgs:
        for tok in m.get("token_balances", []) or []:
            sym = tok.get("symbol") or tok.get("token") or "TOKEN"
            val = tok.get("value") or tok.get("balance") or 0
            jetton_lines.append(f"{escape_html(str(sym))}: {escape_html(str(val))}")
    body_note = ""
    body = in_msg.get("body") if isinstance(in_msg, dict) else None
    if body:
        try:
            body_str = str(body)
            body_note = escape_html(body_str[:200]) + ("..." if len(body_str) > 200 else "")
        except:
            body_note = "(body present)"
    lines = []
    lines.append(f"<b>TX</b> LT={escape_html(str(lt))}  |  <b>ID</b> {escape_html(str(txid))}")
    lines.append(f"<b>Time:</b> {escape_html(ts)}")
    lines.append(f"<b>Direction:</b> {escape_html(direction)}")
    lines.append(f"<b>Net:</b> {format_amount_from_value(net_nano)}")
    lines.append(f"<b>From:</b> <code>{src}</code>")
    lines.append(f"<b>To:</b> <code>{dst}</code>")
    if jetton_lines:
        lines.append("<b>Jettons / token changes:</b>")
        for jl in jetton_lines:
            lines.append(escape_html(jl))
    if body_note:
        lines.append("<b>Body:</b>")
        lines.append(body_note)
    if out_msgs:
        lines.append(f"<b>Out messages:</b> {len(out_msgs)}")
    return "\n".join(lines)

# -------------------------
# Инициализация бота (aiogram 3.x)
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
    kb.button(text="/tokens")
    kb.button(text="/token_tx")
    kb.button(text="/monitor_start")
    kb.button(text="/monitor_stop")
    await msg.answer(
        "Привет! Я бот для детальной истории TON.\n\n"
        "Команды:\n"
        "/setaddr <address>\n"
        "/balance - показать баланс\n"
        "/transactions [N] - показать последние N транзакций\n"
        "/tokens - показать jetton балансы\n"
        "/token_tx - показать jetton транзакции\n"
        "/monitor_start - включить уведомления\n"
        "/monitor_stop - отключить\n",
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
    set_monitor(msg.chat.id, addr, last_lt, active=True)
    await msg.answer(f"Адрес установлен: <code>{escape_html(addr)}</code>")

@dp.message(Command(commands=["balance"]))
async def cmd_balance(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    addr = mon["address"] if mon and mon.get("address") else None
    if not addr:
        await msg.answer("Сначала /setaddr <address>")
        return
    async with aiohttp.ClientSession() as sess:
        info = await api_get_address_info(sess, addr)
    balance = None
    try:
        if info.get("ok") and isinstance(info.get("result"), dict):
            b = info["result"].get("balance")
            if isinstance(b, (str, int)):
                balance = int(b)
    except:
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
        await msg.answer("Сначала /setaddr <address>")
        return
    async with aiohttp.ClientSession() as sess:
        txs = await api_get_transactions(sess, addr, limit=n)
    if not txs:
        await msg.answer(f"Транзакций для <code>{escape_html(addr)}</code> не найдено")
        return
    for tx in txs:
        text = fmt_tx_detailed(tx, addr)
        try:
            await msg.answer(text)
        except Exception:
            lt = extract_lt(tx) or "N/A"
            await msg.answer(f"LT={escape_html(str(lt))}")

@dp.message(Command(commands=["tokens"]))
async def cmd_tokens(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    addr = mon["address"] if mon and mon.get("address") else None
    if not addr:
        await msg.answer("Сначала /setaddr <address>")
        return
    async with aiohttp.ClientSession() as sess:
        tokens = await api_get_jetton_balances(sess, addr)
    if not tokens:
        await msg.answer("Токенов не найдено или ошибка API.")
        return
    out = ["<b>Jetton балансы:</b>"]
    # tokens may be of different shapes; handle common fields
    for t in tokens:
        # try detection
        sym = t.get("symbol") or (t.get("jetton", {}) or {}).get("symbol") or t.get("name") or "TOKEN"
        bal = t.get("balance") or t.get("value") or (t.get("jetton", {}) or {}).get("balance") or 0
        dec = t.get("decimals") or (t.get("jetton", {}) or {}).get("decimals")
        try:
            if dec:
                bal_display = int(bal) / (10 ** int(dec))
            else:
                bal_display = int(bal)
        except:
            bal_display = bal
        out.append(f"{escape_html(str(sym))} — {escape_html(str(bal_display))}")
    await msg.answer("\n".join(out))

@dp.message(Command(commands=["token_tx"]))
async def cmd_token_tx(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    addr = mon["address"] if mon and mon.get("address") else None
    if not addr:
        await msg.answer("Сначала /setaddr <address>")
        return
    async with aiohttp.ClientSession() as sess:
        txs = await api_get_jetton_transfers(sess, addr, limit=20)
    if not txs:
        await msg.answer("Jetton транзакции не найдены")
        return
    out = ["<b>Jetton транзакции:</b>"]
    for t in txs[:20]:
        ts = t.get("utime") or int(time.time())
        dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
        amount = t.get("amount") or t.get("value") or 0
        jet = (t.get("jetton") or {}).get("symbol") or t.get("symbol") or "JET"
        sender = escape_html(t.get("sender") or t.get("from") or "?")
        receiver = escape_html(t.get("receiver") or t.get("to") or "?")
        out.append(f"{escape_html(dt)} | {escape_html(str(amount))} {escape_html(jet)} | {sender} → {receiver}")
    await msg.answer("\n".join(out))

@dp.message(Command(commands=["monitor_start"]))
async def cmd_monitor_start(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon or not mon.get("address"):
        await msg.answer("Сначала /setaddr <address>")
        return
    state["chat_monitors"][str(msg.chat.id)]["active"] = True
    _try_save_state(state)
    await msg.answer(f"Мониторинг включён для <code>{escape_html(state['chat_monitors'][str(msg.chat.id)]['address'])}</code>")

@dp.message(Command(commands=["monitor_stop"]))
async def cmd_monitor_stop(msg: types.Message):
    mon = get_monitor(msg.chat.id)
    if not mon:
        await msg.answer("Монитор не был настроен.")
        return
    state["chat_monitors"][str(msg.chat.id)]["active"] = False
    _try_save_state(state)
    await msg.answer(f"Мониторинг отключён для <code>{escape_html(mon['address'])}</code>")

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
                    newest_lt = extract_lt(txs[0]) or None
                    if not newest_lt:
                        continue
                    if not last_lt:
                        state["chat_monitors"][chat_id_str]["last_lt"] = newest_lt
                        _try_save_state(state)
                        continue
                    new_items = []
                    for tx in txs:
                        tx_lt = extract_lt(tx)
                        try:
                            if tx_lt and int(tx_lt) > int(last_lt):
                                new_items.append(tx)
                        except:
                            if tx_lt and str(tx_lt) > str(last_lt):
                                new_items.append(tx)
                    new_items = sorted(new_items, key=lambda t: int(extract_lt(t) or "0"))
                    for tx in new_items:
                        text = fmt_tx_detailed(tx, address)
                        try:
                            await bot.send_message(chat_id, text)
                        except Exception as e:
                            lt = extract_lt(tx) or "N/A"
                            await bot.send_message(chat_id, f"Новая транзакция LT={escape_html(str(lt))}")
                    if new_items:
                        last_seen = extract_lt(new_items[-1])
                        state["chat_monitors"][chat_id_str]["last_lt"] = last_seen
                        _try_save_state(state)
                except Exception as e:
                    print("poll error for", address, e)
            await asyncio.sleep(POLL_INTERVAL)

# -------------------------
# Запуск
# -------------------------
async def main():
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
