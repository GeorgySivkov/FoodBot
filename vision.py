#!/usr/bin/env python3
"""vision.py — estimate calories/macros from a food photo (or text) via Claude.

All model calls go through engine.py (SDK or subscription CLI) and are
logged to the shared usage ledger as 'food_photo'.

Returns a dict:
  {dish, items: [{name, grams, kcal}], kcal, protein_g, fat_g, carbs_g,
   confidence: high|medium|low, notes}
"""
import json
import re

import engine

PROMPT = """Ты — нутрициолог. Оцени еду на фото (и/или по описанию) и посчитай калории и БЖУ.

Правила:
- Оценивай реальный размер порции по тарелке/приборам/руке в кадре.
- Если сомневаешься между двумя оценками — бери среднее, укажи confidence.
- Учитывай масло/соусы, даже если их не видно (жарка ~ +5-10 г масла), если не сказано "аэрогриль/без масла".
- Подпись пользователя (если есть) важнее фото: там могут быть граммы или состав.

Ответь СТРОГО одним JSON-объектом без markdown-обёртки:
{"dish": "короткое название по-русски",
 "items": [{"name": "...", "grams": 0, "kcal": 0}],
 "kcal": 0, "protein_g": 0, "fat_g": 0, "carbs_g": 0,
 "confidence": "high|medium|low",
 "notes": "1 короткая строка: допущения"}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    # tolerate ```json fences or prose around the object
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:200]}")
    data = json.loads(m.group(0))
    for k in ("kcal", "protein_g", "fat_g", "carbs_g"):
        data[k] = int(round(float(data.get(k) or 0)))
    data["dish"] = str(data.get("dish") or "Еда")[:200]
    data.setdefault("confidence", "medium")
    data.setdefault("items", [])
    data.setdefault("notes", "")
    return data


def analyze(image_bytes: bytes | None, caption: str | None) -> dict:
    prompt = PROMPT
    if caption:
        prompt += f"\nПодпись пользователя: {caption}"
    if not image_bytes:
        prompt += "\n(Фото нет — считай только по описанию.)"
    return _extract_json(engine.run(prompt, "food_photo", image_bytes))
