def get_basic_info(df):
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
        "dtypes": {
            col: str(dtype)
            for col, dtype in df.dtypes.items()
        },
        "sample_rows": df.head(3).to_dict(
            orient="records"
        )
    }