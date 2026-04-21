#!/usr/bin/env python3
"""分析策略亏损原因"""
import sys
sys.path.insert(0, '/root/quant_trading_v2')

import pandas as pd
from backtest import BacktestEngine, fetch_okx_candles
from indicators import compute_all_indicators
from config import strategy_cfg

# 拉20天数据
print("拉取数据...")
df_full = fetch_okx_candles("BTC-USDT", "15m", 1920)

print("=" * 80)
print("  BTC 20天价格走势 (每5天)")
print("=" * 80)
for i in range(0, len(df_full), 480):
    end = min(i + 480, len(df_full))
    seg = df_full.iloc[i:end]
    s_price = seg["close"].iloc[0]
    e_price = seg["close"].iloc[-1]
    pct = (e_price - s_price) / s_price * 100
    date_s = seg["ts"].iloc[0].strftime("%m/%d")
    date_e = seg["ts"].iloc[-1].strftime("%m/%d")
    bar = "+" * int(abs(pct)) if pct > 0 else "-" * int(abs(pct))
    print(f"  {date_s} ~ {date_e}: ${s_price:.0f} -> ${e_price:.0f}  ({pct:+.1f}%) {bar}")

# 运行回测，看逐笔交易
print("\n" + "=" * 80)
print("  20天基准回测 - 逐笔交易明细")
print("=" * 80)

engine = BacktestEngine(initial_capital=100)
metrics = engine.run(df_full)

buy_count = sell_count = 0
buy_win = sell_win = 0
buy_pnl = sell_pnl = 0

for t in engine.trades:
    side_cn = "多" if t.side == "buy" else "空"
    result = "WIN" if t.pnl_after_fee > 0 else "LOSS"
    print(f"  {t.mode:>10} | {side_cn} | 入${t.entry_price:.0f} -> 出${t.exit_price:.0f} | {t.exit_reason} | ${t.pnl_after_fee:+.3f} [{result}]")

    if t.side == "buy":
        buy_count += 1
        buy_pnl += t.pnl_after_fee
        if t.pnl_after_fee > 0:
            buy_win += 1
    else:
        sell_count += 1
        sell_pnl += t.pnl_after_fee
        if t.pnl_after_fee > 0:
            sell_win += 1

# 统计分析
wins = [t for t in engine.trades if t.pnl_after_fee > 0]
losses = [t for t in engine.trades if t.pnl_after_fee <= 0]
avg_win = sum(t.pnl_after_fee for t in wins) / len(wins) if wins else 0
avg_loss = sum(t.pnl_after_fee for t in losses) / len(losses) if losses else 0

print(f"\n{'=' * 80}")
print(f"  统计分析")
print(f"{'=' * 80}")
print(f"  总交易: {len(engine.trades)} 笔")
print(f"  胜率: {len(wins)}/{len(engine.trades)} = {len(wins)/len(engine.trades)*100:.1f}%")
print(f"  平均盈利: ${avg_win:+.4f}")
print(f"  平均亏损: ${avg_loss:+.4f}")
print(f"  盈亏比: {abs(avg_win/avg_loss) if avg_loss else 0:.2f}")
print(f"\n  做多: {buy_count} 笔, 胜{buy_win}笔, 净利${buy_pnl:+.3f}")
print(f"  做空: {sell_count} 笔, 胜{sell_win}笔, 净利${sell_pnl:+.3f}")

# 按止损/止盈统计
sl_count = len([t for t in engine.trades if t.exit_reason == "止损"])
tp_count = len([t for t in engine.trades if t.exit_reason == "止盈"])
end_count = len([t for t in engine.trades if t.exit_reason == "回测结束"])
print(f"\n  止损出场: {sl_count} 笔 ({sl_count/len(engine.trades)*100:.0f}%)")
print(f"  止盈出场: {tp_count} 笔 ({tp_count/len(engine.trades)*100:.0f}%)")
print(f"  回测结束: {end_count} 笔")

# 按模式分析止损/止盈
print(f"\n{'=' * 80}")
print(f"  按模式分析")
print(f"{'=' * 80}")
for mode in ["spot", "margin_2x", "margin_3x"]:
    mode_trades = [t for t in engine.trades if t.mode == mode]
    if not mode_trades:
        continue
    mode_wins = [t for t in mode_trades if t.pnl_after_fee > 0]
    mode_sl = [t for t in mode_trades if t.exit_reason == "止损"]
    mode_tp = [t for t in mode_trades if t.exit_reason == "止盈"]
    mode_pnl = sum(t.pnl_after_fee for t in mode_trades)
    mode_fee = sum(t.total_fee for t in mode_trades)

    print(f"\n  [{mode}]")
    print(f"    交易: {len(mode_trades)} 笔, 胜率 {len(mode_wins)}/{len(mode_trades)}")
    print(f"    净利: ${mode_pnl:+.3f}")
    print(f"    手续费: ${mode_fee:.3f}")
    print(f"    止损: {len(mode_sl)} 笔, 止盈: {len(mode_tp)} 笔")

# 手续费总计
total_fee = sum(t.total_fee for t in engine.trades)
total_pnl_before = sum(t.pnl for t in engine.trades)
total_pnl_after = sum(t.pnl_after_fee for t in engine.trades)
print(f"\n{'=' * 80}")
print(f"  手续费影响")
print(f"{'=' * 80}")
print(f"  总手续费: ${total_fee:.3f}")
print(f"  扣费前PnL: ${total_pnl_before:+.3f}")
print(f"  扣费后PnL: ${total_pnl_after:+.3f}")
print(f"  手续费占亏损比: {total_fee/abs(total_pnl_after)*100:.1f}%")

# 分析信号在下跌趋势中的表现
print(f"\n{'=' * 80}")
print(f"  核心问题诊断")
print(f"{'=' * 80}")

df_full = compute_all_indicators(df_full)
# 看看EMA趋势
above_ema50 = 0
below_ema50 = 0
for i in range(100, len(df_full)):
    if not pd.isna(df_full.iloc[i].get("ema_trend")):
        if df_full.iloc[i]["close"] > df_full.iloc[i]["ema_trend"]:
            above_ema50 += 1
        else:
            below_ema50 += 1

total_bars = above_ema50 + below_ema50
print(f"  价格在EMA50上方: {above_ema50} 根 ({above_ema50/total_bars*100:.0f}%)")
print(f"  价格在EMA50下方: {below_ema50} 根 ({below_ema50/total_bars*100:.0f}%)")

# 看做多信号在EMA50下方出现多少次
long_below_trend = 0
long_total = 0
from config import strategy_cfg as cfg
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

print(f"\n  做多信号总数: {long_total}")
print(f"  其中价格在EMA50下方(下跌趋势): {long_below_trend} ({long_below_trend/long_total*100:.0f}%)")
print(f"  -> 这些信号很可能是「逆势抄底」, 成功率很低!")
