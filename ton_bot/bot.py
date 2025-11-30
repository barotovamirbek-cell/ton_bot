# bot.py — устойчивая рабочая версия (копировать целиком)
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
# CONFIG (env)
# -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TON_API_KEY = os.getenv("TON_API_KEY", "")
TONCENTER_BASE = os.getenv("TONCENTER_BASE", "https://toncenter.com/api/v2")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 8))
STATE_FILE = os.getenv("STORAGE_FILE", "state.json")

if not TELEGRAM_TOKEN:
    raise SystemExit("Установи TELEGRAM_BOT_TOKEN в переменных окружения")

HEADERS = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

# -------------------------
# Simple safe HTML escape (no external deps)
# -------------------------
def safe(text: Optional[Any]) -> str:
    if text is None:
        return ""
    s = str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# -------------------------
# State: try file, fallback to memory
# -------------------------
_state_in_memory: Dict[str, Any] = {"chat_monitors": {}}
_state_file_writable = True

def load_state() -> Dict[str, Any]:
    global _state_file_writable, _state_in_memory
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            # try create
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"chat_monitors": {}}, f)
                return {"chat_monitors": {}}
            except Exception:
                _state_file_writable = False
                return _state_in_memory
    except Exception:
        _state_file_writable = False
        return _state_in_memory

def save_state(state: Dict[str, Any]):
    global _state_file_writable, _state_in_memory
    if _state_file_writable:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            return
        except Exception as e:
            print("Warning: cannot write state file, switching to memory-only:", e)
            _state_file_writable = False
            _state_in_memory = state
    else:
        _state_in_memory = state

state = load_state()
if "chat_monitors" not in state:
    state["chat_monitors"] = {}
save_state(state)

def get_monitor(chat_id: int) -> Optional[dict]:
    return state.get("chat_monitors", {}).get(str(chat_id))

def set_monitor(chat_id: int, address: str, last_lt: Optional[str] = None):
    state.setdefault("chat_monitors", {})
    state["chat_monitors"][str(chat_id)] = {"address": address, "last_lt": last_lt, "active": True}
    save_state(state)

def stop_monitor(chat_id: int):
    mon = state.get("chat_monitors", {}).get(str(chat_id))
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
            # sometimes API returns non-json — guard it
            try:
                return await resp.json()
            except Exception:
                text = await resp.text()
                print("http_get non-json response:", text[:400])
                return None
    except Exception as e:
        print("http_get exception:", e)
        return None

# -------------------------
# TonCenter wrappers (robust)
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
    if isinstance(res, dict) and res.get("ok"):
        return res.get("result", []) or []
    # in case different format
    if isinstance(res, list):
        return res
    return []

async def api_get_jetton_balances(session: aiohttp.ClientSession, address: str) -> List[dict]:
    # try dedicated endpoint, fallback to address info tokens
    res = await http_get(session, "getJettonBalances", {"address": address})
    if res and isinstance(res, dict) and res.get("ok"):
        return res.get("result", []) or []
    # fallback
    info = await api_get_address_info(session, address)
    tokens = []
    try:
        if info.get("ok") and isinstance(info.get("result"), dict):
            tokens = info["result"].get("tokens", []) or []
    except Exception:
        tokens = []
    return tokens

async def api_get_jetton_transfers(session: aiohttp.ClientSession, address: str, limit: int = 20) -> List[dict]:
    res = await http_get(session, "getJettonTransfers", {"address": address, "limit": limit})
    if res and isinstance(res, dict) and res.get("ok"):
        return res.get("result", []) or []
    return []

# -------------------------
# Utilities: LT extraction, analysis, formatting
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
    # final fallback: maybe id or hash
    if tx.get("id"):
        return str(tx.get("id"))
    return None

def format_amount_from_value(value: Optional[Any]) -> str:
    try:
        v = int(value or 0)
        return f"{v / 1_000_000_000:.9f} TON".rstrip("0").rstrip(".")
    except Exception:
        return "0 TON"

