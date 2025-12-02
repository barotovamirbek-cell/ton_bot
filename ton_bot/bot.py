import json
import asyncio
import requests
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

import config

bot = Bot(
    token=config.BOT_TOKEN,
    timeout=30,     # увеличенный таймаут
)

dp = Dispatcher()

DB_FILE = "db.json"


# -------------------- DB --------------------
def load_db():
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)


# -------------------- Команды --------------------
@dp.message(Command("start"))
async def start(msg: Message):
    await msg.answer(
        "👋 Бот TON уведомлений.\n\n"
        "/setwallet <адрес> — установить кошелёк\n"
        "/mywallet — текущий кошелёк\n"
        "/history — последние транзакции\n"
    )


@dp.message(Command("setwallet"))
async def setwallet(msg: Message):
    parts = msg.text.split()
    if len(parts) < 2:
        return await msg.answer("❗ Укажи кошелёк: /setwallet EQxxxx")

    wallet = parts[1].strip()
    user_id = str(msg.from_user.id)

    db = load_db()
    db[user_id] = {"wallet": wallet, "last_tx": ""}
    save_db(db)

    await msg.answer(f"✔ Кошелёк установлен:\n`{wallet}`", parse_mode="Markdown")


@dp.message(Command("mywallet"))
async def mywallet(msg: Message):
    user_id = str(msg.from_user.id)
    db = load_db()

    if user_id not in db:
        return await msg.answer("❗ Кошелёк не установлен")

    await msg.answer(f"Твой кошелёк:\n`{db[user_id]['wallet']}`", parse_mode="Markdown")


# -------------------- /history --------------------
@dp.message(Command("history"))
async def history(msg: Message):
    user_id = str(msg.from_user.id)
    db = load_db()

    if user_id not in db:
        return await msg.answer("❗ Сначала установи кошелёк: /setwallet")

    wallet = db[user_id]["wallet"]

    try:
        params = {
            "address": wallet,
            "limit": 10,
            "api_key": config.TONCENTER_KEY
        }
        r = requests.get(config.TONCENTER_API, params=params).json()

        if "result" not in r or len(r["result"]) == 0:
            return await msg.answer("📭 История пуста")

        text = f"📜 *Последние 10 транзакций*\n`{wallet}`\n\n"

        for tx in r["result"]:
            tx_hash = tx["transaction_id"]["hash"]

            in_msg = tx.get("in_msg", {})
            out_msgs = tx.get("out_msgs", [])

            # входящая транзакция?
            if in_msg and in_msg.get("value") and int(in_msg["value"]) > 0:
                value = int(in_msg["value"]) / 1e9
                src = in_msg.get("source", "unknown")
                tx_type = "IN"
                text += f"🟢 *IN*  +{value} TON\n↪ from `{src}`\n🆔 `{tx_hash}`\n\n"

            # исходящие?
            for out in out_msgs:
                if out.get("value") and int(out["value"]) > 0:
                    value = int(out["value"]) / 1e9
                    dst = out.get("destination", "unknown")
                    tx_type = "OUT"
                    text += f"🔴 *OUT*  -{value} TON\n↪ to `{dst}`\n🆔 `{tx_hash}`\n\n"

        await msg.answer(text, parse_mode="Markdown")

    except Exception as e:
        await msg.answer("❗ Ошибка при загрузке истории")
        print("HISTORY ERROR:", e)


# -------------------- Мониторинг TON --------------------
async def check_transactions():
    print("TON мониторинг запущен...")

    while True:
        db = load_db()

        for user_id, data in db.items():
            wallet = data["wallet"]
            last_tx = data.get("last_tx", "")

            try:
                params = {
                    "address": wallet,
                    "limit": 1,
                    "api_key": config.TONCENTER_KEY
                }
                r = requests.get(config.TONCENTER_API, params=params).json()

                if "result" not in r or len(r["result"]) == 0:
                    continue

                tx = r["result"][0]
                tx_hash = tx["transaction_id"]["hash"]

                # Новая транзакция?
                if tx_hash != last_tx:

                    in_msg = tx.get("in_msg", {})
                    out_msgs = tx.get("out_msgs", [])

                    msg_text = f"💎 *Новая транзакция TON!*\n\n"

                    # входящая?
                    if in_msg and in_msg.get("value"):
                        value = int(in_msg["value"]) / 1e9
                        src = in_msg.get("source", "unknown")
                        msg_text += (
                            f"🟢 *Тип:* IN (входящая)\n"
                            f"👤 От: `{src}`\n"
                            f"💰 Сумма: +{value} TON\n\n"
                        )

                    # исходящие?
                    for out in out_msgs:
                        if out.get("value"):
                            value = int(out["value"]) / 1e9
                            dst = out.get("destination", "unknown")
                            msg_text += (
                                f"🔴 *Тип:* OUT (исходящая)\n"
                                f"➡ Кому: `{dst}`\n"
                                f"💸 Сумма: -{value} TON\n\n"
                            )

                    msg_text += (
                        f"📬 Кошелёк: `{wallet}`\n"
                        f"🆔 `{tx_hash}`"
                    )

                    await bot.send_message(user_id, msg_text, parse_mode="Markdown")

                    db[user_id]["last_tx"] = tx_hash
                    save_db(db)

            except Exception as e:
                print("MONITORING ERROR:", e)

        await asyncio.sleep(10)


# -------------------- Старт бота --------------------
async def main():
    asyncio.create_task(check_transactions())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
