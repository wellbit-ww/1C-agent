import pandas as pd
from typing import Any
from services.column_resolver import resolve_semantic_column

def _format_number(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{float(value):,.2f}".replace(",", " ")

def calculate_total_revenue(df: pd.DataFrame) -> dict[str, Any] | None:
    col = resolve_semantic_column(df, "", semantic="revenue", dtype="numeric")
    if not col:
        return None
    val = float(df[col].sum())
    return {"name": "total_revenue", "value": val, "formatted": _format_number(val)}

def calculate_average_check(df: pd.DataFrame) -> dict[str, Any] | None:
    col = resolve_semantic_column(df, "", semantic="revenue", dtype="numeric")
    if not col:
        return None
    val = float(df[col].mean())
    return {"name": "average_check", "value": val, "formatted": _format_number(val)}

def calculate_total_deficit(df: pd.DataFrame) -> dict[str, Any] | None:
    # Need to find deficit col specifically or fallback to amount
    col = None
    for c in df.columns:
        if "дефицит" in str(c).lower() or "остаток" in str(c).lower() or "задолженность" in str(c).lower():
            if pd.api.types.is_numeric_dtype(df[c]):
                col = c
                break
    if not col:
        col = resolve_semantic_column(df, "", semantic="amount", dtype="numeric")
    if not col:
        return None
    val = float(df[col].sum())
    return {"name": "total_deficit", "value": val, "formatted": _format_number(val)}

def calculate_unique_customers(df: pd.DataFrame) -> dict[str, Any] | None:
    col = resolve_semantic_column(df, "", semantic="client", dtype="categorical")
    if not col:
        return None
    val = int(df[col].nunique())
    return {"name": "unique_customers", "value": val, "formatted": str(val)}

def calculate_unique_managers(df: pd.DataFrame) -> dict[str, Any] | None:
    col = resolve_semantic_column(df, "", semantic="manager", dtype="categorical")
    if not col:
        return None
    val = int(df[col].nunique())
    return {"name": "unique_managers", "value": val, "formatted": str(val)}

def calculate_row_count(df: pd.DataFrame) -> dict[str, Any]:
    val = int(len(df))
    return {"name": "row_count", "value": val, "formatted": str(val)}

def calculate_unique_departments(df: pd.DataFrame) -> dict[str, Any] | None:
    col = next((c for c in df.columns if "подразделение" in str(c).lower() or "отдел" in str(c).lower()), None)
    if not col:
        return None
    val = int(df[col].nunique())
    return {"name": "unique_departments", "value": val, "formatted": str(val)}

def run_kpis(df: pd.DataFrame, kpi_names: list[str]) -> list[dict[str, Any]]:
    kpi_map = {
        "total_revenue": calculate_total_revenue,
        "average_check": calculate_average_check,
        "total_deficit": calculate_total_deficit,
        "unique_customers": calculate_unique_customers,
        "unique_managers": calculate_unique_managers,
        "row_count": calculate_row_count,
        "unique_departments": calculate_unique_departments,
    }
    
    results = []
    for name in kpi_names:
        if name in kpi_map:
            res = kpi_map[name](df)
            if res:
                results.append(res)
    return results
