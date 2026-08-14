#!/usr/bin/env python3
"""foodbot — Telegram front-end for calorie tracking.

Send a food photo (caption optional) or a text description — the bot estimates
kcal + macros via Claude, logs to Postgres and replies with the day's running
total against your target. Locked to one Telegram user.

Commands:
  /day             today's log + totals + deficit
  /week            last 7 days table
  /weight 86.4     log a morning weigh-in
  /undo            delete the last meal entry
  /goal            progress toward goal weight
  /start           help
"""
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters,
)

import db
import vision

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED = os.environ.get("TELEGRAM_ALLOWED_USER_ID")

TARGET_KCAL = int(os.environ.get("TARGET_KCAL", "1650"))
PROTEIN_TARGET = int(os.environ.get("PROTEIN_TARGET_G", "160"))
GOAL_WEIGHT = float(os.environ.get("GOAL_WEIGHT", "77"))
GOAL_DATE = os.environ.get("GOAL_DATE", "2026-10-14")

CONF_MARK = {"high": "", "medium": " ~", "low": " ⚠️ грубая оценка"}


def _allowed(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if not ALLOWED:
        return False
    return str(uid) == str(ALLOWED)


def _bootstrap_hint(uid):
    return (f"Бот ещё не привязан к тебе. Твой Telegram ID: {uid}.\n"
            f"Добавь TELEGRAM_ALLOWED_USER_ID={uid} в .env и перезапусти бота.")


def _totals_line(d) -> str:
    kcal, p, f, c, n = db.day_totals(d)
    left = TARGET_KCAL - kcal
    bar = "🟢" if left >= 0 else "🔴"
    line = (f"Итого за день: {kcal} ккал (Б{p} Ж{f} У{c}), приёмов: {n}\n"
            f"{bar} Бюджет {TARGET_KCAL}: {'осталось ' + str(left) if left >= 0 else 'перебор ' + str(-left)} ккал")
    if p < PROTEIN_TARGET:
        line += f"\n🥩 Белка {p}/{PROTEIN_TARGET} г — добери {PROTEIN_TARGET - p} г"
    g = db.garmin_for(d)
    if g and g[0]:
        burned = g[0]
        line += f"\n⌚ Garmin: сожжено {burned} ккал → дефицит {burned - kcal} ккал"
    return line


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not ALLOWED:
        await update.message.reply_text("foodbot запущен.\n" + _bootstrap_hint(uid))
        return
    if not _allowed(update):
        return
    await update.message.reply_text(
        "Кидай фото еды (можно с подписью — граммы/состав) или просто текстом "
        "«гречка 150г + куриная грудка».\n\n"
        "/day — сводка за сегодня\n/week — неделя\n/weight 86.4 — записать вес\n"
        "/undo — удалить последнюю запись\n/goal — прогресс к цели")


async def handle_meal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not ALLOWED:
        await update.message.reply_text(_bootstrap_hint(update.effective_user.id))
        return
    if not _allowed(update):
        return

    caption = (update.message.caption or update.message.text or "").strip()
    photo_bytes, file_id = None, None
    if update.message.photo:
        biggest = update.message.photo[-1]
        file_id = biggest.file_id
        tg_file = await biggest.get_file()
        photo_bytes = bytes(await tg_file.download_as_bytearray())
    elif len(caption) < 3:
        await update.message.reply_text("Кинь фото еды или опиши текстом, что съел.")
        return

    note = await update.message.reply_text("⏳ Считаю…")
    try:
        est = vision.analyze(photo_bytes, caption or None)
    except Exception as e:
        await note.edit_text(f"Не смог посчитать: {e}")
        return

    db.add_meal(
        dish=est["dish"], kcal=est["kcal"], protein_g=est["protein_g"],
        fat_g=est["fat_g"], carbs_g=est["carbs_g"], confidence=est["confidence"],
        source="photo" if photo_bytes else "text", photo_file_id=file_id,
        raw_json=json.dumps(est, ensure_ascii=False))

    items = "\n".join(f"  · {i.get('name')} ~{i.get('grams', '?')}г — {i.get('kcal', '?')} ккал"
                      for i in est.get("items", [])[:8])
    mark = CONF_MARK.get(est["confidence"], "")
    text = (f"🍽 {est['dish']}{mark}\n"
            f"≈ {est['kcal']} ккал · Б{est['protein_g']} Ж{est['fat_g']} У{est['carbs_g']}\n")
    if items:
        text += items + "\n"
    if est.get("notes"):
        text += f"({est['notes']})\n"
    text += "\n" + _totals_line(db.today())
    text += "\n\nОшибся — /undo и пришли с уточнением в подписи."
    await note.edit_text(text)


async def day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    d = db.today()
    meals = db.day_meals(d)
    if not meals:
        await update.message.reply_text("Сегодня ещё ничего не записано.")
        return
    lines = [f"{t}  {dish} — {kcal} ккал (Б{p})" for t, dish, kcal, p in meals]
    await update.message.reply_text("\n".join(lines) + "\n\n" + _totals_line(d))


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    rows = db.week_rows(db.today())
    if not rows:
        await update.message.reply_text("Пока нет данных за неделю.")
        return
    lines, defs = [], []
    for day_, kcal, p, burned, steps in rows:
        d_str = day_.strftime("%d.%m")
        if burned:
            defs.append(burned - kcal)
            lines.append(f"{d_str}: ел {kcal}, сжёг {burned} → дефицит {burned - kcal}")
        else:
            lines.append(f"{d_str}: ел {kcal} (Б{p}), Garmin нет")
    text = "\n".join(lines)
    if defs:
        avg = sum(defs) // len(defs)
        text += (f"\n\nСредний дефицит: {avg} ккал/день"
                 f" ≈ {round(avg * 7 / 7700, 2)} кг/нед")
    await update.message.reply_text(text)


async def weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    args = context.args or []
    try:
        kg = float(args[0].replace(",", "."))
        assert 40 < kg < 200
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Формат: /weight 86.4")
        return
    db.set_weight(db.today(), kg)
    await update.message.reply_text(f"Записал: {kg} кг ✅\n" + _goal_text())


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    row = db.undo_last_meal()
    if not row:
        await update.message.reply_text("Нечего удалять.")
        return
    await update.message.reply_text(f"Удалил: {row[0]} ({row[1]} ккал)\n\n" + _totals_line(db.today()))


def _goal_text() -> str:
    ws = db.recent_weights(30)
    if not ws:
        return f"Цель: {GOAL_WEIGHT} кг к {GOAL_DATE}. Запиши первый вес: /weight 87.0"
    cur = float(ws[-1][1])
    text = f"Сейчас {cur} кг → цель {GOAL_WEIGHT} кг (осталось {round(cur - GOAL_WEIGHT, 1)} кг)"
    if len(ws) >= 4:
        first_d, first_kg = ws[0][0], float(ws[0][1])
        days = (ws[-1][0] - first_d).days or 1
        rate = (first_kg - cur) / days * 7  # kg per week
        if rate > 0.05:
            weeks_left = (cur - GOAL_WEIGHT) / rate
            eta = ws[-1][0] + timedelta(weeks=weeks_left)
            text += f"\nТемп: {round(rate, 2)} кг/нед → выйдешь на цель ~{eta.strftime('%d.%m.%Y')}"
        else:
            text += "\nТемп пока около нуля — держи дефицит, вес догонит."
    return text


async def goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text(_goal_text())


def main():
    if not TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("weight", weight))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("goal", goal))
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.TEXT) & ~filters.COMMAND, handle_meal))
    print("foodbot running (polling)…  Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
