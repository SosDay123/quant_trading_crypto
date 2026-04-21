#!/usr/bin/env python3
"""Batch test different signal thresholds with cached data"""
import sys
sys.path.insert(0, '/root/quant_trading_v2')

import pandas as pd
import config
from backtest import BacktestEngine, print_report
from config import strategy_cfg

# Load cached data
df_full = pd.read_csv('/root/cache_4800.csv')
df_full['ts'] = pd.to_datetime(df_full['ts'])

# 30 days = 2880 bars, 50 days = 4800 bars
periods = {"30天(2880根)": 2880, "50天(4800根)": 4800}
thresholds = [0.40, 0.35, 0.30, 0.25]

results = []

for period_name, bars in periods.items():
    df = df_full.tail(bars).reset_index(drop=True)
    date_range = f"{df['ts'].iloc[0].strftime('%m/%d')} - {df['ts'].iloc[-1].strftime('%m/%d')}"

    for th in thresholds:
        # Patch threshold
        object.__setattr__(strategy_cfg, 'signal_threshold', th)

        engine = BacktestEngine(initial_capital=100)
        object.__setattr__(engine.strategy.cfg, 'signal_threshold', th)

        metrics = engine.run(df)

        results.append({
            "period": period_name,
            "date_range": date_range,
            "threshold": th,
            "final_equity": metrics["final_equity"],
            "return_pct": metrics.get("total_return_pct", 0),
            "trades": metrics["total_trades"],
            "win_rate": metrics.get("win_rate", 0),
            "max_dd": metrics.get("max_drawdown_pct", 0),
            "profit_factor": metrics.get("profit_factor", 0),
            "fees": metrics.get("total_fees", 0),
        })

        print(f"[阈值={th}] {period_name} | 净利={metrics.get('total_return_pct',0):+.2f}% | "
              f"交易={metrics['total_trades']}笔 | 胜率={metrics.get('win_rate',0):.1f}% | "
              f"回撤={metrics.get('max_drawdown_pct',0):.2f}% | 盈亏比={metrics.get('profit_factor',0)}")

    print()

# Summary table
print("\n" + "=" * 80)
print("                    阈值对比汇总表")
print("=" * 80)
print(f"{'阈值':>6} | {'周期':>12} | {'净利润%':>8} | {'交易数':>6} | {'胜率':>6} | {'回撤':>6} | {'盈亏比':>6} | {'手续费':>6}")
print("-" * 80)
for r in results:
    print(f"  {r['threshold']:.2f} | {r['period']:>12} | {r['return_pct']:>+7.2f}% | {r['trades']:>5}笔 | {r['win_rate']:>5.1f}% | {r['max_dd']:>5.2f}% | {r['profit_factor']:>6} | ${r['fees']:>5.2f}")

# Restore
object.__setattr__(strategy_cfg, 'signal_threshold', 0.4)
