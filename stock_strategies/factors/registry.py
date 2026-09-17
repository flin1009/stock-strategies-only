"""因子註冊表。缺 required_data → None（C3）；內部算掛 → 0.5；composite 剔除 None。"""
from __future__ import annotations

from .base import NEUTRAL, has_rows

FACTOR_REGISTRY: dict[str, "FactorEntry"] = {}


class FactorEntry:
    def __init__(self, fn, name, school, required_data, description, lookback_min):
        self.fn = fn
        self.name = name
        self.school = school
        self.required_data = required_data
        self.description = description
        self.lookback_min = lookback_min

    def __call__(self, ctx, params):
        for need in self.required_data:
            min_rows = self.lookback_min if need == "price_df" else 1
            if need == "fundamentals":
                if not getattr(ctx, "fundamentals", None):
                    return None
            elif not has_rows(ctx, need, min_rows):
                return None
        try:
            return self.fn(ctx, params)
        except Exception:
            return NEUTRAL


def register(name, school, required_data, description="", lookback_min=60):
    def deco(fn):
        FACTOR_REGISTRY[name] = FactorEntry(fn, name, school, required_data,
                                            description, lookback_min)
        fn.factor_name = name
        return fn
    return deco


def compute_factor(name, ctx, params):
    entry = FACTOR_REGISTRY.get(name)
    if entry is None:
        return None
    return entry(ctx, params)


def compute_all_factors(ctx, factor_list, params):
    """composite = Σ(score·weight)/Σ(weight)，只計非 None（C3）。
    回 {composite, used:[{name,score,weight}], missing:[name]}。"""
    num = den = 0.0
    used, missing = [], []
    for f in factor_list:
        name = f["name"]
        w = float(f.get("weight", 1.0))
        s = compute_factor(name, ctx, params)
        if s is None:
            missing.append(name)
            continue
        num += s * w
        den += w
        used.append({"name": name, "score": round(float(s), 3), "weight": w})
    composite = (num / den) if den > 0 else 0.5
    return {"composite": composite, "used": used, "missing": missing}


def list_factors(school: str | None = None) -> list[dict]:
    return [{"name": e.name, "school": e.school, "required_data": e.required_data,
             "description": e.description}
            for e in FACTOR_REGISTRY.values()
            if school is None or e.school == school]
