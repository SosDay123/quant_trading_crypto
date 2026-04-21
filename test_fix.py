#!/usr/bin/env python3
"""Test P0 fix: compare 60-day backtest before/after"""
import sys
sys.path.insert(0, '/root/quant_trading_v2')

import pandas as pd
from backtest import BacktestEngine, fetch_okx_candles

# 拉60天数据
print("拉取60天数据...")
df = fetch_okx_candles("BTC-USDT", "15m", 5760)

pct = (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100
print(f"BTC 60天: ${df['close'].iloc[0]:.0f} -> ${df['close'].iloc[-1]:.0f} ({pct:+.1f}%)")
print(f"时间: {df['ts'].iloc[0].strftime('%m/%d')} ~ {df['ts'].iloc[-1].strftime('%m/%d')}")

# 跑回测
print(f"\n{'=' * 80}")
print(f"  60天回测 (修复后: 日亏损限额按天重置)")
print(f"{'=' * 80}")

engine = BacktestEngine(initial_capital=100)
metrics = engine.run(df)

# 整体结果
print(f"\n  总交易: {metrics['total_trades']} 笔")
print(f"  胜率: {metrics['win_rate']}%")
print(f"  净利: ${metrics['total_pnl_net']:+.2f} ({metrics['total_return_pct']:+.2f}%)")
print(f"  最大回撤: {metrics['max_drawdown_pct']}%")
print(f"  盈亏比: {metrics['profit_factor']}")
print(f"  手续费: ${metrics['total_fees']:.2f}")

# 按模式
print(f"\n  按模式:")
for mode in ["spot", "margin_2x", "margin_3x"]:
    mt = [t for t in engine.trades if t.mode == mode]
    if not mt:
        continue
    wins = len([t for t in mt if t.pnl_after_fee > 0])
    pnl = sum(t.pnl_after_fee for t in mt)
    fee = sum(t.total_fee for t in mt)
    longs = [t for t in mt if t.side == "buy"]
    shorts = [t for t in mt if t.side == "sell"]
    long_pnl = sum(t.pnl_after_fee for t in longs)
    short_pnl = sum(t.pnl_after_fee for t in shorts)
    print(f"    {mode:>10}: {len(mt)}笔 胜率{wins}/{len(mt)} 净利${pnl:+.2f} (手续费${fee:.2f})")
    print(f"               做多{len(longs)}笔${long_pnl:+.2f} | 做空{len(shorts)}笔${short_pnl:+.2f}")

# 做多做空
print(f"\n  做多: {metrics['long_trades']}笔 净利${metrics['long_pnl']:+.2f}")
print(f"  做空: {metrics['short_trades']}笔 净利${metrics['short_pnl']:+.2f}")

# 对比: 修复前是 8笔 0%胜率 -$7.37
print(f"\n{'=' * 80}")
print(f"  对比修复前")
print(f"{'=' * 80}")
print(f"  修复前: 8笔, 0%胜率, -$7.37, 回撤7.37%")
print(f"  修复后: {metrics['total_trades']}笔, {metrics['win_rate']}%胜率, ${metrics['total_pnl_net']:+.2f}, 回撤{metrics['max_drawdown_pct']}%")

# 也跑一下20天
df20 = df.tail(1920).reset_index(drop=True)
engine20 = BacktestEngine(initial_capital=100)
m20 = engine20.run(df20)
print(f"\n  20天回测: {m20['total_trades']}笔, {m20['win_rate']}%胜率, ${m20['total_pnl_net']:+.2f}, 回撤{m20['max_drawdown_pct']}%")
