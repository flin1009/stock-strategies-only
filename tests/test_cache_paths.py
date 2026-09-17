from pathlib import Path
import pandas as pd
from stock_strategies import cache


def test_cache_path_format():
    p = cache.cache_path("TaiwanStockMonthRevenue", "2330")
    assert isinstance(p, Path)
    assert p.name == "TaiwanStockMonthRevenue__2330.parquet"


def test_clear_cache_counts_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("FINMIND_CACHE_DIR", str(tmp_path))
    # 寫兩個假快取檔
    for did in ("2330", "2317"):
        df = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "v": [1]})
        p = cache.cache_path("TaiwanStockPrice", did)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p)
        p.with_suffix(".meta.json").write_text("{}")
    removed = cache.clear_cache(dataset="TaiwanStockPrice")
    assert removed == 2
    assert cache.clear_cache() == 0  # 已清空
