from dataclasses import dataclass

BUSINESS_ENTITIES = {
    "revenue": [
        "выручка",
        "оборот",
        "сумма",
        "сумма продажи",
        "сумма заказа",
        "стоимость",
        "amount",
        "revenue",
        "income"
    ],
    "customer": [
        "клиент",
        "заказчик",
        "контрагент",
        "компания",
        "customer"
    ],
    "manager": [
        "менеджер",
        "ответственный",
        "продавец",
        "manager"
    ],
    "department": [
        "подразделение",
        "отдел",
        "департамент",
        "служба"
    ],
    "region": [
        "регион",
        "город",
        "область"
    ],
    "date": [
        "дата",
        "период",
        "месяц"
    ],
    "deficit": [
        "дефицит",
        "недостаток",
        "нехватка",
        "неоплаченн",
        "остаток",
        "задолженность"
    ]
}

def detect_entities(columns: list[str]) -> list[str]:
    """Detects which business entities are present in the columns."""
    found = set()
    cols_norm = [str(c).lower().strip() for c in columns]
    
    for entity, aliases in BUSINESS_ENTITIES.items():
        for alias in aliases:
            if any(alias in col or col in alias for col in cols_norm):
                found.add(entity)
                break
    
    return list(found)
