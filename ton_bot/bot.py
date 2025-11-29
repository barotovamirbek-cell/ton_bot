import os
import asyncio
import signal
import sys
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

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

if not TELEGRAM_TOKEN:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN not set!")
    sys.exit(1)

# Явно настраиваем бота для polling
bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Храним включение/выключение мониторинга
monitoring_enabled = {}
monitoring_tasks = {}

# ==========================
#   GRACEFUL SHUTDOWN
# ==========================
async def shutdown():
    """Корректное завершение работы бота"""
    print("🛑 Shutting down bot...")
    
    # Останавливаем все задачи мониторинга
    for user_id, task in monitoring_tasks.items():
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    # Закрываем сессию бота
    await bot.session.close()
    print("✅ Bot shutdown complete")

# ==========================
#   TON API — баланс
# ==========================
async def get_balance(address):
    url = f"https://toncenter.com/api/v2/getAddressBalance?address={address}"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    balance = int(data.get("result", 0)) / 1e9
                    return balance
                else:
                    print(f"Balance API error: HTTP {resp.status}")
                    return None
    except asyncio.TimeoutError:
        print("❌ Balance request timeout")
        return None
    except Exception as e:
        print(f"❌ Balance error: {e}")
        return None

# ==========================
#   TON API — токены (Jettons)
# ==========================
async def get_tokens(address):
    url = f"https://toncenter.com/api/v2/jetton/getBalances?address={address}"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"🔍 Tokens raw response: {data}")

                    out = []
                    balances = data.get("result", {}).get("balances", [])
                    
                    if not balances:
                        return ["🚫 Токены не найдены"]
                        
                    for t in balances:
                        try:
                            balance = int(t.get("balance", 0))
                            jetton_info = t.get("jetton_info", {})
                            
                            name = jetton_info.get("name", "Unknown")
                            symbol = jetton_info.get("symbol", "???")
                            decimals = int(jetton_info.get("decimals", 9))
                            
                            formatted_balance = balance / (10 ** decimals)
                            
                            out.append(f"• {name} ({symbol}) — {formatted_balance:.6f}")
                        except Exception as e:
                            print(f"⚠️ Token processing error: {e}")
                            continue

                    return out if out else ["🚫 Нет доступных токенов"]
                else:
                    return [f"❌ API Error: HTTP {resp.status}"]
                    
    except asyncio.TimeoutError:
        return ["⏰ Таймаут запроса токенов"]
    except Exception as e:
        print(f"❌ Tokens API error: {e}")
        return [f"❌ Ошибка получения токенов: {str(e)[:100]}"]

# ==========================
#   TON API — транзакции
# ==========================
async def get_transactions(address, limit=10):
    url = f"https://toncenter.com/api/v2/getTransactions?address={address}&limit={limit}"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"🔍 Transactions response count: {len(data.get('result', []))}")

                    txs = data.get("result", [])
                    parsed = []

                    if not txs:
                        return ["📭 Транзакции не найдены"]

                    for tx in txs:
                        try:
                            tx_id = tx.get("transaction_id", {})
                            lt = tx_id.get("lt", "N/A")
                            hash_value = tx_id.get("hash", "N/A")[:8]
                            ts = tx.get("utime", 0)
                            
                            if ts:
                                dt_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                            else:
                                dt_str = "N/A"

                            in_msg = tx.get("in_msg", {})
                            out_msgs = tx.get("out_msgs", [])
                            
                            tx_type = "❓ Unknown"
                            amount = 0
                            other_party = "Unknown"
                            
                            if in_msg and in_msg.get("source"):
                                tx_type = "📥 IN"
                                other_party = escape_html(in_msg.get("source", "Unknown")[:10] + "...")
                                amount = int(in_msg.get("value", 0)) / 1e9
                            
                            elif out_msgs:
                                tx_type = "📤 OUT"
                                if out_msgs[0].get("destination"):
                                    other_party = escape_html(out_msgs[0].get("destination", "Unknown")[:10] + "...")
                                amount = int(out_msgs[0].get("value", 0)) / 1e9
                            
                            parsed.append(
                                f"{tx_type} | LT:{lt} | {dt_str}\n"
                                f"👤 {other_party}\n"
                                f"💰 {amount:.6f} TON\n"
                                f"🔗 {hash_value}..."
                            )
                            
                        except Exception as e:
                            print(f"⚠️ Transaction processing error: {e}")
                            continue

                    return parsed if parsed else ["❌ Не удалось обработать транзакции"]
                else:
                    return [f"❌ API Error: HTTP {resp.status}"]
                    
    except asyncio.TimeoutError:
        return ["⏰ Таймаут запроса транзакций"]
    except Exception as e:
        print(f"❌ Transactions API error: {e}")
        return [f"❌ Ошибка получения истории: {str(e)[:100]}"]

