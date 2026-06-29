import pandas as pd
from services.business_dictionary import detect_entities

def detect_report_type(df: pd.DataFrame) -> str:
    entities = detect_entities(df.columns.tolist())
    
    # Rules for sales pipeline
    sales_hits = 0
    if "revenue" in entities: sales_hits += 1
    if "customer" in entities: sales_hits += 1
    if any(c for c in df.columns if "этап" in str(c).lower() or "статус" in str(c).lower()):
        sales_hits += 1

    # Rules for deficit report
    deficit_hits = 0
    if "deficit" in entities: deficit_hits += 1
    if "customer" in entities: deficit_hits += 1
    if "department" in entities: deficit_hits += 1
    
    if sales_hits >= 2 and sales_hits >= deficit_hits:
        return "sales_pipeline"
    elif deficit_hits >= 2:
        return "deficit_report"
        
    return "unknown"
