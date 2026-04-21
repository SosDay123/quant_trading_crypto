#!/usr/bin/env python3
"""Test different signal thresholds with real OKX data"""
import sys
import os
sys.path.insert(0, '/root/quant_trading_v2')

# Patch frozen dataclass before importing
import config
object.__setattr__(config.strategy_cfg, 'signal_threshold', float(sys.argv[1]))

from backtest import BacktestEngine, fetch_okx_candles, print_report

bars = int(sys.argv[2])
threshold = float(sys.argv[1])

print(f"\n{'='*60}")
print(f"  信号阈值: {threshold}  |  K线数: {bars}")
print(f"{'='*60}")

df = fetch_okx_candles(inst_id="BTC-USDT", bar="15m", total_bars=bars)

engine = BacktestEngine(initial_capital=100)
# Also patch the engine's strategy threshold
object.__setattr__(engine.strategy.cfg, 'signal_threshold', threshold)

metrics = engine.run(df)
print_report(metrics, engine.trades)
