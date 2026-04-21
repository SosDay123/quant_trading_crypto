#!/usr/bin/env python3
"""Run 6 experiments: 3 strategies x 2 periods (20d, 60d)"""
import sys, copy
sys.path.insert(0, '/root/quant_trading_v2')

import pandas as pd
import config
from backtest import BacktestEngine, fetch_okx_candles
from config import strategy_cfg, risk_cfg
from strategy import Signal

# ── Fetch data ──
print("=" * 70)
print("  拉取 60 天真实 K 线数据...")
print("=" * 70)
df_full = fetch_okx_candles(inst_id="BTC-USDT", bar="15m", total_bars=5760)

# ── Custom engine that supports per-mode signal threshold ──
class ExperimentEngine(BacktestEngine):
    def __init__(self, initial_capital=100, m3_min_strength=None):
        super().__init__(initial_capital)
        self.m3_min_strength = m3_min_strength  # extra threshold for 3x

    def run(self, df):
        """Override run to filter 3x trades by signal strength"""
        if len(df) < 50:
            raise ValueError("数据不足")
        from indicators import compute_all_indicators
        df = compute_all_indicators(df)

        equity = self.initial_capital
        peak_equity = equity
        open_positions = {}
        consecutive_losses = 0
        cooldown_until_bar = -1
        window = strategy_cfg.lookback_candles

        for i in range(window, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i - 1]
            ts = str(row.get("ts", i))
            price = row["close"]
            high = row["high"]
            low = row["low"]
            atr = row.get("atr", 0)
            if pd.isna(atr):
                atr = price * 0.02

            # 1. Check stop loss / take profit
            modes_to_close = []
            for mode, trade in open_positions.items():
                hit_sl = hit_tp = False
                exit_price = 0
                exit_reason = ""

                if trade.side == "buy":
                    if low <= trade.stop_loss:
                        hit_sl, exit_price, exit_reason = True, trade.stop_loss, "止损"
                    elif high >= trade.take_profit:
                        hit_tp, exit_price, exit_reason = True, trade.take_profit, "止盈"
                else:
                    if high >= trade.stop_loss:
                        hit_sl, exit_price, exit_reason = True, trade.stop_loss, "止损"
                    elif low <= trade.take_profit:
                        hit_tp, exit_price, exit_reason = True, trade.take_profit, "止盈"

                if hit_sl or hit_tp:
                    from config import fee_cfg
                    leverage = self.modes[mode]["leverage"]
                    if trade.side == "buy":
                        pnl = (exit_price - trade.entry_price) * trade.size * leverage
                    else:
                        pnl = (trade.entry_price - exit_price) * trade.size * leverage

                    entry_notional = trade.size * trade.entry_price
                    exit_notional = trade.size * exit_price
                    entry_fee = entry_notional * fee_cfg.default_fee if fee_cfg.enabled else 0
                    exit_fee = exit_notional * fee_cfg.default_fee if fee_cfg.enabled else 0
                    total_fee = entry_fee + exit_fee
                    pnl_after_fee = pnl - total_fee

                    trade.exit_time = ts
                    trade.exit_price = exit_price
                    trade.exit_reason = exit_reason
                    trade.pnl = round(pnl, 4)
                    trade.entry_fee = round(entry_fee, 4)
                    trade.exit_fee = round(exit_fee, 4)
                    trade.total_fee = round(total_fee, 4)
                    trade.pnl_after_fee = round(pnl_after_fee, 4)
                    trade.pnl_pct = round(pnl_after_fee / entry_notional * 100 if entry_notional > 0 else 0, 2)

                    equity += pnl_after_fee
                    if equity > peak_equity:
                        peak_equity = equity

                    if pnl_after_fee < 0:
                        consecutive_losses += 1
                        if consecutive_losses >= risk_cfg.cooldown_after_loss:
                            cooldown_until_bar = i + 4
                    else:
                        consecutive_losses = 0

                    self.trades.append(trade)
                    modes_to_close.append(mode)

            for mode in modes_to_close:
                del open_positions[mode]

            # 2. Equity tracking
            unrealized = 0
            for mode, trade in open_positions.items():
                lev = self.modes[mode]["leverage"]
                if trade.side == "buy":
                    unrealized += (price - trade.entry_price) * trade.size * lev
                else:
                    unrealized += (trade.entry_price - price) * trade.size * lev

            current_equity = equity + unrealized
            dd = (peak_equity - current_equity) / peak_equity * 100 if peak_equity > 0 else 0

            # 3. Risk checks
            if i < cooldown_until_bar:
                continue
            daily_loss_limit = self.initial_capital * risk_cfg.max_daily_loss_pct
            if equity < self.initial_capital - daily_loss_limit:
                continue
            max_dd_limit = peak_equity * (1 - risk_cfg.max_drawdown_pct)
            if current_equity < max_dd_limit:
                continue
            if len(open_positions) >= risk_cfg.max_open_positions:
                continue

            # 4. Signal
            signal = self._quick_signal(row, prev, price, atr)
            # Also compute signal strength for filtering
            strength = self._calc_strength(row, prev, price, atr)

            if signal == Signal.HOLD:
                continue

            # 5. Open positions
            from backtest import BacktestTrade
            from config import pair_cfg
            for mode, params in self.modes.items():
                if mode in open_positions:
                    continue
                if signal == Signal.SHORT and not params["can_short"]:
                    continue

                # ── KEY: filter 3x by higher threshold ──
                if mode == "margin_3x" and self.m3_min_strength is not None:
                    if abs(strength) < self.m3_min_strength:
                        continue

                side = "buy" if signal == Signal.LONG else "sell"
                cap = self.initial_capital * params["capital_ratio"]
                risk_amount = cap * params["risk_pct"]
                sl_dist = price * params["sl_pct"]
                size = risk_amount / sl_dist if sl_dist > 0 else 0
                max_size = (cap * params["leverage"]) / price
                size = min(size, max_size)
                if size < pair_cfg.min_order_size:
                    continue
                size = round(size, pair_cfg.size_precision)

                if side == "buy":
                    sl = round(price * (1 - params["sl_pct"]), 1)
                    tp = round(price * (1 + params["tp_pct"]), 1)
                else:
                    sl = round(price * (1 + params["sl_pct"]), 1)
                    tp = round(price * (1 - params["tp_pct"]), 1)

                self.trade_counter += 1
                trade = BacktestTrade(
                    trade_id=self.trade_counter, mode=mode, side=side,
                    entry_time=ts, entry_price=price, size=size,
                    stop_loss=sl, take_profit=tp,
                )
                open_positions[mode] = trade

        # Close remaining positions
        from config import fee_cfg
        last_price = df.iloc[-1]["close"]
        last_ts = str(df.iloc[-1].get("ts", len(df)))
        for mode, trade in open_positions.items():
            lev = self.modes[mode]["leverage"]
            if trade.side == "buy":
                pnl = (last_price - trade.entry_price) * trade.size * lev
            else:
                pnl = (trade.entry_price - last_price) * trade.size * lev
            entry_notional = trade.size * trade.entry_price
            exit_notional = trade.size * last_price
            entry_fee = entry_notional * fee_cfg.default_fee if fee_cfg.enabled else 0
            exit_fee = exit_notional * fee_cfg.default_fee if fee_cfg.enabled else 0
            total_fee = entry_fee + exit_fee
            pnl_after_fee = pnl - total_fee
            trade.exit_time = last_ts
            trade.exit_price = last_price
            trade.exit_reason = "回测结束"
            trade.pnl = round(pnl, 4)
            trade.entry_fee = round(entry_fee, 4)
            trade.exit_fee = round(exit_fee, 4)
            trade.total_fee = round(total_fee, 4)
            trade.pnl_after_fee = round(pnl_after_fee, 4)
            trade.pnl_pct = round(pnl_after_fee / entry_notional * 100 if entry_notional > 0 else 0, 2)
            self.trades.append(trade)
            equity += pnl_after_fee

        return self._calc_metrics(equity)

    def _calc_strength(self, row, prev, price, atr):
        """Calculate raw signal strength (same logic as _quick_signal but returns float)"""
        cfg = strategy_cfg
        ema_score = rsi_score = bb_score = vol_score = 0

        if not pd.isna(row.get("ema_fast")) and not pd.isna(row.get("ema_slow")):
            fast, slow = row["ema_fast"], row["ema_slow"]
            pfst, pslw = prev["ema_fast"], prev["ema_slow"]
            if fast > slow and pfst <= pslw: ema_score = 1.0
            elif fast < slow and pfst >= pslw: ema_score = -1.0
            elif fast > slow: ema_score = min((fast - slow) / slow * 200, 0.8)
            elif fast < slow: ema_score = max(-(slow - fast) / slow * 200, -0.8)

        rsi = row.get("rsi"); prsi = prev.get("rsi")
        if not pd.isna(rsi) and not pd.isna(prsi):
            if rsi < cfg.rsi_oversold and rsi > prsi: rsi_score = 1.0
            elif rsi > cfg.rsi_overbought and rsi < prsi: rsi_score = -1.0
            elif rsi < 45 and rsi > prsi: rsi_score = 0.4
            elif rsi > 55 and rsi < prsi: rsi_score = -0.4

        bb_pct = row.get("bb_pct")
        if not pd.isna(bb_pct):
            if bb_pct < 0.15: bb_score = 1.0
            elif bb_pct < 0.30: bb_score = 0.6
            elif bb_pct > 0.85: bb_score = -1.0
            elif bb_pct > 0.70: bb_score = -0.6

        vr = row.get("vol_ratio")
        if not pd.isna(vr):
            if vr > cfg.vol_surge_ratio: vol_score = 1.0
            elif vr > 1.0: vol_score = 0.3
            else: vol_score = -0.2

        dir_score = ema_score * cfg.ema_weight + rsi_score * cfg.rsi_weight + bb_score * cfg.bb_weight
        dir_w = cfg.ema_weight + cfg.rsi_weight + cfg.bb_weight
        normalized = dir_score / dir_w if dir_w else 0
        final = normalized * (1 + vol_score * cfg.vol_weight)
        return final


