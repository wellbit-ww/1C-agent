import pandas as pd
from typing import Any

class ReportProfile:
    def get_kpis(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        return []
        
    def get_charts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        return []
        
    def get_insights(self, df: pd.DataFrame) -> list[str]:
        return []
        
    def answer_question(self, df: pd.DataFrame, question: str) -> Any:
        return None
