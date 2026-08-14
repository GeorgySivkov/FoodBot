#!/usr/bin/env python3
"""report.py — evening summary to Telegram. Run from cron ~22:30.

Runs a fresh Garmin sync first, then sends: eaten vs burned, deficit,
protein, weight trend, projection to goal.
"""
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

import advice
import db

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
TARGET_KCAL = int(os.environ.get("TARGET_KCAL", "1650"))
PROTEIN_TARGET = int(os.environ.get("PROTEIN_TARGET_G", "160"))
GOAL_WEIGHT = float(os.environ.get("GOAL_WEIGHT", "77"))
MONTHLY_CREDIT = float(os.environ.get("MONTHLY_CREDIT_USD", "100"))


def tg_send(text):
    if not (BOT_TOKEN and CHAT_ID):
        print(text)
        return
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    urllib.request.urlopen(
        urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=data),
        timeout=30)


def main():
    # fresh Garmin numbers first (ignore failures — report still goes out)
    try:
        subprocess.run([sys.executable, str(HERE / "garmin_sync.py")],
                       capture_output=True, timeout=300)
    except Exception:
        pass

    d = db.today()
    kcal, p, f, c, n = db.day_totals(d)
    g = db.garmin_for(d)

    lines = [f"🌙 Итог дня {d.strftime('%d.%m')}"]
    lines.append(f"Съедено: {kcal} ккал (Б{p} Ж{f} У{c}), приёмов: {n}")
    over = kcal - TARGET_KCAL
    lines.append(("✅ В бюджете " if over <= 0 else "🔴 Перебор ") +
                 f"({TARGET_KCAL} ккал план, {abs(over)} ккал {'запас' if over <= 0 else 'сверху'})")
    if p < PROTEIN_TARGET:
        lines.append(f"🥩 Белок {p}/{PROTEIN_TARGET} г — завтра добирай")

    if g and g[0]:
        total_kcal, active_kcal, steps, rhr, sleep_s, acts = g
        deficit = total_kcal - kcal
        lines.append(f"⌚ Сожжено {total_kcal} ккал → дефицит {deficit} ккал"
                     f" (≈{round(deficit * 7 / 7700, 2)} кг/нед таким темпом)")
        extras = []
        if steps:
            extras.append(f"{steps} шагов")
        if sleep_s:
            extras.append(f"сон {round(sleep_s / 3600, 1)} ч")
        if rhr:
            extras.append(f"пульс покоя {rhr}")
        if extras:
            lines.append(" · ".join(extras))
        import json as _json
        try:
            acts = _json.loads(acts) if isinstance(acts, str) else (acts or [])
        except Exception:
            acts = []
        for a in acts:
            lines.append(f"🏃 {a.get('name')}: {a.get('duration_min')} мин, {a.get('kcal')} ккал")
    else:
        lines.append("⌚ Garmin за сегодня не синкнулся")

    ws = db.recent_weights(30)
    if ws:
        cur = float(ws[-1][1])
        lines.append(f"⚖️ Вес: {cur} кг (до цели {round(cur - GOAL_WEIGHT, 1)} кг)")

    ctx = {
        "mode": "evening",
        "kcal": kcal, "protein_g": p, "fat_g": f, "carbs_g": c, "meals": n,
        "burned": g[0] if g else None,
        "deficit": (g[0] - kcal) if (g and g[0]) else None,
        "steps": g[2] if g else None,
        "sleep_h": round(g[4] / 3600, 1) if (g and g[4]) else None,
        "activities": g[5] if g else None,
        "weights_last_14d": [[str(d), float(k)] for d, k in db.recent_weights(14)],
        "target_kcal": TARGET_KCAL, "protein_target": PROTEIN_TARGET,
        "goal_weight": GOAL_WEIGHT,
    }
    tips = advice.get(ctx, "evening")
    if tips:
        lines.append("")
        lines.append("💡 Рекомендации:")
        lines.extend(tips)

    tomorrow = d + timedelta(days=1)
    menu_ctx = {
        "tomorrow_weekday": tomorrow.weekday(),
        "tomorrow_is_free_day": tomorrow.weekday() == 5,  # Saturday
        "recent_dishes": db.recent_dishes(3),
        "protein_g": p, "protein_target": PROTEIN_TARGET,
        "target_kcal": TARGET_KCAL,
    }
    menu = advice.get_menu(menu_ctx)
    if menu:
        lines.append("")
        lines.append(f"🍽 Меню на завтра ({'суббота — свободный день, ~2100' if menu_ctx['tomorrow_is_free_day'] else '~' + str(TARGET_KCAL)} ккал):")
        lines.extend(menu)

    # shared Claude budget: JobBot + FoodBot write to the same public.usage_log
    try:
        total, ncalls, food = db.month_spend()
        lines.append("")
        lines.append(f"⚙️ Claude за месяц: ${total:.2f} из ${MONTHLY_CREDIT:.0f}"
                     f" ({total / MONTHLY_CREDIT * 100:.0f}%) · FoodBot ${food:.2f}")
    except Exception:
        pass

    tg_send("\n".join(lines))
    print("report sent.")


if __name__ == "__main__":
    main()
