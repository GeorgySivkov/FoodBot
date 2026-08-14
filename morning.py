#!/usr/bin/env python3
"""morning.py — morning briefing to Telegram. Run from cron ~08:00.

Fresh Garmin sync (yesterday's final numbers + last night's sleep), then:
sleep quality, yesterday recap (deficit, protein), today's plan, weight
reminder, recommendations.
"""
import json
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
    try:
        subprocess.run([sys.executable, str(HERE / "garmin_sync.py")],
                       capture_output=True, timeout=300)
    except Exception:
        pass

    today = db.today()
    yday = today - timedelta(days=1)
    g_today = db.garmin_for(today)      # has last night's sleep + morning HR
    g_yday = db.garmin_for(yday)
    y_kcal, y_p, y_f, y_c, y_n = db.day_totals(yday)

    lines = [f"🌅 Доброе утро! {today.strftime('%d.%m')}"]

    sleep_h = None
    src = g_today or g_yday
    if src:
        bits = []
        if g_today and g_today[4]:
            sleep_h = round(g_today[4] / 3600, 1)
            bits.append(f"сон {sleep_h} ч")
        if src[3]:
            bits.append(f"пульс покоя {src[3]}")
        if bits:
            lines.append("😴 " + " · ".join(bits))

    deficit = None
    if y_n:
        y_line = f"Вчера: ел {y_kcal} ккал (Б{y_p})"
        if g_yday and g_yday[0]:
            deficit = g_yday[0] - y_kcal
            y_line += f", сжёг {g_yday[0]} → дефицит {deficit}"
            y_line += " ✅" if 800 <= deficit <= 1600 else ""
        lines.append(y_line)

    ws = db.recent_weights(30)
    weight_logged_today = bool(ws) and ws[-1][0] == today
    if ws:
        cur = float(ws[-1][1])
        lines.append(f"⚖️ Вес: {cur} кг (до цели {round(cur - GOAL_WEIGHT, 1)} кг)"
                     + ("" if weight_logged_today else " — сегодня ещё не записан, /weight"))
    else:
        lines.append("⚖️ Запиши первый вес: /weight 87.0")

    lines.append(f"🎯 План: {TARGET_KCAL} ккал · белок {PROTEIN_TARGET} г")

    ctx = {
        "mode": "morning", "sleep_h": sleep_h,
        "yesterday": {"kcal": y_kcal, "protein_g": y_p, "meals": y_n,
                      "burned": g_yday[0] if g_yday else None, "deficit": deficit},
        "yesterday_protein_low": y_n > 0 and y_p < PROTEIN_TARGET * 0.85,
        "weight_logged": weight_logged_today,
        "weights_last_14d": [[str(d), float(k)] for d, k in db.recent_weights(14)],
        "target_kcal": TARGET_KCAL, "protein_target": PROTEIN_TARGET,
        "goal_weight": GOAL_WEIGHT,
    }
    tips = advice.get(ctx, "morning")
    if tips:
        lines.append("")
        lines.append("💡 Рекомендации:")
        lines.extend(tips)

    tg_send("\n".join(lines))
    print("morning report sent.")


if __name__ == "__main__":
    main()
