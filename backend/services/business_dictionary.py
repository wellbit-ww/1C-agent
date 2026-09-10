from services.column_resolver import SEMANTIC_ALIASES

# Имена сущностей, которые ждут report_detector / профили.
# Алиасы живут в SEMANTIC_ALIASES — один источник, без расхождения словарей.
ENTITY_TO_SEMANTIC = {
    "revenue": ("revenue", "sales", "amount", "income"),
    "customer": ("client",),
    "manager": ("manager",),
    "department": ("department",),
    "region": ("region",),
    "date": ("date", "month"),
    "deficit": ("deficit",),
}


def _aliases_for_entity(entity: str) -> list[str]:
    names: list[str] = []
    for semantic in ENTITY_TO_SEMANTIC.get(entity, ()):
        names.extend(SEMANTIC_ALIASES.get(semantic, []))
    return list(dict.fromkeys(names))


BUSINESS_ENTITIES = {entity: _aliases_for_entity(entity) for entity in ENTITY_TO_SEMANTIC}


def detect_entities(columns: list[str]) -> list[str]:
    """Detects which business entities are present in the columns."""
    found = set()
    cols_norm = [str(c).lower().strip() for c in columns]

    for entity in ENTITY_TO_SEMANTIC:
        aliases = _aliases_for_entity(entity)
        for alias in aliases:
            # только alias внутри имени колонки: иначе «сумма» матчит
            # алиас «сумма долга» и любой файл с суммой становится дефицитом
            if any(alias == col or alias in col for col in cols_norm):
                found.add(entity)
                break

    return list(found)
