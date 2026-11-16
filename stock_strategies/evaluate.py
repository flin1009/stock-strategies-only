from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from .config import CONFIG
from .data import get_fundamental, get_price_history
from .datasources import get_institutional, get_index_history
from .exchange_data import get_market_valuation_map, get_market_recent_inst_map
from .indicators import add_indicators, tech_score_at
from .backtest import backtest
from .volume import detect_patterns, verdict as volume_verdict
from .vcp import detect_vcp
from .loader import merge_params


def evaluate(stock_id: str, name: str, strategy: dict | None = None) -> Optional[dict]:
    """評估一檔股票。strategy 為策略 dict（含 params），不給就用預設值。"""
    params = merge_params(strategy)

    result = {
        "stock_id": stock_id,
        "name": name,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "strategy_id": (strategy or {}).get("id", "default"),
        "risk_notes": [],
    }

    try:
        sid_clean = str(stock_id).strip()
        is_etf = sid_clean.startswith("00")
        if is_etf:
            eps_vals, roe_vals = [], []
            fund_pass = True
        else:
            fund = get_fundamental(stock_id)
            eps_vals = list(fund["eps"].values())
            roe_vals = list(fund["roe"].values())
            fund_pass = (
                len(eps_vals) >= 2
                and len(roe_vals) >= 2
                and min(eps_vals) > params["eps_threshold"]
                and min(roe_vals) > params["roe_threshold"]
            )

        px = get_price_history(stock_id, params["backtest_years"])
        if len(px) < 100:
            result["action"] = "SKIP"
            result["risk_notes"].append("價格資料不足")
            return result

        px = add_indicators(px)
        latest = px.iloc[-1]
        ts = tech_score_at(latest, params)

        index_df = pd.DataFrame()
        if params.get("use_vcp_breakout", True) or params.get("vcp_only_signal"):
            try:
                index_df = get_index_history("TAIEX")
            except Exception:
                index_df = pd.DataFrame()

        bt_params = dict(params)
        if not index_df.empty:
            bt_params["_index_df"] = index_df
        bt = backtest(px, bt_params)

        if params["use_volume_patterns"]:
            vp = detect_patterns(px)
        else:
            vp = {"patterns": [], "bonus": 0, "details": {}}

        if (not is_etf) and params.get("use_vcp_breakout", True):
            vcp_info = detect_vcp(px, index_df=index_df if not index_df.empty else None)
            if vcp_info.get("signal"):
                ts["signals"] = list(ts["signals"]) + [vcp_info["signal"]]
        else:
            vcp_info = {"signal": None, "bonus": 0}

        # 1. 基本面評分 (結合財報 EPS/ROE 與 證交所/櫃買 官方即時 PE/殖利率快照；ETF 採中性評分免扣財報)
        val_info = {}
        try:
            val_map = get_market_valuation_map()
            val_info = val_map.get(sid_clean, {})
        except Exception:
            pass

        pe_val = val_info.get("pe")
        yield_val = val_info.get("yield", 0.0)
        pb_val = val_info.get("pb", 0.0)

        if is_etf:
            fund_score = 60.0
            if yield_val >= 5.0:
                fund_score += 15.0
            elif yield_val >= 3.5:
                fund_score += 8.0
        else:
            min_eps = min(eps_vals) if eps_vals else 0.0
            min_roe = min(roe_vals) if roe_vals else 0.0
            eps_score = min(50.0, max(0.0, (min_eps / 4.0) * 50.0))
            roe_score = min(50.0, max(0.0, ((min_roe - 5.0) / 20.0) * 50.0))
            fund_score = min(100.0, max(20.0, eps_score + roe_score))

            if yield_val >= 5.0:
                fund_score += 10.0
            elif yield_val >= 3.5:
                fund_score += 5.0

            if pe_val is not None:
                if 0 < pe_val <= 16.0:
                    fund_score += 5.0
                elif pe_val > 50.0:
                    fund_score -= 10.0
            else:
                fund_score -= 5.0

        fund_score = round(min(100.0, max(0.0, fund_score)), 1)

        # 2. 技術面評分 (含 VCP 加分；若為純 VCP 策略則嚴格門控 BUY)
        vcp_sig = vcp_info.get("signal")
        if params.get("vcp_only_signal"):
            if vcp_sig == "VCP突破":
                tech_score = max(85, min(100, ts["score"] + vp["bonus"] + vcp_info.get("bonus", 0)))
            elif vcp_sig == "VCP蓄勢":
                tech_score = min(max(0, params["min_tech_score_for_buy"] - 1), max(65, ts["score"] + vp["bonus"]))
            else:
                tech_score = min(max(0, params["min_tech_score_for_buy"] - 10), max(0, ts["score"] + vp["bonus"]))
        else:
            tech_score = max(0, min(100, ts["score"] + vp["bonus"] + vcp_info.get("bonus", 0)))

        # 3. 回測評分（貝氏平滑：以 10 筆 50% 基準平滑極端值）
        samples = bt.get("samples", 0)
        raw_winrate = bt.get("winrate") or 0.5
        if samples > 0:
            shrunk_winrate = (raw_winrate * samples + 0.5 * 10) / (samples + 10)
        else:
            shrunk_winrate = 0.5
        bt_score = round(shrunk_winrate * 100, 1)

        # 4. 籌碼面評分 (0 ~ 100，基準 50 分中性；優先讀取 TWSE/TPEx 免費資料，無資料再退回 FinMind)
        chips_score = 50.0
        chips_summary = ""
        try:
            inst_df = pd.DataFrame()
            try:
                inst_map = get_market_recent_inst_map(trading_days_count=5)
                if sid_clean in inst_map and len(inst_map[sid_clean]) >= 3:
                    inst_df = pd.DataFrame(inst_map[sid_clean])
            except Exception:
                inst_df = pd.DataFrame()

            if inst_df.empty or len(inst_df) < 3:
                start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
                inst_df = get_institutional(stock_id, start=start_date)

            if not inst_df.empty and len(inst_df) >= 3:
                inst_slice = inst_df.tail(5)
                tot_5d = inst_slice["total_net"].sum()
                t_streak = 0
                for v in reversed(inst_df["trust_net"].tolist()):
                    if v > 0:
                        t_streak += 1
                    else:
                        break
                f_streak = 0
                for v in reversed(inst_df["foreign_net"].tolist()):
                    if v > 0:
                        f_streak += 1
                    else:
                        break

                delta = 0.0
                if t_streak >= 3 and f_streak >= 3:
                    delta += 25.0
                    chips_summary = f"土洋同買(投信連{t_streak}/外資連{f_streak})"
                elif t_streak >= 3:
                    delta += 20.0
                    chips_summary = f"投信認養(連{t_streak}天)"
                elif f_streak >= 3:
                    delta += 15.0
                    chips_summary = f"外資買進(連{f_streak}天)"
                elif tot_5d > 0:
                    delta += 10.0
                    chips_summary = "5日法人淨買超"
                elif t_streak == 0 and f_streak == 0 and tot_5d < 0:
                    delta -= 15.0
                    chips_summary = "5日法人調節"

                chips_score = min(100.0, max(0.0, 50.0 + delta))
        except Exception:
            pass

        # 四維權重模型
        wc = params.get("weight_chips", 0.25)
        wt = params.get("weight_technical", 0.35)
        wf = params.get("weight_fundamental", 0.25)
        wb = params.get("weight_backtest", 0.15)
        wsum = wc + wt + wf + wb
        if wsum > 0:
            wc, wt, wf, wb = wc / wsum, wt / wsum, wf / wsum, wb / wsum

        signal_score = round(
            wc * chips_score + wt * tech_score + wf * fund_score + wb * bt_score, 1
        )

        fund_gate = (not params["fundamental_pass_required"]) or fund_pass
        if (
            signal_score >= params["min_total_score_for_buy"]
            and fund_gate
            and tech_score >= params["min_tech_score_for_buy"]
        ):
            action = "BUY"
        elif signal_score >= 50:
            action = "WATCH"
        else:
            action = "SKIP"

        entry = float(latest["close"])
        stop_price = round(entry * (1 - params["stop_loss"]), 2)
        target_price = round(entry * (1 + params["target_return"]), 2)
        rr = round(params["target_return"] / params["stop_loss"], 2)
        position_pct = min(2.0 / (params["stop_loss"] * 100) * 100, 20.0)
        entry_rule = (
            f"明日以開盤價進場，停損 -{params['stop_loss']*100:.0f}% / "
            f"停利 +{params['target_return']*100:.0f}%（下方參考價為今日收盤）"
        )

        if bt.get("samples", 0) < 8:
            result["risk_notes"].append(f"回測樣本僅 {bt.get('samples', 0)} 次，統計弱")
        if (not is_etf) and (not fund_pass):
            result["risk_notes"].append("基本面未過門檻")
        if raw_winrate < 0.5:
            result["risk_notes"].append(f"歷史勝率 {raw_winrate*100:.0f}% 低於五成")
        if pd.notna(latest.get("bb_upper")) and latest["close"] > latest["bb_upper"]:
            result["risk_notes"].append("已突破布林上軌，追高風險")
        if "放量滯漲" in vp["patterns"]:
            result["risk_notes"].append("偵測到放量滯漲，高檔爆量疑似出貨")

        chg_5d = (latest["close"] / px.iloc[-6]["close"] - 1) * 100 if len(px) >= 6 else 0
        chg_20d = (latest["close"] / px.iloc[-21]["close"] - 1) * 100 if len(px) >= 21 else 0
        vol_5 = px["volume"].iloc[-5:].mean()
        vol_20 = px["volume"].iloc[-20:].mean()
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1
        high_252 = px["high"].iloc[-252:].max() if len(px) >= 252 else px["high"].max()
        low_252 = px["low"].iloc[-252:].min() if len(px) >= 252 else px["low"].min()
        pct_from_high = (latest["close"] / high_252 - 1) * 100
        above_ma20 = latest["close"] > latest["ma20"] if pd.notna(latest["ma20"]) else False
        above_ma60 = latest["close"] > latest["ma60"] if pd.notna(latest["ma60"]) else False

        result.update({
            "action": action,
            "signal_score": signal_score,
            "components": {
                "is_etf": is_etf,
                "fundamental_pass": fund_pass,
                "fundamental_score": fund_score,
                "eps_min": min(eps_vals) if eps_vals else None,
                "roe_min": min(roe_vals) if roe_vals else None,
                "tech_score": tech_score,
                "tech_signals": ts["signals"],
                "chips_score": chips_score,
                "chips_summary": chips_summary,
                "backtest_winrate": round(shrunk_winrate, 3),
                "backtest_samples": samples,
                "volume_patterns": vp["patterns"],
                "volume_details": vp["details"],
                "volume_bonus": vp["bonus"],
                "volume_verdict": volume_verdict(vp["patterns"]),
                "vcp": vcp_info,
                "valuation": {
                    "pe": pe_val,
                    "yield": yield_val,
                    "pb": pb_val,
                },
            },
            "trend": {
                "chg_5d": round(chg_5d, 2),
                "chg_20d": round(chg_20d, 2),
                "vol_ratio": round(vol_ratio, 2),
                "pct_from_high": round(pct_from_high, 1),
                "above_ma20": bool(above_ma20),
                "above_ma60": bool(above_ma60),
            },
            "entry_price": entry,
            "stop_loss_price": stop_price,
            "target_price": target_price,
            "risk_reward_ratio": rr,
            "position_size_pct": round(position_pct, 1),
            "entry_rule": entry_rule,
        })
        return result

    except Exception as e:
        result["action"] = "ERROR"
        result["risk_notes"].append(f"錯誤: {str(e)[:80]}")
        return result