# ── Run experiments ──
orig_3x_sl = risk_cfg.margin_3x_stop_loss_pct
orig_3x_tp = risk_cfg.margin_3x_take_profit_pct

experiments = [
    ("基准(当前)", {"sl": orig_3x_sl, "tp": orig_3x_tp, "m3_thresh": None}),
    ("A: 放宽止盈止损", {"sl": 0.025, "tp": 0.035, "m3_thresh": None}),
    ("B: 提高3x阈值", {"sl": orig_3x_sl, "tp": orig_3x_tp, "m3_thresh": 0.5}),
    ("C: 两者结合", {"sl": 0.025, "tp": 0.035, "m3_thresh": 0.5}),
]

all_results = []

for period_name, bars in [("20天", 1920), ("60天", 5760)]:
    df = df_full.tail(bars).reset_index(drop=True)
    date_range = f"{df['ts'].iloc[0].strftime('%m/%d')} - {df['ts'].iloc[-1].strftime('%m/%d')}"

    for exp_name, params in experiments:
        # Set 3x SL/TP
        object.__setattr__(risk_cfg, 'margin_3x_stop_loss_pct', params["sl"])
        object.__setattr__(risk_cfg, 'margin_3x_take_profit_pct', params["tp"])

        engine = ExperimentEngine(initial_capital=100, m3_min_strength=params["m3_thresh"])
        metrics = engine.run(df)

        m3_trades = [t for t in engine.trades if t.mode == "margin_3x"]
        m3_wins = len([t for t in m3_trades if t.pnl_after_fee > 0])
        m3_pnl = sum(t.pnl_after_fee for t in m3_trades)

        # Also get spot and 2x stats
        spot_trades = [t for t in engine.trades if t.mode == "spot"]
        spot_pnl = sum(t.pnl_after_fee for t in spot_trades)
        m2_trades = [t for t in engine.trades if t.mode == "margin_2x"]
        m2_pnl = sum(t.pnl_after_fee for t in m2_trades)

        all_results.append({
            "exp": exp_name,
            "period": period_name,
            "date_range": date_range,
            "return_pct": metrics.get("total_return_pct", 0),
            "trades": metrics["total_trades"],
            "win_rate": metrics.get("win_rate", 0),
            "max_dd": metrics.get("max_drawdown_pct", 0),
            "profit_factor": metrics.get("profit_factor", 0),
            "spot_pnl": round(spot_pnl, 2),
            "m2_pnl": round(m2_pnl, 2),
            "m3_trades": len(m3_trades),
            "m3_winrate": round(m3_wins / len(m3_trades) * 100, 1) if m3_trades else 0,
            "m3_pnl": round(m3_pnl, 2),
        })

        print(f"[{period_name}] {exp_name}: 总收益={metrics.get('total_return_pct',0):+.2f}% | "
              f"3x: {len(m3_trades)}笔 胜率{m3_wins}/{len(m3_trades)} 净利${m3_pnl:+.2f}")

