#!/usr/bin/env python3
"""分析60天策略亏损原因"""
import sys
sys.path.insert(0, '/root/quant_trading_v2')

import pandas as pd
from backtest import BacktestEngine, fetch_okx_candles
from indicators import compute_all_indicators
from config import strategy_cfg

print("拉取60天数据...")
df_full = fetch_okx_candles("BTC-USDT", "15m", 5760)

print("=" * 80)
print("  BTC 60天价格走势 (每10天)")
print("=" * 80)
for i in range(0, len(df_full), 960):
    end = min(i + 960, len(df_full))
    seg = df_full.iloc[i:end]
    s_price = seg["close"].iloc[0]
    e_price = seg["close"].iloc[-1]
    pct = (e_price - s_price) / s_price * 100
    date_s = seg["ts"].iloc[0].strftime("%m/%d")
    date_e = seg["ts"].iloc[-1].strftime("%m/%d")
    print(f"  {date_s} ~ {date_e}: ${s_price:.0f} -> ${e_price:.0f}  ({pct:+.1f}%)")

pct_total = (df_full["close"].iloc[-1] - df_full["close"].iloc[0]) / df_full["close"].iloc[0] * 100
print(f"  ----")
print(f"  BTC 60天总涨跌: {pct_total:+.1f}%")

# 运行60天回测
print(f"\n{'=' * 80}")
print(f"  60天基准回测统计")
print(f"{'=' * 80}")

engine = BacktestEngine(initial_capital=100)
metrics = engine.run(df_full)

# 按模式统计
for mode in ["spot", "margin_2x", "margin_3x"]:
    mt = [t for t in engine.trades if t.mode == mode]
    if not mt:
        continue
    wins = [t for t in mt if t.pnl_after_fee > 0]
    losses = [t for t in mt if t.pnl_after_fee <= 0]
    avg_w = sum(t.pnl_after_fee for t in wins) / len(wins) if wins else 0
    avg_l = sum(t.pnl_after_fee for t in losses) / len(losses) if losses else 0
    pnl = sum(t.pnl_after_fee for t in mt)
    fee = sum(t.total_fee for t in mt)
    sl = len([t for t in mt if t.exit_reason == "止损"])
    tp = len([t for t in mt if t.exit_reason == "止盈"])

    # 做多做空分开统计
    longs = [t for t in mt if t.side == "buy"]
    shorts = [t for t in mt if t.side == "sell"]
    long_win = len([t for t in longs if t.pnl_after_fee > 0])
    short_win = len([t for t in shorts if t.pnl_after_fee > 0])
    long_pnl = sum(t.pnl_after_fee for t in longs)
    short_pnl = sum(t.pnl_after_fee for t in shorts)

    print(f"\n  [{mode}]")
    print(f"    交易: {len(mt)} 笔, 胜率 {len(wins)}/{len(mt)} ({len(wins)/len(mt)*100:.0f}%)")
    print(f"    净利: ${pnl:+.2f}, 手续费: ${fee:.2f}")
    print(f"    平均赢: ${avg_w:+.3f}, 平均亏: ${avg_l:+.3f}, 盈亏比: {abs(avg_w/avg_l) if avg_l else 0:.2f}")
    print(f"    止损: {sl}, 止盈: {tp}")
    print(f"    做多: {len(longs)}笔 胜{long_win} 净利${long_pnl:+.2f}")
    print(f"    做空: {len(shorts)}笔 胜{short_win} 净利${short_pnl:+.2f}")

# 整体统计
total_fee = sum(t.total_fee for t in engine.trades)
total_pnl_before = sum(t.pnl for t in engine.trades)
total_pnl_after = sum(t.pnl_after_fee for t in engine.trades)
all_wins = [t for t in engine.trades if t.pnl_after_fee > 0]
all_losses = [t for t in engine.trades if t.pnl_after_fee <= 0]
avg_w = sum(t.pnl_after_fee for t in all_wins) / len(all_wins) if all_wins else 0
avg_l = sum(t.pnl_after_fee for t in all_losses) / len(all_losses) if all_losses else 0

