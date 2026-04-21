#!/usr/bin/env python3
"""Test P3: improve short signals - higher threshold or disable shorts"""
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

# 保存原始值
orig_short_thresh = strategy_cfg.margin_3x_short_threshold
orig_signal_thresh = strategy_cfg.signal_threshold

# 测试方案:
# A: 当前 (做空阈值0.5, 仅3x有额外过滤)
# B: 提高所有做空阈值到0.5
# C: 提高所有做空阈值到0.6
# D: 完全禁用做空
# E: 只允许2x做空(禁3x做空)

# 为了测试B/C/D/E, 需要临时修改backtest逻辑
# 用monkey-patch方式

from backtest import BacktestEngine as BE
from strategy import Signal

original_run = BE.run

def make_patched_run(short_mode):
    """Create a patched run method with different short filtering"""
    def patched_run(self, df):
        # Temporarily patch _quick_signal to filter shorts
        original_quick = self._quick_signal

        if short_mode == "no_short":
            # 完全禁用做空
            def filtered_signal(row, prev, price, atr):
                sig, score = original_quick(row, prev, price, atr)
                if sig == Signal.SHORT:
                    return Signal.HOLD, score
                return sig, score
            self._quick_signal = filtered_signal

        elif short_mode == "no_3x_short":
            # 3x禁止做空 (在开仓逻辑里处理，这里不改信号)
            pass

        elif short_mode.startswith("thresh_"):
            # 提高做空阈值
            thresh = float(short_mode.split("_")[1])
            def filtered_signal(row, prev, price, atr):
                sig, score = original_quick(row, prev, price, atr)
                if sig == Signal.SHORT and abs(score) < thresh:
                    return Signal.HOLD, score
                return sig, score
            self._quick_signal = filtered_signal

        result = original_run(self, df)
        self._quick_signal = original_quick
        return result

    return patched_run


experiments = [
    ("A: 当前策略", None),
    ("B: 做空阈值0.5", "thresh_0.5"),
    ("C: 做空阈值0.6", "thresh_0.6"),
    ("D: 做空阈值0.7", "thresh_0.7"),
    ("E: 完全禁用做空", "no_short"),
]

results = []

for exp_name, short_mode in experiments:
    if short_mode:
        BE.run = make_patched_run(short_mode)
    else:
        BE.run = original_run

    for period_name, data in [("60天", df), ("20天", df20)]:
        engine = BacktestEngine(initial_capital=100)
        m = engine.run(data.copy())

        longs = [t for t in engine.trades if t.side == "buy"]
        shorts = [t for t in engine.trades if t.side == "sell"]
        long_pnl = sum(t.pnl_after_fee for t in longs)
        short_pnl = sum(t.pnl_after_fee for t in shorts)
        long_wins = len([t for t in longs if t.pnl_after_fee > 0])
        short_wins = len([t for t in shorts if t.pnl_after_fee > 0])

        results.append({
            "name": exp_name,
            "period": period_name,
            "trades": m["total_trades"],
            "win_rate": m["win_rate"],
            "pnl": m["total_pnl_net"],
            "return_pct": m["total_return_pct"],
            "max_dd": m["max_drawdown_pct"],
            "pf": m["profit_factor"],
            "fees": m["total_fees"],
            "longs": len(longs),
            "long_pnl": long_pnl,
            "long_wr": round(long_wins / len(longs) * 100, 1) if longs else 0,
            "shorts": len(shorts),
            "short_pnl": short_pnl,
            "short_wr": round(short_wins / len(shorts) * 100, 1) if shorts else 0,
        })

# 恢复
BE.run = original_run

# 打印
for period in ["60天", "20天"]:
    subset = [r for r in results if r["period"] == period]
    print(f"\n{'=' * 130}")
    print(f"  {period}回测 - 做空策略对比")
    print(f"{'=' * 130}")
    print(f"  {'方案':>16} | {'笔数':>4} | {'胜率':>5} | {'净利':>7} | {'收益率':>6} | {'回撤':>5} | {'盈亏比':>5} | {'多头':>4} {'多胜率':>5} {'多PnL':>7} | {'空头':>4} {'空胜率':>5} {'空PnL':>7}")
    print(f"  {'-' * 125}")
    for r in subset:
        print(f"  {r['name']:>16} | {r['trades']:>3}笔 | {r['win_rate']:>4.1f}% | ${r['pnl']:>+6.2f} | {r['return_pct']:>+5.2f}% | {r['max_dd']:>4.2f}% | {r['pf']:>5} | {r['longs']:>3}笔 {r['long_wr']:>4.1f}% ${r['long_pnl']:>+6.2f} | {r['shorts']:>3}笔 {r['short_wr']:>4.1f}% ${r['short_pnl']:>+6.2f}")

# 综合评分
print(f"\n{'=' * 130}")
print(f"  综合评分 (60天×0.7 + 20天×0.3):")
names = list(dict.fromkeys(r["name"] for r in results))
scores = []
for n in names:
    r60 = next(r for r in results if r["name"] == n and r["period"] == "60天")
    r20 = next(r for r in results if r["name"] == n and r["period"] == "20天")
    score = (r60["pnl"] * 0.7 + r20["pnl"] * 0.3) - (r60["max_dd"] * 0.1 + r20["max_dd"] * 0.05)
    scores.append((n, score, r60["pnl"], r20["pnl"], r60["max_dd"], r20["max_dd"]))

scores.sort(key=lambda x: x[1], reverse=True)
for name, score, p60, p20, dd60, dd20 in scores:
    print(f"    {name:>16}: 综合={score:+.2f}  (60天${p60:+.2f} 回撤{dd60:.1f}% / 20天${p20:+.2f} 回撤{dd20:.1f}%)")
