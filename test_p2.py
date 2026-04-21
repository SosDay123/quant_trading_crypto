#!/usr/bin/env python3
"""Test P2: compare different EMA parameters"""
import sys
sys.path.insert(0, '/root/quant_trading_v2')

import pandas as pd
from backtest import BacktestEngine, fetch_okx_candles
from config import strategy_cfg

print("拉取60天数据...")
df = fetch_okx_candles("BTC-USDT", "15m", 5760)
df20 = df.tail(1920).reset_index(drop=True)

pct = (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100
print(f"BTC 60天: ${df['close'].iloc[0]:.0f} -> ${df['close'].iloc[-1]:.0f} ({pct:+.1f}%)")

# 测试不同EMA参数组合
ema_combos = [
    (9, 21, 50, "当前 EMA(9,21,50)"),
    (12, 26, 50, "EMA(12,26,50)"),
    (15, 35, 50, "EMA(15,35,50)"),
    (21, 55, 100, "EMA(21,55,100)"),
    (9, 21, 100, "EMA(9,21,100)"),  # 只改趋势线
    (15, 35, 100, "EMA(15,35,100)"),
]

results = []

for fast, slow, trend, name in ema_combos:
    # 修改策略参数
    object.__setattr__(strategy_cfg, 'ema_fast', fast)
    object.__setattr__(strategy_cfg, 'ema_slow', slow)
    object.__setattr__(strategy_cfg, 'ema_trend', trend)

    for period_name, data in [("60天", df), ("20天", df20)]:
        engine = BacktestEngine(initial_capital=100)
        m = engine.run(data.copy())

        spot_t = [t for t in engine.trades if t.mode == "spot"]
        m2_t = [t for t in engine.trades if t.mode == "margin_2x"]
        m3_t = [t for t in engine.trades if t.mode == "margin_3x"]

        results.append({
            "name": name,
            "period": period_name,
            "trades": m["total_trades"],
            "win_rate": m["win_rate"],
            "pnl": m["total_pnl_net"],
            "return_pct": m["total_return_pct"],
            "max_dd": m["max_drawdown_pct"],
            "pf": m["profit_factor"],
            "fees": m["total_fees"],
            "spot": sum(t.pnl_after_fee for t in spot_t),
            "m2": sum(t.pnl_after_fee for t in m2_t),
            "m3": sum(t.pnl_after_fee for t in m3_t),
        })

# 恢复原始参数
object.__setattr__(strategy_cfg, 'ema_fast', 9)
object.__setattr__(strategy_cfg, 'ema_slow', 21)
object.__setattr__(strategy_cfg, 'ema_trend', 50)

# 打印结果
for period in ["60天", "20天"]:
    subset = [r for r in results if r["period"] == period]
    print(f"\n{'=' * 120}")
    print(f"  {period}回测对比")
    print(f"{'=' * 120}")
    print(f"  {'参数':>20} | {'笔数':>4} | {'胜率':>5} | {'净利':>7} | {'收益率':>6} | {'回撤':>5} | {'盈亏比':>5} | {'手续费':>5} | {'现货':>6} | {'2x':>6} | {'3x':>6}")
    print(f"  {'-' * 115}")
    for r in subset:
        print(f"  {r['name']:>20} | {r['trades']:>3}笔 | {r['win_rate']:>4.1f}% | ${r['pnl']:>+6.2f} | {r['return_pct']:>+5.2f}% | {r['max_dd']:>4.2f}% | {r['pf']:>5} | ${r['fees']:>4.2f} | ${r['spot']:>+5.2f} | ${r['m2']:>+5.2f} | ${r['m3']:>+5.2f}")

# 找最优
print(f"\n{'=' * 120}")
print(f"  最优参数")
print(f"{'=' * 120}")
r60 = [r for r in results if r["period"] == "60天"]
best60 = max(r60, key=lambda x: x["pnl"])
print(f"  60天最优: {best60['name']} -> 净利${best60['pnl']:+.2f} 胜率{best60['win_rate']}% 回撤{best60['max_dd']}%")

r20 = [r for r in results if r["period"] == "20天"]
best20 = max(r20, key=lambda x: x["pnl"])
print(f"  20天最优: {best20['name']} -> 净利${best20['pnl']:+.2f} 胜率{best20['win_rate']}% 回撤{best20['max_dd']}%")

# 综合评分 (60天权重70%, 20天30%)
print(f"\n  综合评分 (60天×0.7 + 20天×0.3):")
names = list(set(r["name"] for r in results))
scores = []
for n in names:
    r60_item = next(r for r in results if r["name"] == n and r["period"] == "60天")
    r20_item = next(r for r in results if r["name"] == n and r["period"] == "20天")
    # 综合考虑收益和回撤
    score = (r60_item["pnl"] * 0.7 + r20_item["pnl"] * 0.3) - (r60_item["max_dd"] * 0.1 + r20_item["max_dd"] * 0.05)
    scores.append((n, score, r60_item["pnl"], r20_item["pnl"]))

scores.sort(key=lambda x: x[1], reverse=True)
for name, score, p60, p20 in scores:
    print(f"    {name:>20}: 综合={score:+.2f}  (60天${p60:+.2f} / 20天${p20:+.2f})")
