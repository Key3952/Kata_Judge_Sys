"""Модуль подсчёта оценок (scoring).

Правила (восстановлены из прежней логики проекта):
- Балл за технику = 10 - сумма штрафов; если техника «забыта» — 0.
- Если судья отметил хотя бы одну забытую технику, итог судьи по ката
  делится пополам.
- Итог пары: 3-4 судьи — сумма всех; 5+ судей — сумма трёх средних
  (отбрасываются лучшая и худшая оценки).
- Тай-брейк: выше место у пары с меньшим номером.
- П.9: если техника не выполняется (not_performed) — она исключается
  из расчёта (не штрафует), количество оцениваемых техник = techniques_count.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_TECHNIQUES = 10
BASE_SCORE = 10.0


@dataclass
class TechniqueResult:
    index: int
    penalty: float = 0.0
    forgotten: bool = False
    not_performed: bool = False
    score: float | None = None  # None => не учитывается


@dataclass
class JudgeProtocol:
    """Протокол одного судьи по паре."""

    techniques: list[TechniqueResult] = field(default_factory=list)

    def compute(self) -> float:
        """Итоговый балл судьи по паре (с правилом /2 за забытую технику)."""
        total = 0.0
        has_forgotten = False
        for t in self.techniques:
            if t.not_performed:
                continue
            if t.forgotten:
                has_forgotten = True
                t.score = 0.0
            else:
                t.score = max(0.0, BASE_SCORE - t.penalty)
            total += t.score
        if has_forgotten:
            total /= 2.0
        return round(total, 2)


def protocol_from_raw(raw: dict, techniques_count: int = MAX_TECHNIQUES) -> JudgeProtocol:
    """Парсинг сохранённой формы судьи в JudgeProtocol.

    Ожидаемый формат raw: {"techniques": [{"penalty": 0.5, "forgotten": false,
    "not_performed": false}, ...]} — флаги штрафов приходят как список кодов,
    вес каждого кода задаётся PENALTY_WEIGHTS.
    """
    weights = PENALTY_WEIGHTS
    proto = JudgeProtocol()
    techs = raw.get("techniques") or []
    for i in range(techniques_count):
        src = techs[i] if i < len(techs) else {}
        flags = src.get("flags") or []
        penalty = float(src.get("penalty", 0.0) or 0.0)
        penalty += sum(float(weights.get(f, 0.0)) for f in flags)
        proto.techniques.append(
            TechniqueResult(
                index=i + 1,
                penalty=round(penalty, 2),
                forgotten=bool(src.get("forgotten")) or "forgotten" in flags,
                not_performed=bool(src.get("not_performed")),
            )
        )
    return proto


PENALTY_WEIGHTS: dict[str, float] = {
    "minor_error": 0.5,
    "major_error": 1.0,
    "correction": 0.5,
}


def final_score(judge_totals: list[float]) -> float:
    """Итог пары по всем судьям: 3-4 — сумма, 5+ — сумма трёх средних."""
    totals = sorted(t for t in judge_totals if t is not None)
    if not totals:
        return 0.0
    if len(totals) >= 5:
        trimmed = totals[1:-1]  # отбросить min и max
        # при >5 судей берём средние три: ещё один trim
        while len(trimmed) > 3:
            trimmed = trimmed[1:-1]
        return round(sum(trimmed), 2)
    return round(sum(totals), 2)


def rank_pairs(rows: list[dict]) -> list[dict]:
    """Ранжирование: по убыванию final_score, тай-брейк — меньший номер пары.
    Пары без оценок получают 0 и остаются в списке (п.5 — не пропадают)."""
    ranked = sorted(rows, key=lambda r: (-float(r.get("final_score", 0) or 0),
                                         int(r.get("pair_number", 0))))
    for place, row in enumerate(ranked, start=1):
        row["place"] = place
    return ranked
