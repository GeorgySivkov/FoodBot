#!/usr/bin/env python3
"""advice.py — short personalized recommendations for morning/evening reports.

Engine order:
  1. Claude (SDK if ANTHROPIC_API_KEY, else `claude` CLI) — 2-4 живые строки
  2. rule-based fallback — если Claude недоступен, отчёт всё равно уходит

Disable LLM entirely with ADVICE_LLM=off in .env.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

import engine

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

SYSTEM = """Ты — личный ассистент George по питанию и спорту. Цель: 87 → {goal} кг.
План: {target} ккал/день, белок {protein}+ г. Зал 3-4р/нед + плавание в море утром.
Правила безопасности: если сила в зале падает 2 сессии подряд или дефицит " \
"стабильно >1500 — советуй поднять калории; белок не жертвуем никогда.

По данным ниже дай 2-4 КОРОТКИХ конкретных рекомендации ({mode_hint}).
Пиши по-русски, по делу, без воды и мотивационных банальностей. Каждая строка начинается с "- ".
Не повторяй сами цифры из данных — они уже в отчёте, давай только выводы и действия."""

MODE_HINT = {
    "morning": "на сегодняшний день: как распределить еду, стоит ли менять нагрузку с учётом сна и вчерашнего дня",
    "evening": "выводы по прошедшему дню и 1-2 фокуса на завтра",
}


def _rules(ctx: dict, mode: str) -> list[str]:
    out = []
    p, tgt_p = ctx.get("protein_g", 0), ctx.get("protein_target", 160)
    kcal, tgt = ctx.get("kcal", 0), ctx.get("target_kcal", 1650)
    deficit = ctx.get("deficit")
    sleep_h = ctx.get("sleep_h")
    if mode == "morning":
        if sleep_h and sleep_h < 6.5:
            out.append("- Сон короткий — сегодня лучше техника/лёгкий зал, не рекорды.")
        if ctx.get("yesterday_protein_low"):
            out.append("- Вчера не добрал белок — начни день с творога/яиц, не с углеводов.")
        if not ctx.get("weight_logged"):
            out.append("- Запиши вес до завтрака: /weight")
        out.append("- Держи скелет: завтрак 450 / обед 550 / перекус 200 / ужин 450.")
    else:
        if kcal > tgt:
            out.append("- Перебор по калориям — завтра без свободных перекусов, вернись в 1650.")
        if p < tgt_p:
            out.append(f"- Белок {p}/{tgt_p} — завтра добавь порцию рыбы или скира.")
        if deficit and deficit > 1600:
            out.append("- Дефицит очень большой — следи за силой в зале, при просадке ешь больше.")
        if not out:
            out.append("- День по плану. Завтра то же самое.")
    return out


def _llm(ctx: dict, mode: str) -> list[str] | None:
    prompt = SYSTEM.format(
        goal=ctx.get("goal_weight", 77), target=ctx.get("target_kcal", 1650),
        protein=ctx.get("protein_target", 160), mode_hint=MODE_HINT[mode])
    prompt += "\n\nДанные:\n" + json.dumps(ctx, ensure_ascii=False, default=str)
    try:
        text = engine.run(prompt, "food_advice")
        lines = [l.strip() for l in text.splitlines() if l.strip().startswith("- ")]
        return lines[:4] or None
    except Exception:
        return None


def get(ctx: dict, mode: str) -> list[str]:
    """mode: 'morning' | 'evening'. Returns list of '- ...' lines."""
    if os.environ.get("ADVICE_LLM", "").lower() != "off":
        lines = _llm(ctx, mode)
        if lines:
            return lines
    return _rules(ctx, mode)


# ── tomorrow's menu ──────────────────────────────────────────

MENU_PROMPT = """Ты — личный ассистент George по питанию. Составь КОНКРЕТНОЕ меню на завтра.

Рамки: {target} ккал (суббота — свободный день, тогда ~2100), белок {protein}+ г.
Скелет: завтрак ~450 (после плавания в море, белковый) / обед ~550 / перекус ~200 (перед залом) / ужин ~450.
Продукты: Бадалона — дорада, лубина, гамбас, сепия, мидии, бакалао, мерлуза, тунец из банки,
яйца, queso fresco batido 0% (скир), овощи и сезонные фрукты без ограничений, немного риса/картофеля.
Готовка: аэрогриль (масло только спреем), мидии — кастрюля.
{extra}
Не повторяй блюда последних дней: {recent}.
Если сегодня не добран белок — завтра сделай упор на него.

Ответь СТРОГО 4 строками, без вступления, каждая строка:
"- Завтрак ~450: ..." (и так далее: Обед, Перекус, Ужин; в конце строки итог белка в скобках не нужен)"""

# static rotation fallback: weekday-indexed dinners/lunches from ration.md
_ROTATION = [
    ("Скир 250г + персик + овсянка 30г", "Дорада в аэрогриле + перцы гриль + рис 100г", "Тунец + тост", "Гамбас + большой салат"),
    ("Яичница 3 яйца + помидор + тост", "Сепия гриль + цукини + картофель дольками", "Скир 150г", "Мидии 1 кг + тост"),
    ("Творог + арбуз", "Бакалао в аэрогриле + брокколи + фасоль 100г", "Фрукт + горсть миндаля", "Тортилья 3 яйца со шпинатом + салат"),
    ("Скир 250г + дыня", "Лубина в аэрогриле + спаржа + рис 100г", "Тунец + тост", "Гамбас аль ахильо + овощи гриль"),
    ("Яичница 3 яйца + перец", "Мерлуза + картофель дольками + салат", "Скир 150г", "Сепия + брокколи"),
    ("Скир 250г + персик + овсянка 30г", "Свободный день: паэлья/ресторан, держи ~2100 за день", "—", "Лёгкий ужин: салат + тунец"),
    ("Творог + фрукт", "Дорада целиком + овощи гриль + рис 100г", "Фрукт", "Куриные бёдра из аэрогриля + салат"),
]


def get_menu(ctx: dict) -> list[str]:
    """Menu lines for tomorrow's date. ctx needs: tomorrow_weekday (0=Mon),
    tomorrow_is_free_day, recent_dishes, protein_g, protein_target, target_kcal."""
    if os.environ.get("ADVICE_LLM", "").lower() != "off":
        extra = ""
        if ctx.get("tomorrow_is_free_day"):
            extra = "Завтра суббота — свободный день: обед можно ресторанный, но впиши его в ~2100 ккал суммарно."
        if ctx.get("protein_g", 0) < ctx.get("protein_target", 160) * 0.9:
            extra += "\nСегодня белок не добран — завтра упор на белок."
        prompt = MENU_PROMPT.format(
            target=ctx.get("target_kcal", 1650),
            protein=ctx.get("protein_target", 160),
            extra=extra,
            recent=", ".join(ctx.get("recent_dishes", [])[:15]) or "нет данных")
        try:
            text = engine.run(prompt, "food_menu")
            lines = [l.strip() for l in text.splitlines() if l.strip().startswith("- ")]
            if len(lines) >= 3:
                return lines[:4]
        except Exception:
            pass
    b, l, s, d = _ROTATION[ctx.get("tomorrow_weekday", 0) % 7]
    return [f"- Завтрак ~450: {b}", f"- Обед ~550: {l}",
            f"- Перекус ~200: {s}", f"- Ужин ~450: {d}"]