def analyze_transaction_for_address(tx: dict, address: str) -> dict:
    incoming = outgoing = 0
    try:
        in_msg = tx.get("in_msg")
        if in_msg and isinstance(in_msg, dict):
            val = int(in_msg.get("value", 0) or 0)
            if in_msg.get("destination") and in_msg.get("destination").lower() == address.lower():
                incoming += val
            if in_msg.get("source") and in_msg.get("source").lower() == address.lower():
                outgoing += val
    except Exception:
        pass
    try:
        for m in tx.get("out_msgs", []) or []:
            val = int(m.get("value", 0) or 0)
            if m.get("destination") and m.get("destination").lower() == address.lower():
                incoming += val
            if m.get("source") and m.get("source").lower() == address.lower():
                outgoing += val
    except Exception:
        pass
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
    net_str = format_amount_from_value(analysis.get("net", 0))
    direction = analysis.get("direction", "self/none")
    in_msg = tx.get("in_msg") or {}
    out_msgs = tx.get("out_msgs") or []
    src = escape_html(in_msg.get("source") or (out_msgs[0].get("source") if out_msgs else "Unknown"))
    dst = escape_html(in_msg.get("destination") or (out_msgs[0].get("destination") if out_msgs else "Unknown"))
    # collect token changes
    jetton_lines = []
    if isinstance(in_msg, dict):
        for tok in in_msg.get("token_balances", []) or []:
            sym = tok.get("symbol") or tok.get("token") or "TOKEN"
            val = tok.get("value") or tok.get("balance") or 0
            jetton_lines.append(f"{safe(str(sym))}: {safe(str(val))}")
    for m in out_msgs:
        for tok in m.get("token_balances", []) or []:
            sym = tok.get("symbol") or tok.get("token") or "TOKEN"
            val = tok.get("value") or tok.get("balance") or 0
            jetton_lines.append(f"{safe(str(sym))}: {safe(str(val))}")
    body_note = ""
    body = in_msg.get("body") if isinstance(in_msg, dict) else None
    if body:
        try:
            bs = str(body)
            body_note = safe(bs[:400]) + ("..." if len(bs) > 400 else "")
        except:
            body_note = "(body present)"
    lines = []
    lines.append(f"<b>TX</b> LT={safe(str(lt))}  |  <b>ID</b> {safe(str(txid))}")
    lines.append(f"<b>Time:</b> {safe(ts)}")
    lines.append(f"<b>Direction:</b> {safe(direction)}")
    lines.append(f"<b>Net:</b> {safe(net_str)}")
    lines.append(f"<b>From:</b> <code>{safe(src)}</code>")
    lines.append(f"<b>To:</b> <code>{safe(dst)}</code>")
    if jetton_lines:
        lines.append("<b>Jettons / token changes:</b>")
        for jl in jetton_lines:
            lines.append(jl)
    if body_note:
        lines.append("<b>Body:</b>")
        lines.append(body_note)
    if out_msgs:
        lines.append(f"<b>Out messages:</b> {len(out_msgs)}")
    return "\n".join(lines)

# -------------------------
# Bot init
# -------------------------
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# -------------------------
# Commands (robust handlers)
# -------------------------
@dp.message(Command(commands=["start"]))
async def cmd_start(msg: types.Message):
    try:
        kb = ReplyKeyboardBuilder()
        kb.button(text="/balance")
        kb.button(text="/transactions")
        kb.button(text="/tokens")
        kb.button(text="/token_tx")
        kb.button(text="/monitor_start")
        kb.button(text="/monitor_stop")
        intro = (
            "Привет! Я бот для истории и мониторинга TON.\n\n"
            "Команды:\n"
            "/setaddr <address>\n"
            "/balance — показать баланс\n"
            "/transactions [N] — показать N транзакций\n"
            "/tokens — показать jetton балансы\n"
            "/token_tx — показать jetton транзакции\n"
            "/monitor_start — включить уведомления\n"
            "/monitor_stop — отключить\n"
        )
        await msg.answer(safe(intro), reply_markup=kb.as_markup(resize_keyboard=True))
    except Exception as e:
        print("start handler error:", e)

@dp.message(Command(commands=["setaddr"]))
async def cmd_setaddr(msg: types.Message):
    try:
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.answer("Использование: /setaddr <TON address>")
            return
        addr = parts[1].strip()
        mon = get_monitor(msg.chat.id)
        last_lt = mon["last_lt"] if mon else None
        set_monitor(msg.chat.id, addr, last_lt)
        await msg.answer(f"Адрес установлен: <code>{safe(addr)}</code>")
    except Exception as e:
        print("setaddr error:", e)
        await msg.answer("Ошибка установки адреса")

