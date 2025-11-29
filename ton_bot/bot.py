import os
import asyncio
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

# ==========================
#   SECURITY FIX — HTML ESCAPE
# ==========================
def escape_html(text: str) -> str:
    if not isinstance(text, str):
        return text
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

# ==========================
#   CONFIG
# ==========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Храним включение/выключение мониторинга
monitoring_enabled = {}

# ==========================
#   TON API — баланс
# ==========================
async def get_balance(address):
    url = f"https://toncenter.com/api/v2/getAddressBalance?address={address}"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()

        balance = int(data.get("result", 0)) / 1e9
        return balance
    except Exception as e:
        print(f"Balance error: {e}")
        return None

# ==========================
#   TON API — токены (Jettons) - ИСПРАВЛЕННАЯ ВЕРСИЯ
# ==========================
async def get_tokens(address):
    url = f"https://toncenter.com/api/v3/jetton/balances?address={address}"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                print(f"Tokens raw response: {data}")  # Для отладки

        out = []
        balances = data.get("balances", [])
        
        if not balances:
            return ["Токены не найдены"]
            
        for t in balances:
            try:
                balance = int(t.get("balance", 0))
                jetton_info = t.get("jetton", {})
                
                # Получаем метаданные
                metadata = jetton_info.get("metadata", {})
                name = metadata.get("name", "Unknown")
                symbol = metadata.get("symbol", "???")
                decimals = int(jetton_info.get("decimals", 9))
                
                # Рассчитываем баланс с правильными decimal
                formatted_balance = balance / (10 ** decimals)
                
                out.append(f"{name} ({symbol}) — {formatted_balance:.6f}")
            except Exception as e:
                print(f"Token processing error: {e}")
                continue

        return out if out else ["Токены не найдены"]
    except Exception as e:
        print(f"Tokens API error: {e}")
        return [f"Ошибка получения токенов: {e}"]

# ==========================
#   TON API — транзакции (ПОЛНОСТЬЮ ПЕРЕПИСАННАЯ ВЕРСИЯ)
# ==========================
async def get_transactions(address, limit=10):
    url = f"https://toncenter.com/api/v2/getTransactions?address={address}&limit={limit}"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                print(f"Transactions raw response: {data}")  # Для отладки

        txs = data.get("result", [])
        parsed = []

        if not txs:
            return ["Транзакции не найдены"]

        for tx in txs:
            try:
                # Получаем базовую информацию
                tx_id = tx.get("transaction_id", {})
                lt = tx_id.get("lt", "N/A")
                hash_value = tx_id.get("hash", "N/A")[:8]  # Берем только первые 8 символов хеша
                ts = tx.get("utime", 0)
                
                if ts:
                    dt_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    dt_str = "N/A"

                # Обрабатываем входящее сообщение
                in_msg = tx.get("in_msg", {})
                out_msgs = tx.get("out_msgs", [])
                
                # Определяем тип транзакции и участников
                tx_type = "❓ Unknown"
                amount = 0
                other_party = "Unknown"
                
                # Если есть входящее сообщение - это получение средств
                if in_msg and in_msg.get("source"):
                    tx_type = "📥 IN"
                    other_party = escape_html(in_msg.get("source", "Unknown")[:10] + "...")
                    amount = int(in_msg.get("value", 0)) / 1e9
                
                # Если есть исходящие сообщения - это отправка средств
                elif out_msgs:
                    tx_type = "📤 OUT"
                    if out_msgs[0].get("destination"):
                        other_party = escape_html(out_msgs[0].get("destination", "Unknown")[:10] + "...")
                    amount = int(out_msgs[0].get("value", 0)) / 1e9
                
                # Форматируем запись о транзакции
                parsed.append(
                    f"{tx_type} | LT:{lt} | {dt_str}\n"
                    f"👤 {other_party}\n"
                    f"💰 {amount:.6f} TON\n"
                    f"🔗 {hash_value}..."
                )
                
            except Exception as e:
                print(f"Transaction processing error: {e}")
                continue

        return parsed if parsed else ["Не удалось обработать транзакции"]

    except Exception as e:
        print(f"Transactions API error: {e}")
        return [f"Ошибка получения истории: {e}"]

# ==========================
#   /start
# ==========================
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    monitoring_enabled[msg.from_user.id] = False

    await msg.answer(
        "👋 Бот активирован!\n\n"
        "Команды:\n"
        "/start — включить бота\n"
        "/stop — выключить бота\n"
        "/balance <адрес>\n"
        "/tokens <адрес>\n"
        "/history <адрес>\n"
        "/monitor_on <адрес>\n"
        "/monitor_off\n"
    )

# ==========================
#   /stop
# ==========================
@dp.message(Command("stop"))
async def cmd_stop(msg: Message):
    monitoring_enabled[msg.from_user.id] = False
    await msg.answer("🔴 Бот выключен.")

# ==========================
#   /balance
# ==========================
@dp.message(Command("balance"))
async def cmd_balance(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /balance <TON адрес>")

    address = args[1]
    balance = await get_balance(address)

    if balance is None:
        return await msg.answer("Ошибка получения баланса.")

    await msg.answer(f"💰 Баланс: {balance:.6f} TON")

# ==========================
#   /tokens
# ==========================
@dp.message(Command("tokens"))
async def cmd_tokens(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /tokens <TON адрес>")

    address = args[1]
    tokens = await get_tokens(address)

    response = "🪙 Токены:\n" + "\n".join(tokens)
    # Разбиваем длинные сообщения
    if len(response) > 4000:
        response = response[:4000] + "..."
    
    await msg.answer(response)

# ==========================
#   /history
# ==========================
@dp.message(Command("history"))
async def cmd_history(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /history <TON адрес>")

    address = args[1]
    txs = await get_transactions(address)

    response = "📜 Последние транзакции:\n\n" + "\n\n".join(txs)
    # Разбиваем длинные сообщения
    if len(response) > 4000:
        response = response[:4000] + "..."
    
    await msg.answer(response)

# ==========================
#   Мониторинг (вкл)
# ==========================
@dp.message(Command("monitor_on"))
async def cmd_monitor_on(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Использование: /monitor_on <TON адрес>")

    user = msg.from_user.id
    address = args[1]

    monitoring_enabled[user] = True
    await msg.answer(f"🟢 Мониторинг включен для:\n{address}")

    asyncio.create_task(monitor_loop(msg, address))

# ==========================
#   Мониторинг (выкл)
# ==========================
@dp.message(Command("monitor_off"))
async def cmd_monitor_off(msg: Message):
    monitoring_enabled[msg.from_user.id] = False
    await msg.answer("🔴 Мониторинг отключен.")

# ==========================
#   Основной цикл мониторинга
# ==========================
async def monitor_loop(msg: Message, address: str):
    user = msg.from_user.id
    last_lt = None

    while monitoring_enabled.get(user, False):
        try:
            txs = await get_transactions(address, limit=1)

            if txs and "LT:" in txs[0]:
                # Извлекаем LT из нового формата
                for line in txs[0].split('\n'):
                    if "LT:" in line:
                        lt_new = line.split("LT:")[1].split(" | ")[0].strip()
                        break
                else:
                    lt_new = None
                
                if lt_new and lt_new != last_lt:
                    if last_lt is not None:  # Не отправляем уведомление при первом запуске
                        await msg.answer("🆕 Новая транзакция:\n" + txs[0])
                    last_lt = lt_new

            await asyncio.sleep(10)  # Увеличил интервал до 10 секунд
        except Exception as e:
            print(f"Monitor loop error: {e}")
            await asyncio.sleep(10)

# ==========================
#   RUN
# ==========================
if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
