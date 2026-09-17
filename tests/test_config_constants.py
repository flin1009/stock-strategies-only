from stock_strategies import config


def test_cache_and_ratelimit_constants_exist():
    assert isinstance(config.CACHE_FRESH_DAYS, dict)
    assert config.CACHE_FRESH_DAYS["daily"] == 1
    assert config.CACHE_FRESH_DAYS["monthly"] == 20
    assert config.FINMIND_MIN_INTERVAL > 0
    assert config.RATE_LIMIT_BACKOFF_BASE == 5
    assert config.RATE_LIMIT_MAX_RETRIES == 4
    assert config.MIN_PRICE_ROWS == 60
    assert config.FINMIND_CACHE_DIR  # 非空字串
