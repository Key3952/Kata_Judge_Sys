def calculate_technique_score(penalty_value: float = 0.0, forgotten_flag: bool = False) -> float:
    """
    Рассчитывает балл за одну технику.
    penalty_value: штраф (отрицательное число: -0.5, -1, -3, -5)
    forgotten_flag: bool, забытая техника
    Возвращает: балл от 0 до 10
    """
    base_score = 10.0
    score = base_score + penalty_value  # penalty_value уже отрицательное
    
    # Если забытая техника, ставим 0
    if forgotten_flag:
        score = 0.0
    
    # Ограничиваем 0-10
    score = max(0.0, min(10.0, score))
    
    return score


def calculate_judge_total_score(technique_scores: list, forgotten_flags: list) -> float:
    """
    Рассчитывает итоговый балл судьи за пару.
    technique_scores: список баллов за техники
    forgotten_flags: список bool для забытых техник
    Возвращает: итоговый балл судьи
    """
    total = sum(technique_scores)
    
    # Если есть хотя бы одна забытая техника, делим на 2
    if any(forgotten_flags):
        total /= 2.0
    
    return total


def calculate_pair_final_score(judge_totals: list, judge_count: int = 5) -> float:
    """
    Рассчитывает итоговый балл пары.
    judge_totals: список баллов от судей
    judge_count:
      - 3 -> сумма трех
      - 4 -> сумма четырех
      - >=5 -> по первым 5: отбрасывание max/min и сумма 3 средних
    """
    if judge_count < 3:
        return None
    if judge_count > 5:
        judge_count = 5

    vals = [float(x) for x in judge_totals[:judge_count] if x is not None]
    if len(vals) < judge_count:
        return None

    if judge_count == 3:
        return sum(vals)
    if judge_count == 4:
        return sum(vals)

    sorted_scores = sorted(vals[:5])
    trimmed = sorted_scores[1:-1]
    return sum(trimmed)
