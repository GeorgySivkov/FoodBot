#!/usr/bin/env python3
"""garmin_sync.py — pull daily stats from Garmin Connect into Postgres.

Uses the unofficial `garminconnect` library (garth-based OAuth tokens).

First run (interactive, once — tokens then live ~1 year):
    python3 garmin_sync.py --login

Daily cron run (add e.g. 4 runs/day so the evening report is fresh):
    python3 garmin_sync.py            # today + yesterday
    python3 garmin_sync.py --days 7   # backfill a week
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from garminconnect import Garmin

import db

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

TOKEN_DIR = os.path.expanduser(os.environ.get("GARMIN_TOKEN_DIR", "~/.garminconnect"))
EMAIL = os.environ.get("GARMIN_EMAIL")
PASSWORD = os.environ.get("GARMIN_PASSWORD")


def login(force_credentials=False) -> Garmin:
    if not force_credentials:
        try:
            api = Garmin()
            api.login(TOKEN_DIR)          # resume from cached tokens
            return api
        except Exception:
            pass
    if not (EMAIL and PASSWORD):
        sys.exit("No cached Garmin tokens and GARMIN_EMAIL/GARMIN_PASSWORD not set in .env")
    api = Garmin(email=EMAIL, password=PASSWORD, return_on_mfa=True)
    result1, result2 = api.login()
    if result1 == "needs_mfa":
        code = input("Garmin MFA code: ").strip()
        api.resume_login(result2, code)
    api.garth.dump(TOKEN_DIR)
    print(f"Logged in, tokens cached in {TOKEN_DIR}")
    return api


def sync_day(api: Garmin, d: date):
    ds = d.isoformat()
    stats = api.get_stats(ds) or {}

    activities = []
    try:
        for a in api.get_activities_by_date(ds, ds) or []:
            activities.append({
                "name": a.get("activityName"),
                "type": (a.get("activityType") or {}).get("typeKey"),
                "duration_min": round((a.get("duration") or 0) / 60),
                "kcal": round(a.get("calories") or 0),
                "distance_km": round((a.get("distance") or 0) / 1000, 2),
            })
    except Exception as e:
        print(f"  activities failed for {ds}: {e}")

    sleep_seconds = stats.get("sleepingSeconds")
    if sleep_seconds is None:
        try:
            sl = api.get_sleep_data(ds) or {}
            sleep_seconds = (sl.get("dailySleepDTO") or {}).get("sleepTimeSeconds")
        except Exception:
            sleep_seconds = None

    db.upsert_garmin(
        day=d,
        total_kcal=stats.get("totalKilocalories"),
        active_kcal=stats.get("activeKilocalories"),
        steps=stats.get("totalSteps"),
        resting_hr=stats.get("restingHeartRate"),
        sleep_seconds=sleep_seconds,
        body_battery_max=stats.get("bodyBatteryHighestValue"),
        activities_json=json.dumps(activities, ensure_ascii=False),
    )
    print(f"  {ds}: {stats.get('totalKilocalories')} kcal burned, "
          f"{stats.get('totalSteps')} steps, {len(activities)} activities")

    # Garmin scale weigh-ins, if any
    try:
        bc = api.get_body_composition(ds) or {}
        for e in bc.get("dateWeightList") or []:
            if e.get("weight"):
                db.set_weight(d, round(e["weight"] / 1000.0, 2), source="garmin")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="interactive first login (MFA ok)")
    ap.add_argument("--days", type=int, default=2, help="how many days back to sync (default 2)")
    args = ap.parse_args()

    api = login(force_credentials=args.login)
    if args.login:
        pass  # tokens cached; still do a sync below to verify

    today = date.today()
    for i in range(args.days):
        sync_day(api, today - timedelta(days=i))
    print("done.")


if __name__ == "__main__":
    main()
