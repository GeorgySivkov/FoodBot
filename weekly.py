#!/usr/bin/env python3
"""weekly.py — Sunday briefing: week recap + grocery list for the coming week.

Cron (Sunday 10:00 Madrid = 08:00 UTC in summer):
    0 8 * * 0 cd /root/foodbot && .venv/bin/python weekly.py >> report.log 2>&1
"""
import os
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

import db
import engine

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
TARGET_KCAL = int(os.environ.get("TARGET_KCAL", "1650"))
PROTEIN_TARGET = int(os.environ.get("PROTEIN_TARGET_G", "160"))
GOAL_WEIGHT = float(os.environ.get("GOAL_WEIGHT", "77"))
GROCERY_NOTES = os.environ.get(
    "GROCERY_NOTES",
    "Закупка на двоих: George + жена. Жена ест то же самое (рыба/морепродукты/яйца ок), "
    "но не ест мясо — вместо мяса для неё индейка (филе/бедро). Её порции обычные, "
    "без дефицитных рамок George.")

GROCERY_PROMPT = """Ты — личный ассистент George по питанию. Составь список покупок на неделю.

{notes}

Рамки George: {target} ккал/день (суббота свободная ~2100), белок {protein}+ г/день.
Скелет дня: белковый завтрак ~450 / обед ~550 (рыба или морепродукты + немного углеводов) /
перекус ~200 / ужин ~450 (белок + овощи). Готовка в аэрогриле, масло только спреем.
Магазины: рынок Бадалоны и Mercadona. Основа: дорада, лубина, гамбас, сепия, мидии,
бакалао, мерлуза, тунец в банках, яйца, queso fresco batido 0%, сезонные овощи и фрукты,
немного риса/картофеля.

На прошлой неделе George ел: {recent}. Сделай ротацию — не дублируй прошлую неделю дословно.

Ответь СТРОГО в формате (без вступлений, количества на 7 дней на всех, кто указан выше):
🐟 Рыба/морепродукты:
- ...
🥚 Белок прочий:
- ...
🥦 Овощи/фрукты:
- ...
🌾 Прочее:
- ...
💶 Примерный бюджет: X-Y €"""

# fallback if the model is unavailable — the ration.md base list, scaled for two
FALLBACK = """🐟 Рыба/морепродукты (на двоих):
- Дорада или лубина — 5-6 шт
- Сепия/кальмары — 800 г
- Мидии — 1.5 кг
- Гамбас — 600 г
- Бакалао или мерлуза — 800 г
- Тунец в банках — 6 шт
🥚 Белок прочий:
- Филе/бедро индейки — 700 г (для жены, вместо мяса)
- Яйца — 20-24 шт
- Queso fresco batido 0% — 2-3 кг
🥦 Овощи/фрукты:
- Перцы, цукини, брокколи, помидоры, грибы — без ограничений
- Фрукты сезонные (персики, дыня, арбуз) — 2-3 кг
🌾 Прочее:
- Рис — 1 кг, картофель — 1.5 кг
- Оливковое масло-спрей, лимоны, чеснок, специи
💶 Примерный бюджет: 55-70 €"""


def tg_send(text):
    if not (BOT_TOKEN and CHAT_ID):
        print(text)
        return
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    urllib.request.urlopen(
        urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data),
        timeout=30)


def week_recap(today) -> list[str]:
    lines = []
    rows = db.week_rows(today - timedelta(days=1))  # the finished week, up to yesterday
    if rows:
        eaten = [r[1] for r in rows if r[1]]
        defs = [r[3] - r[1] for r in rows if r[1] and r[3]]
        lines.append(f"Неделя: залогировано {len(rows)} дн., в среднем {sum(eaten)//max(len(eaten),1)} ккал/день")
        if defs:
            avg = sum(defs) // len(defs)
            lines.append(f"Средний дефицит: {avg} ккал ≈ {round(avg*7/7700, 2)} кг/нед")
    ws = db.recent_weights(8)
    if len(ws) >= 2:
        delta = float(ws[-1][1]) - float(ws[0][1])
        arrow = "▼" if delta < 0 else "▲"
        lines.append(f"Вес за неделю: {arrow} {abs(round(delta,1))} кг → {float(ws[-1][1])} кг "
                     f"(до цели {round(float(ws[-1][1]) - GOAL_WEIGHT, 1)})")
    return lines


def grocery_list() -> str:
    if os.environ.get("ADVICE_LLM", "").lower() != "off":
        try:
            recent = ", ".join(db.recent_dishes(7)[:20]) or "нет данных"
            text = engine.run(
                GROCERY_PROMPT.format(notes=GROCERY_NOTES, target=TARGET_KCAL,
                                      protein=PROTEIN_TARGET, recent=recent),
                "food_grocery")
            if "🐟" in text or text.strip().startswith("-"):
                return text.strip()
        except Exception:
            pass
    return FALLBACK


def main():
    today = db.today()
    lines = ["🛒 Воскресенье — закупка на неделю"]
    recap = week_recap(today)
    if recap:
        lines.append("")
        lines.extend(recap)
    lines.append("")
    lines.append(grocery_list())
    tg_send("\n".join(lines))
    print("weekly report sent.")


if __name__ == "__main__":
    main()