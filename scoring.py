def calculate_technique_score(penalties, forgotten_flag):
    """
    Рассчитывает балл за одну технику.
    penalties: список штрафов, например [-1, -1] для двух малых ошибок
    forgotten_flag: bool, забытая техника
    Возвращает: балл от 0 до 10
    """
    base_score = 10.0
    score = base_score

    # Применяем штрафы
    for penalty in penalties:
        score += penalty  # penalties отрицательные

    # Если забытая техника, ставим 0
    if forgotten_flag:
        score = 0.0

    # Ограничиваем 0-10
    score = max(0.0, min(10.0, score))

    return score

def calculate_judge_total_score(technique_scores, forgotten_flags):
    """
    Рассчитывает итоговый балл судьи за пару.
    technique_scores: список из 17 баллов за техники
    forgotten_flags: список из 17 bool для забытых техник
    Возвращает: итоговый балл судьи
    """
    total = sum(technique_scores)

    # Если есть хотя бы одна забытая техника, делим на 2
    if any(forgotten_flags):
        total /= 2.0

    return total

def calculate_pair_final_score(judge_totals):
    """
    Рассчитывает итоговый балл пары.
    judge_totals: список баллов от 5 судей
    Возвращает: сумма после отбрасывания max и min
    """
    if len(judge_totals) < 3:
        return sum(judge_totals) / len(judge_totals) if judge_totals else 0.0

    sorted_scores = sorted(judge_totals)
    trimmed = sorted_scores[1:-1]  # убираем min и max
    return sum(trimmed)
