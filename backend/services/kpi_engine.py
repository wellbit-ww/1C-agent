import pandas as pd
from typing import Any
from services.column_resolver import resolve_semantic_column


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{float(value):,.2f}".replace(",", " ")


def _status_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = str(col).lower().strip()
        if name in {"статус", "status"} or name.startswith("статус"):
            return col
    return None


def _won_mask(df: pd.DataFrame) -> pd.Series | None:
    col = _status_column(df)
    if not col:
        return None
    return df[col].astype(str).str.lower().str.contains("выигран", na=False)


def calculate_total_revenue(df: pd.DataFrame) -> dict[str, Any] | None:
    col = resolve_semantic_column(df, "", semantic="revenue", dtype="numeric")
    if not col:
        return None
    val = float(pd.to_numeric(df[col], errors="coerce").sum())
    return {"name": "total_revenue", "value": val, "formatted": _format_number(val)}


def calculate_cancelled_share(df: pd.DataFrame) -> dict[str, Any] | None:
    col = _status_column(df)
    if not col or len(df) == 0:
        return None
    status = df[col].astype(str).str.lower()
    cancelled = status.str.contains("отмен", na=False)
    if not bool(cancelled.any()):
        return None
    share = 100.0 * float(cancelled.sum()) / float(len(df))
    text = f"{share:.1f}".replace(".", ",") + " %"
    return {"name": "cancelled_share", "value": share, "formatted": text}


def calculate_won_revenue(df: pd.DataFrame) -> dict[str, Any] | None:
    mask = _won_mask(df)
    if mask is None or not bool(mask.any()):
        return None
    col = resolve_semantic_column(df, "", semantic="revenue", dtype="numeric")
    if not col:
        return None
    val = float(pd.to_numeric(df.loc[mask, col], errors="coerce").sum())
    return {"name": "won_revenue", "value": val, "formatted": _format_number(val)}


def calculate_average_check(df: pd.DataFrame) -> dict[str, Any] | None:
    col = resolve_semantic_column(df, "", semantic="revenue", dtype="numeric")
    if not col:
        return None
    val = float(pd.to_numeric(df[col], errors="coerce").mean())
    return {"name": "average_check", "value": val, "formatted": _format_number(val)}

def calculate_total_deficit(df: pd.DataFrame) -> dict[str, Any] | None:
    # Need to find deficit col specifically or fallback to amount
    col = None
    for c in df.columns:
        lower = str(c).lower()
        if any(
            marker in lower
            for marker in ("дефицит", "остаток", "задолженность", "не оплачен", "неоплачен")
        ):
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
    if val < 2:
        return None
    return {"name": "unique_departments", "value": val, "formatted": str(val)}

def run_kpis(df: pd.DataFrame, kpi_names: list[str]) -> list[dict[str, Any]]:
    kpi_map = {
        "total_revenue": calculate_total_revenue,
        "won_revenue": calculate_won_revenue,
        "cancelled_share": calculate_cancelled_share,
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
