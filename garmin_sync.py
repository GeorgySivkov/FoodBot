#!/usr/bin/env python3
"""garmin_sync.py — pull daily stats from Garmin Connect into Postgres.

Uses the unofficial `garminconnect` library (>=0.3, garth-based OAuth tokens).

IMPORTANT: Garmin blocks the SSO *login* endpoint from datacenter IPs
(Cloudflare 429). Do the one-time login from a residential IP (your laptop),
then copy the token dir to the server — API calls with cached tokens work fine
from anywhere:

    # on your laptop (asks email/password/MFA interactively if no .env):
    python3 garmin_sync.py --login
    scp -r ~/.garminconnect user@server:~/

    # on the server (cron):
    python3 garmin_sync.py            # today + yesterday
    python3 garmin_sync.py --days 7   # backfill a week

Tokens live ~a year; the library refreshes them automatically.
"""
import argparse
import getpass
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from garminconnect import Garmin

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

TOKEN_DIR = os.path.expanduser(os.environ.get("GARMIN_TOKEN_DIR", "~/.garminconnect"))


def local_today() -> date:
    """'Today' in the user's timezone, not the server's (servers run UTC —
    right after midnight in Madrid the UTC date is still yesterday)."""
    return datetime.now(ZoneInfo(os.environ.get("FOOD_TZ", "Europe/Madrid"))).date()


def _dump_tokens(api):
    """Persist tokens; attr name differs across garminconnect versions."""
    client = getattr(api, "client", None) or getattr(api, "garth", None)
    if client is not None and hasattr(client, "dump"):
        client.dump(TOKEN_DIR)


def login_from_tokens() -> Garmin:
    api = Garmin()
    api.login(TOKEN_DIR)  # raises if tokens missing/expired
    return api


def login_with_credentials() -> Garmin:
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password (hidden): ")
    try:
        # modern garminconnect (>=0.3): explicit MFA round-trip
        api = Garmin(email=email, password=password, return_on_mfa=True)
        status, ctx = api.login()
        if status == "needs_mfa":
            code = input("Garmin MFA code: ").strip()
            api.resume_login(ctx, code)
    except TypeError:
        # older garminconnect (e.g. on a laptop with old Python):
        # login() prompts for MFA by itself
        api = Garmin(email, password)
        api.login()
    _dump_tokens(api)
    print(f"Logged in, tokens cached in {TOKEN_DIR}")
    # re-login from the token store so the profile is fully loaded
    return login_from_tokens()


def login(force_credentials=False) -> Garmin:
    if not force_credentials:
        try:
            return login_from_tokens()
        except Exception as e:
            print(f"token login failed ({e}); falling back to credentials")
    return login_with_credentials()


def sync_day(api: Garmin, d: date):
    import db  # lazy: --login on a laptop needs no database

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

    # Garmin scale weigh-ins, if the account ever gets one
    try:
        bc = api.get_body_composition(ds) or {}
        for e in bc.get("dateWeightList") or []:
            if e.get("weight"):
                db.set_weight(d, round(e["weight"] / 1000.0, 2), source="garmin")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true",
                    help="interactive credential login (run on a residential IP), cache tokens, exit")
    ap.add_argument("--days", type=int, default=2, help="how many days back to sync (default 2)")
    args = ap.parse_args()

    if args.login:
        api = login(force_credentials=True)
        # smoke test that the tokens actually work — no database needed
        today = local_today().isoformat()
        stats = api.get_stats(today) or {}
        print(f"token check OK: {today}, {stats.get('totalSteps')} steps so far")
        print(f"Now copy tokens to the server:  scp -r {TOKEN_DIR} user@server:~/")
        return

    api = login()
    today = local_today()
    for i in range(args.days):
        sync_day(api, today - timedelta(days=i))
    print("done.")


if __name__ == "__main__":
    main()