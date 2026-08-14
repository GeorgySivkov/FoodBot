-- ============================================================
--  foodbot — schema (deploy from scratch)
--  Run once:  psql -f schema.sql   (uses PG* env vars)
--
--  PersonalOS convention: one Postgres, a schema per domain.
--  This is the `health` domain (food, Garmin, weight).
--  JobBot (`jobs` domain) currently lives in public.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS health;
SET search_path TO health;

-- ------------------------------------------------------------
--  Shared spend ledger — same table JobBot uses (public schema).
--  No-op if JobBot's schema.sql already created it.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.usage_log (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts       timestamptz NOT NULL DEFAULT now(),
    command  text,                              -- food_photo / food_advice / food_menu / ...
    cost_usd numeric(10,5),
    tokens   int
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON public.usage_log (ts);

-- ------------------------------------------------------------
--  meals — one row per eaten meal (photo or text description)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meals (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    eaten_at      timestamptz NOT NULL DEFAULT now(),
    day           date NOT NULL DEFAULT (now() AT TIME ZONE 'Europe/Madrid')::date,
    dish          text NOT NULL,              -- short name, e.g. "Дорада на гриле + овощи"
    kcal          int  NOT NULL,
    protein_g     int  NOT NULL DEFAULT 0,
    fat_g         int  NOT NULL DEFAULT 0,
    carbs_g       int  NOT NULL DEFAULT 0,
    confidence    text,                       -- high | medium | low
    source        text NOT NULL DEFAULT 'photo' CHECK (source IN ('photo','text','manual')),
    photo_file_id text,                       -- Telegram file_id, to re-fetch the photo
    raw_json      jsonb,                      -- full model output (items breakdown, notes)
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_meals_day ON meals (day);

-- ------------------------------------------------------------
--  garmin_daily — one row per calendar day, upserted by garmin_sync.py
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS garmin_daily (
    day               date PRIMARY KEY,
    total_kcal        int,                    -- full TDEE for the day per Garmin
    active_kcal       int,
    steps             int,
    resting_hr        int,
    sleep_seconds     int,
    body_battery_max  int,
    activities        jsonb,                  -- [{name, type, duration_min, kcal, distance_km}]
    synced_at         timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
--  weights — morning weigh-ins (/weight 86.4 or Garmin scale sync)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weights (
    day        date PRIMARY KEY,
    kg         numeric(5,2) NOT NULL,
    source     text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','garmin')),
    created_at timestamptz NOT NULL DEFAULT now()
);
