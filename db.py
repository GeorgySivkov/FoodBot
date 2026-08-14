#!/usr/bin/env python3
"""db.py — thin Postgres helpers for foodbot (psycopg3, PG* env vars)."""
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

TZ = os.environ.get("FOOD_TZ", "Europe/Madrid")


def connect():
    # psycopg reads PGHOST / PGUSER / PGPASSWORD / PGDATABASE / PGSSLMODE itself.
    # PersonalOS: one shared Postgres, schema per domain — we live in `health`.
    return psycopg.connect(options="-c search_path=health,public")


def today() -> date:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT (now() AT TIME ZONE %s)::date", (TZ,))
        return cur.fetchone()[0]


def add_meal(dish, kcal, protein_g, fat_g, carbs_g, confidence=None,
             source="photo", photo_file_id=None, raw_json=None):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO meals (day, dish, kcal, protein_g, fat_g, carbs_g,
                                  confidence, source, photo_file_id, raw_json)
               VALUES ((now() AT TIME ZONE %s)::date, %s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (TZ, dish, kcal, protein_g, fat_g, carbs_g,
             confidence, source, photo_file_id, raw_json))
        return cur.fetchone()[0]


def undo_last_meal():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """DELETE FROM meals WHERE id =
                 (SELECT id FROM meals ORDER BY created_at DESC LIMIT 1)
               RETURNING dish, kcal""")
        return cur.fetchone()


def day_totals(d: date):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(kcal),0), COALESCE(SUM(protein_g),0),
                      COALESCE(SUM(fat_g),0), COALESCE(SUM(carbs_g),0), COUNT(*)
               FROM meals WHERE day = %s""", (d,))
        return cur.fetchone()


def day_meals(d: date):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT to_char(eaten_at AT TIME ZONE %s, 'HH24:MI'), dish, kcal, protein_g
               FROM meals WHERE day = %s ORDER BY eaten_at""", (TZ, d))
        return cur.fetchall()


def week_rows(end: date, days: int = 7):
    start = end - timedelta(days=days - 1)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.day, SUM(m.kcal), SUM(m.protein_g),
                      g.total_kcal, g.steps
               FROM meals m LEFT JOIN garmin_daily g ON g.day = m.day
               WHERE m.day BETWEEN %s AND %s
               GROUP BY m.day, g.total_kcal, g.steps ORDER BY m.day""",
            (start, end))
        return cur.fetchall()


def garmin_for(d: date):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT total_kcal, active_kcal, steps, resting_hr,
                      sleep_seconds, activities
               FROM garmin_daily WHERE day = %s""", (d,))
        return cur.fetchone()


def upsert_garmin(day, total_kcal, active_kcal, steps, resting_hr,
                  sleep_seconds, body_battery_max, activities_json):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO garmin_daily (day, total_kcal, active_kcal, steps,
                     resting_hr, sleep_seconds, body_battery_max, activities, synced_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (day) DO UPDATE SET
                     total_kcal = EXCLUDED.total_kcal,
                     active_kcal = EXCLUDED.active_kcal,
                     steps = EXCLUDED.steps,
                     resting_hr = EXCLUDED.resting_hr,
                     sleep_seconds = EXCLUDED.sleep_seconds,
                     body_battery_max = EXCLUDED.body_battery_max,
                     activities = EXCLUDED.activities,
                     synced_at = now()""",
            (day, total_kcal, active_kcal, steps, resting_hr,
             sleep_seconds, body_battery_max, activities_json))


def set_weight(d: date, kg: float, source="manual"):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO weights (day, kg, source) VALUES (%s,%s,%s)
               ON CONFLICT (day) DO UPDATE SET kg = EXCLUDED.kg, source = EXCLUDED.source""",
            (d, kg, source))


def log_usage(command: str, cost_usd, tokens: int):
    """Write to the SHARED public.usage_log ledger (same one JobBot uses)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.usage_log (command, cost_usd, tokens) VALUES (%s,%s,%s)",
            (command, cost_usd, tokens))


def month_spend():
    """(total_all_bots, n_calls, foodbot_share) for the current month."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(cost_usd),0), COUNT(*),
                      COALESCE(SUM(cost_usd) FILTER (WHERE command LIKE 'food%%'),0)
               FROM public.usage_log
               WHERE date_trunc('month', ts) = date_trunc('month', now())""")
        total, n, food = cur.fetchone()
        return float(total), n, float(food)


def recent_dishes(days: int = 3):
    """Dish names from the last N days — so tomorrow's menu rotates, not repeats."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT dish FROM meals
               WHERE day >= (now() AT TIME ZONE %s)::date - %s
               ORDER BY dish""", (TZ, days))
        return [r[0] for r in cur.fetchall()]


def recent_weights(days: int = 14):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT day, kg FROM weights ORDER BY day DESC LIMIT %s""", (days,))
        return list(reversed(cur.fetchall()))