# Restore
object.__setattr__(risk_cfg, 'margin_3x_stop_loss_pct', orig_3x_sl)
object.__setattr__(risk_cfg, 'margin_3x_take_profit_pct', orig_3x_tp)

# ── Print results ──
for period_name in ["20天", "60天"]:
    subset = [r for r in all_results if r["period"] == period_name]
    dr = subset[0]["date_range"]
    print(f"\n{'═' * 110}")
    print(f"  {period_name}回测 ({dr})")
    print(f"{'═' * 110}")
    print(f"{'实验':>18} | {'总收益':>7} | {'交易':>5} | {'胜率':>5} | {'回撤':>5} | {'盈亏比':>5} | {'现货':>6} | {'2x':>6} | {'3x笔':>4} | {'3x胜率':>5} | {'3x净利':>7}")
    print(f"{'─' * 110}")
    for r in subset:
        print(f"{r['exp']:>18} | {r['return_pct']:>+6.2f}% | {r['trades']:>4}笔 | {r['win_rate']:>4.1f}% | {r['max_dd']:>4.2f}% | {r['profit_factor']:>5} | ${r['spot_pnl']:>+5.2f} | ${r['m2_pnl']:>+5.2f} | {r['m3_trades']:>3}笔 | {r['m3_winrate']:>4.1f}% | ${r['m3_pnl']:>+6.2f}")

print(f"\n{'═' * 110}")
print("  3x参数说明:")
print("  基准: SL=1.5% TP=2.0% 阈值=0.3")
print("  A: SL=2.5% TP=3.5% 阈值=0.3 (放宽止盈止损)")
print("  B: SL=1.5% TP=2.0% 阈值=0.5 (提高开仓门槛)")
print("  C: SL=2.5% TP=3.5% 阈值=0.5 (两者结合)")
print(f"{'═' * 110}")
