_MAX_CELL_LEN = 200


def _truncate_cell(value) -> str:
    text = str(value)
    if len(text) <= _MAX_CELL_LEN:
        return text
    return text[:_MAX_CELL_LEN] + "…"


def get_basic_info(df):
    # значения обрезаем: в 1С-выгрузках комментарии содержат целые письма,
    # без обрезки промпт для LLM раздувается до неработоспособности
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
        "dtypes": {
            col: str(dtype)
            for col, dtype in df.dtypes.items()
        },
        "sample_rows": [
            {str(k): _truncate_cell(v) for k, v in row.items()}
            for row in df.head(3).to_dict(orient="records")
        ]
    }