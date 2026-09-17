"""因子庫：import 即註冊全部因子。"""
from .registry import (
    FACTOR_REGISTRY, register, compute_factor, compute_all_factors, list_factors,
)
from . import value, growth, momentum, chips, revenue, reversal, breakout, legacy  # noqa: F401
from .legacy import legacy_params_to_factors
from .panel import build_panel

__all__ = ["FACTOR_REGISTRY", "register", "compute_factor", "compute_all_factors",
           "list_factors", "build_panel", "legacy_params_to_factors"]
