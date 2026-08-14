# FoodBot — AI nutrition & fitness assistant in Telegram

Snap a photo of your meal → Claude estimates calories and macros → everything lands in Postgres
next to your Garmin data → twice a day the bot briefs you: real calorie deficit, protein gap,
weight trend, personalized recommendations, and a concrete menu for tomorrow.

Built as the *health* module of my personal assistant system (see [JobBot](https://github.com/GeorgySivkov/JobBot)
for the *career* module — same stack, same database, same conventions).

```
meal photo in TG ──► foodbot.py ──► Claude vision ──► health.meals
                                                         │
Garmin Connect  ──► garmin_sync.py (cron) ──► health.garmin_daily, health.weights
                                                         │
   08:00 cron ──► morning.py ──► sleep, yesterday's deficit, today's plan, tips
   22:30 cron ──► report.py  ──► eaten vs burned, deficit → kg/week, tomorrow's menu
```

## What it looks like

Morning briefing:

```
🌅 Доброе утро! 15.08
😴 сон 7.2 ч · пульс покоя 52
Вчера: ел 1620 ккал (Б158), сжёг 3050 → дефицит 1430 ✅
⚖️ Вес: 86.6 кг (до цели 9.6 кг) — сегодня ещё не записан, /weight
🎯 План: 1650 ккал · белок 160 г

💡 Рекомендации:
- Сон короткий — сегодня лучше техника/лёгкий зал, не рекорды.
- Вчера не добрал белок — начни день с творога/яиц, не с углеводов.
```

Evening report ends with tomorrow's menu (rotated against the last 3 days of actual meals)
and the shared Claude budget line:

```
🍽 Меню на завтра (~1650 ккал):
- Завтрак ~450: скир 250г + персик + овсянка 30г
- Обед ~550: дорада в аэрогриле + перцы гриль + рис 100г
- Перекус ~200: тунец + тост
- Ужин ~450: гамбас + большой салат

⚙️ Claude за месяц: $12.40 из $100 (12%) · FoodBot $2.10
```

The bot currently speaks Russian; all prompts live in `vision.py` / `advice.py` and are trivial to localize.

## Features

- **Photo → calories in one message.** Claude vision estimates portion size from the plate and
  cutlery, accounts for cooking oil, and honors your caption ("350g, air fryer") over the photo.
  Text-only descriptions work too. `/undo` if it got it wrong.
- **Garmin Connect sync.** Daily TDEE, steps, workouts, sleep, resting HR pulled on cron via the
  unofficial API (tokens cached ~a year after one interactive login, MFA supported).
  Real deficit = Garmin burn − logged intake; no guessed "activity multipliers".
- **Two daily briefings.** Morning: sleep, yesterday's recap, today's plan. Evening: totals,
  deficit converted to kg/week, weight trend, recommendations, and a concrete menu for tomorrow.
- **Goal projection.** `/goal` fits your actual weigh-ins and predicts the date you hit target weight.
- **LLM with a safety net.** Recommendations and menus are written by Claude; if the model is
  unavailable, rule-based fallbacks fire — the reports always arrive.
- **Spend ledger.** Every model call is logged with its exact cost to a shared `usage_log` table,
  so the evening report shows combined monthly spend across all your bots vs. your plan's credit.

## Stack

Python · [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v22 ·
psycopg3 · PostgreSQL (schema-per-domain: this bot owns `health.*`) ·
[garminconnect](https://github.com/cyberjunky/python-garminconnect) ·
Claude (Anthropic SDK with an API key, or the `claude` CLI drawing on a subscription's monthly credit — pick via `.env`)

Runs on any small VPS. No frameworks, no Docker required: one venv, systemd for the bot, cron for sync and reports.

## Setup

```bash
git clone https://github.com/GeorgySivkov/FoodBot.git ~/foodbot && cd ~/foodbot
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# database (PG* env vars; safe to share a database with other bots — tables live in the `health` schema)
psql -f schema.sql

# config
cp .env.example .env      # Telegram bot token (@BotFather), Garmin creds, PG*, targets

# Garmin: one interactive login (asks for MFA if enabled), tokens cached after that
python3 garmin_sync.py --login

# first run
python3 foodbot.py        # message the bot — it replies with your Telegram ID,
                          # put it in .env → TELEGRAM_ALLOWED_USER_ID, restart
```

Then make it permanent — systemd unit for the bot and three cron lines:

```ini
# /etc/systemd/system/foodbot.service
[Unit]
Description=foodbot telegram bot
After=network-online.target

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/foodbot
ExecStart=/home/YOUR_USER/foodbot/.venv/bin/python foodbot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```cron
15 13,18 * * * cd $HOME/foodbot && .venv/bin/python garmin_sync.py >> sync.log 2>&1
0  8     * * * cd $HOME/foodbot && .venv/bin/python morning.py    >> report.log 2>&1
30 22    * * * cd $HOME/foodbot && .venv/bin/python report.py     >> report.log 2>&1
```

All targets (daily kcal, protein, goal weight/date, timezone) are `.env` variables — no code edits to retune.

## Cost

With calls routed through a Claude subscription's CLI credit: roughly **$10–15/month** of credit
(≈8 calls/day, dominated by CLI overhead). Through a direct API key: **$2–3/month** of real money.
Set `VISION_MODEL` to a Haiku-class model to cut costs ~3× — photo estimates barely suffer.

## Honest caveats

- Photo calorie estimates are **±15–20%**. That's fine: the system tracks a *trend of deficit*,
  and consistency matters more than absolute precision. Captions with grams tighten it up.
- The Garmin API is **unofficial**. If Garmin changes something, sync may break until
  `pip install -U garminconnect`. Your password is only used once; cached tokens do the rest.
- This is a personal tool, single-user by design (the bot is locked to one Telegram ID).
- Not medical advice. Aggressive cuts deserve a doctor's opinion, not a chatbot's.

## License

MIT