print(f"\n{'=' * 80}")
print(f"  整体统计")
print(f"{'=' * 80}")
print(f"  总交易: {len(engine.trades)} 笔")
print(f"  胜率: {len(all_wins)}/{len(engine.trades)} ({len(all_wins)/len(engine.trades)*100:.1f}%)")
print(f"  平均赢: ${avg_w:+.3f}, 平均亏: ${avg_l:+.3f}")
print(f"  盈亏比: {abs(avg_w/avg_l) if avg_l else 0:.2f}")
print(f"  扣费前PnL: ${total_pnl_before:+.2f}")
print(f"  总手续费: ${total_fee:.2f}")
print(f"  扣费后PnL: ${total_pnl_after:+.2f}")

# 趋势分析
df_full = compute_all_indicators(df_full)
long_below_trend = 0
long_total = 0
short_above_trend = 0
short_total = 0
cfg = strategy_cfg

for i in range(100, len(df_full)):
    row = df_full.iloc[i]
    prev = df_full.iloc[i-1]

    ema_score = 0
    if not pd.isna(row.get("ema_fast")) and not pd.isna(row.get("ema_slow")):
        fast, slow = row["ema_fast"], row["ema_slow"]
        pfst, pslw = prev["ema_fast"], prev["ema_slow"]
        if fast > slow and pfst <= pslw: ema_score = 1.0
        elif fast < slow and pfst >= pslw: ema_score = -1.0
        elif fast > slow: ema_score = min((fast - slow) / slow * 200, 0.8)
        elif fast < slow: ema_score = max(-(slow - fast) / slow * 200, -0.8)

    rsi = row.get("rsi"); prsi = prev.get("rsi")
    rsi_score = 0
    if not pd.isna(rsi) and not pd.isna(prsi):
        if rsi < 30 and rsi > prsi: rsi_score = 1.0
        elif rsi > 70 and rsi < prsi: rsi_score = -1.0
        elif rsi < 45 and rsi > prsi: rsi_score = 0.4
        elif rsi > 55 and rsi < prsi: rsi_score = -0.4

    bb_pct = row.get("bb_pct")
    bb_score = 0
    if not pd.isna(bb_pct):
        if bb_pct < 0.15: bb_score = 1.0
        elif bb_pct < 0.30: bb_score = 0.6
        elif bb_pct > 0.85: bb_score = -1.0
        elif bb_pct > 0.70: bb_score = -0.6

    vr = row.get("vol_ratio")
    vol_score = 0
    if not pd.isna(vr):
        if vr > 1.5: vol_score = 1.0
        elif vr > 1.0: vol_score = 0.3
        else: vol_score = -0.2

    dir_score = ema_score * 0.35 + rsi_score * 0.25 + bb_score * 0.25
    normalized = dir_score / 0.85
    final = normalized * (1 + vol_score * 0.15)

    if final >= 0.3:
        long_total += 1
        if not pd.isna(row.get("ema_trend")) and row["close"] < row["ema_trend"]:
            long_below_trend += 1
    elif final <= -0.3:
        short_total += 1
        if not pd.isna(row.get("ema_trend")) and row["close"] > row["ema_trend"]:
            short_above_trend += 1

print(f"\n{'=' * 80}")
print(f"  信号质量诊断")
print(f"{'=' * 80}")
print(f"  做多信号: {long_total} 个")
print(f"    其中逆势(价格<EMA50): {long_below_trend} ({long_below_trend/long_total*100:.0f}%)")
print(f"  做空信号: {short_total} 个")
print(f"    其中逆势(价格>EMA50): {short_above_trend} ({short_above_trend/short_total*100:.0f}%)" if short_total else "    无")

# 连续亏损分析
print(f"\n{'=' * 80}")
print(f"  连续亏损分析")
print(f"{'=' * 80}")
max_streak = 0
cur_streak = 0
streaks = []
for t in engine.trades:
    if t.pnl_after_fee <= 0:
        cur_streak += 1
    else:
        if cur_streak > 0:
            streaks.append(cur_streak)
        cur_streak = 0
if cur_streak > 0:
    streaks.append(cur_streak)

print(f"  最长连续亏损: {max(streaks) if streaks else 0} 笔")
print(f"  连亏>=3次: {len([s for s in streaks if s >= 3])} 次")
print(f"  连亏>=5次: {len([s for s in streaks if s >= 5])} 次")
