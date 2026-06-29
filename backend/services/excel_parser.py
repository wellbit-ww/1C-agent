import pandas as pd

def _clean_column_name(col) -> str:
    if pd.isna(col):
        return ""
    name = str(col).strip().lower()
    if "сумма" in name and ("руб" in name or "₽" in name):
        return "sum"
    return name

def _find_header_row_and_clean(df: pd.DataFrame) -> pd.DataFrame:
    # Drop completely empty rows and columns
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty:
        return df
        
    best_row_idx = 0
    max_non_empty_strings = -1
    
    for i in range(min(20, len(df))):
        row = df.iloc[i]
        non_empty_strings = sum(1 for x in row if pd.notna(x) and isinstance(x, str) and str(x).strip() != "")
        if non_empty_strings > max_non_empty_strings:
            max_non_empty_strings = non_empty_strings
            best_row_idx = i

    # Set headers
    headers = df.iloc[best_row_idx].apply(_clean_column_name).tolist()
    # Ensure unique headers
    seen = set()
    unique_headers = []
    for h in headers:
        if not h:
            h = "unnamed"
        base_h = h
        counter = 1
        while h in seen:
            h = f"{base_h}_{counter}"
            counter += 1
        seen.add(h)
        unique_headers.append(h)

    df.columns = unique_headers
    df = df.iloc[best_row_idx + 1:].copy()
    
    # Drop rows that are completely empty again after header selection
    df = df.dropna(how="all")
    # Drop rows that look like technical totals (e.g., "Итого" in the first column)
    if not df.empty:
        first_col = df.columns[0]
        # Filter out rows where the first column starts with "Итого"
        mask = df[first_col].astype(str).str.lower().str.startswith("итого")
        df = df[~mask]
    
    # Restore data types after removing the header row
    df = df.infer_objects()
    
    return df.reset_index(drop=True)

def parse_excel(file_path: str) -> dict[str, pd.DataFrame]:
    """Reads all sheets, finds headers, and returns a dict of dataframes."""
    sheets = pd.read_excel(file_path, sheet_name=None, header=None)
    cleaned_sheets = {}
    for sheet_name, df in sheets.items():
        cleaned_df = _find_header_row_and_clean(df)
        if not cleaned_df.empty:
            cleaned_sheets[sheet_name] = cleaned_df
    return cleaned_sheets