@dp.message(Command(commands=["balance"]))
async def cmd_balance(msg: types.Message):
    try:
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
        except Exception:
            balance = None
        if balance is None:
            await msg.answer(f"Не удалось получить баланс для <code>{safe(addr)}</code>")
        else:
            await msg.answer(f"Баланс для <code>{safe(addr)}</code>: <b>{format_amount_from_value(balance)}</b>")
    except Exception as e:
        print("balance handler error:", e)
        await msg.answer("Ошибка при запросе баланса")

@dp.message(Command(commands=["transactions"]))
async def cmd_transactions(msg: types.Message):
    try:
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
            await msg.answer(f"Транзакций для <code>{safe(addr)}</code> не найдено")
            return
        for tx in txs:
            text = fmt_tx_detailed(tx, addr)
            try:
                await msg.answer(text)
            except Exception as e:
                print("send tx detailed error:", e)
                lt = extract_lt(tx) or "N/A"
                try:
                    await msg.answer(f"LT={safe(str(lt))}")
                except:
                    pass
    except Exception as e:
        print("transactions handler error:", e)
        await msg.answer("Ошибка при получении транзакций")

@dp.message(Command(commands=["tokens"]))
async def cmd_tokens(msg: types.Message):
    try:
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
        for t in tokens:
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
            out.append(f"{safe(str(sym))} — {safe(str(bal_display))}")
        await msg.answer("\n".join(out))
    except Exception as e:
        print("tokens handler error:", e)
        await msg.answer("Ошибка при получении токенов")

@dp.message(Command(commands=["token_tx"]))
async def cmd_token_tx(msg: types.Message):
    try:
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
            sender = safe(t.get("sender") or t.get("from") or "?")
            receiver = safe(t.get("receiver") or t.get("to") or "?")
            out.append(f"{safe(dt)} | {safe(str(amount))} {safe(jet)} | {sender} → {receiver}")
        await msg.answer("\n".join(out))
    except Exception as e:
        print("token_tx handler error:", e)
        await msg.answer("Ошибка при получении jetton транзакций")

@dp.message(Command(commands=["monitor_start"]))
async def cmd_monitor_start(msg: types.Message):
    try:
        mon = get_monitor(msg.chat.id)
        if not mon or not mon.get("address"):
            await msg.answer("Сначала /setaddr <address>")
            return
        state["chat_monitors"][str(msg.chat.id)]["active"] = True
        save_state(state)
        await msg.answer(f"Мониторинг включён для <code>{safe(state['chat_monitors'][str(msg.chat.id)]['address'])}</code>")
    except Exception as e:
        print("monitor_start handler error:", e)
        await msg.answer("Ошибка при включении мониторинга")

@dp.message(Command(commands=["monitor_stop"]))
async def cmd_monitor_stop(msg: types.Message):
    try:
        mon = get_monitor(msg.chat.id)
        if not mon:
            await msg.answer("Монитор не был настроен.")
            return
        state["chat_monitors"][str(msg.chat.id)]["active"] = False
        save_state(state)
        await msg.answer(f"Мониторинг отключён для <code>{safe(mon['address'])}</code>")
    except Exception as e:
        print("monitor_stop handler error:", e)
        await msg.answer("Ошибка при выключении мониторинга")

# -------------------------
# Background poll loop
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
                active = info.get("active", False)
                last_lt = info.get("last_lt")
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
                        save_state(state)
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
                            print("send message error in poll_loop:", e)
                            lt = extract_lt(tx) or "N/A"
                            try:
                                await bot.send_message(chat_id, f"Новая транзакция LT={safe(str(lt))}")
                            except:
                                pass
                    if new_items:
                        last_seen = extract_lt(new_items[-1])
                        state["chat_monitors"][chat_id_str]["last_lt"] = last_seen
                        save_state(state)
                except Exception as e:
                    print("poll error for", address, e)
            await asyncio.sleep(POLL_INTERVAL)

# -------------------------
# Run
# -------------------------
async def main():
    # start poll loop
    asyncio.create_task(poll_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
