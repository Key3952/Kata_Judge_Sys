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


def calculate_pair_final_score(judge_totals: list) -> float:
    """
    Рассчитывает итоговый балл пары.
    judge_totals: список баллов от 5 судей
    Возвращает: сумма после отбрасывания max и min
    """
    if len(judge_totals) < 5:
        return None  # Недостаточно оценок
    
    sorted_scores = sorted(judge_totals)
    trimmed = sorted_scores[1:-1]  # убираем min и max (3 средних)
    return sum(trimmed)