# ==========================
#   КОМАНДЫ БОТА
# ==========================
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    monitoring_enabled[msg.from_user.id] = False
    await msg.answer(
        "👋 Бот активирован!\n\n"
        "📋 Команды:\n"
        "/start — показать это сообщение\n"
        "/stop — выключить мониторинг\n"
        "/balance <адрес> — баланс TON\n"
        "/tokens <адрес> — список токенов\n"
        "/history <адрес> — история транзакций\n"
        "/monitor_on <адрес> — включить мониторинг\n"
        "/monitor_off — выключить мониторинг\n\n"
        "💡 Пример: /balance EQABCD123..."
    )

@dp.message(Command("stop"))
async def cmd_stop(msg: Message):
    user_id = msg.from_user.id
    monitoring_enabled[user_id] = False
    
    if user_id in monitoring_tasks:
        monitoring_tasks[user_id].cancel()
        del monitoring_tasks[user_id]
    
    await msg.answer("🔴 Мониторинг отключен.")

@dp.message(Command("balance"))
async def cmd_balance(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("❌ Использование: /balance <TON адрес>\n\nПример: /balance EQABCD123...")

    address = args[1]
    
    balance = await get_balance(address)

    if balance is None:
        return await msg.answer("❌ Ошибка получения баланса. Проверьте адрес.")
    
    await msg.answer(f"💰 Баланс: {balance:.6f} TON")

@dp.message(Command("tokens"))
async def cmd_tokens(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("❌ Использование: /tokens <TON адрес>")

    address = args[1]
    
    tokens = await get_tokens(address)

    response = "🪙 Токены:\n" + "\n".join(tokens)
    if len(response) > 4000:
        response = response[:4000] + "..."
    
    await msg.answer(response)

@dp.message(Command("history"))
async def cmd_history(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("❌ Использование: /history <TON адрес>")

    address = args[1]
    
    txs = await get_transactions(address)

    response = "📜 Последние транзакции:\n\n" + "\n\n".join(txs)
    if len(response) > 4000:
        response = response[:4000] + "..."
    
    await msg.answer(response)

@dp.message(Command("monitor_on"))
async def cmd_monitor_on(msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("❌ Использование: /monitor_on <TON адрес>")

    user_id = msg.from_user.id
    address = args[1]

    # Останавливаем предыдущий мониторинг если был
    if user_id in monitoring_tasks:
        monitoring_tasks[user_id].cancel()
    
    monitoring_enabled[user_id] = True
    task = asyncio.create_task(monitor_loop(msg, address))
    monitoring_tasks[user_id] = task
    
    await msg.answer(f"🟢 Мониторинг включен для:\n`{address}`\n\n⏰ Проверка каждые 10 секунд")

@dp.message(Command("monitor_off"))
async def cmd_monitor_off(msg: Message):
    user_id = msg.from_user.id
    monitoring_enabled[user_id] = False
    
    if user_id in monitoring_tasks:
        monitoring_tasks[user_id].cancel()
        del monitoring_tasks[user_id]
    
    await msg.answer("🔴 Мониторинг отключен.")

# ==========================
#   МОНИТОРИНГ
# ==========================
async def monitor_loop(msg: Message, address: str):
    user_id = msg.from_user.id
    last_lt = None
    error_count = 0

    while monitoring_enabled.get(user_id, False) and error_count < 5:
        try:
            txs = await get_transactions(address, limit=1)

            if txs and "LT:" in txs[0]:
                for line in txs[0].split('\n'):
                    if "LT:" in line:
                        lt_new = line.split("LT:")[1].split(" | ")[0].strip()
                        break
                else:
                    lt_new = None
                
                if lt_new and lt_new != last_lt:
                    if last_lt is not None:
                        await msg.answer("🆕 Новая транзакция:\n" + txs[0])
                    last_lt = lt_new
                    error_count = 0
            else:
                error_count += 1

            await asyncio.sleep(10)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"❌ Monitor loop error: {e}")
            error_count += 1
            await asyncio.sleep(10)

    if error_count >= 5:
        await msg.answer("🔴 Мониторинг остановлен из-за множественных ошибок")

# ==========================
#   ЗАПУСК БОТА
# ==========================
async def main():
    print("🤖 Starting Telegram Bot...")
    print(f"🔑 Bot token: {'✅ Set' if TELEGRAM_TOKEN else '❌ Missing'}")
    print(f"🔑 TON API key: {'✅ Set' if TONCENTER_API_KEY else '⚠️  Missing (rate limits)'}")
    
    try:
        # Явно используем polling и отключаем вебхуки
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        print(f"❌ Bot error: {e}")
    finally:
        await shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Bot stopped by user")
    except Exception as e:
        print(f"💥 Fatal error: {e}")
