import os, asyncio, logging, requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)

API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise ValueError("Переменная окружения API_TOKEN не задана")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

wallet_address = None
notifications_enabled = True
last_transactions = set()
users = set()

def main_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Баланс", callback_data="balance"),
        InlineKeyboardButton("История", callback_data="history")
    )
    kb.add(
        InlineKeyboardButton("Вкл/Выкл уведомления", callback_data="toggle_notifications")
    )
    return kb

def get_balance(address):
    url = f"https://toncenter.com/api/v2/getAddressInformation?address={address}&api_key=YOUR_TONCENTER_API_KEY"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            result = r["result"]
            balance = int(result.get("balance",0))/1e9
            tokens = []
            for t in result.get("tokens",[]):
                symbol = t.get("name") or t.get("symbol") or "TOKEN"
                amount = int(t.get("balance",0)) / (10**int(t.get("decimals",9)))
                tokens.append(f"{symbol}: {amount}")
            return balance, tokens
    except:
        pass
    return 0, []

def get_transactions(address):
    url = f"https://toncenter.com/api/v2/getTransactions?address={address}&limit=10&api_key=YOUR_TONCENTER_API_KEY"
    try:
        r = requests.get(url).json()
        if r.get("ok"):
            return r["result"]["transactions"]
    except:
        pass
    return []

def get_tokens_from_tx(tx):
    tokens_text = ""
    in_msg = tx.get("in_msg", {})
    value = int(in_msg.get("value",0))/1e9
    tokens_text += f"TON: {value}\n"
    for token in tx.get("token_balances", []):
        symbol = token.get("symbol") or token.get("name") or "TOKEN"
        amount = int(token.get("balance",0)) / (10**int(token.get("decimals",9)))
        tokens_text += f"{symbol}: {amount}\n"
    return tokens_text.strip()

async def check_new_transactions():
    global last_transactions
    while True:
        if wallet_address:
            txs = get_transactions(wallet_address)
            new_txs = [tx for tx in txs if tx["hash"] not in last_transactions]
            for tx in new_txs:
                if notifications_enabled:
                    sender = tx.get("in_msg", {}).get("source","Unknown")
                    tokens_info = get_tokens_from_tx(tx)
                    text = f"📥 Новая транзакция\nОт: {sender}\n{tokens_info}"
                    for uid in users:
                        try: await bot.send_message(uid, text)
                        except: pass
                last_transactions.add(tx["hash"])
        await asyncio.sleep(10)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    users.add(message.chat.id)
    await message.answer(
        "Бот активирован. Ты будешь получать уведомления о транзакциях TON.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("setwallet"))
async def cmd_setwallet(message: types.Message):
    global wallet_address
    args = message.get_args()
    if not args:
        await message.answer("Используй команду: /setwallet <адрес_кошелька>")
        return
    wallet_address = args.strip()
    await message.answer(f"Адрес кошелька установлен: {wallet_address}")

@dp.callback_query()
async def callbacks(call: types.CallbackQuery):
    global notifications_enabled
    if not wallet_address:
        await call.message.answer("Сначала установите адрес кошелька командой /setwallet")
        return
    if call.data=="balance":
        bal, tokens = get_balance(wallet_address)
        text = f"Баланс: {bal} TON\n"
        if tokens: text += "\n" + "\n".join(tokens)
        await call.message.answer(text)
    elif call.data=="history":
        txs = get_transactions(wallet_address)
        text = "Последние транзакции:\n"
        for tx in txs[:5]:
            sender = tx.get("in_msg", {}).get("source","Unknown")
            tokens_info = get_tokens_from_tx(tx)
            text += f"От: {sender}\n{tokens_info}\n\n"
        await call.message.answer(text.strip())
    elif call.data=="toggle_notifications":
        notifications_enabled = not notifications_enabled
        state = "включены" if notifications_enabled else "выключены"
        await call.message.answer(f"Уведомления {state}.")

async def main():
    asyncio.create_task(check_new_transactions())
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
