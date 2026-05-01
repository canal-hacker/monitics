from pathlib import Path
from typing import Any

import pandas as pd


def save_dataframe(df: pd.DataFrame, path: Path, index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=index)


def save_dict(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(path, "write_text"):
        path.write_text(str(data), encoding="utf-8")
