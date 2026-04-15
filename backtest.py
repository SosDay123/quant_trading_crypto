"""
回测引擎 — 完整版
用历史K线数据回测多信号融合策略，生成详细的绩效报告

用法:
    python backtest.py                     # 用模拟数据回测
    python backtest.py --data btc_15m.csv  # 用自定义CSV回测

CSV 格式: ts,open,high,low,close,vol (无表头也可)
"""
import argparse
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config import strategy_cfg, risk_cfg, capital_cfg, pair_cfg, fee_cfg
from indicators import compute_all_indicators, parse_candles_to_df
from strategy import MultiSignalStrategy, Signal

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("backtest")


# ══════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════
@dataclass
class BacktestTrade:
    """单笔交易记录"""
    trade_id: int
    mode: str             # spot / margin_2x / margin_3x
    side: str             # buy / sell
    entry_time: str
    entry_price: float
    size: float           # BTC
    stop_loss: float
    take_profit: float
    exit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_bars: int = 0
    entry_fee: float = 0.0    # 开仓手续费
    exit_fee: float = 0.0     # 平仓手续费
    total_fee: float = 0.0    # 总手续费
    pnl_after_fee: float = 0.0  # 扣费后净利


@dataclass
class EquityPoint:
    """净值记录点"""
    time: str
    equity: float
    drawdown_pct: float


# ══════════════════════════════════════════════
# 回测引擎
# ══════════════════════════════════════════════
class BacktestEngine:
    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or capital_cfg.total_capital
        self.strategy = MultiSignalStrategy()
        self.trades: list[BacktestTrade] = []
        self.equity_curve: list[EquityPoint] = []
        self.trade_counter = 0

        # 各模式参数
        self.modes = {
            "spot": {
                "capital_ratio": capital_cfg.spot_ratio,
                "leverage": 1,
                "risk_pct": risk_cfg.spot_max_risk_pct,
                "sl_pct": risk_cfg.spot_stop_loss_pct,
                "tp_pct": risk_cfg.spot_take_profit_pct,
                "can_short": False,
            },
            "margin_2x": {
                "capital_ratio": capital_cfg.margin_2x_ratio,
                "leverage": 2,
                "risk_pct": risk_cfg.margin_2x_max_risk_pct,
                "sl_pct": risk_cfg.margin_2x_stop_loss_pct,
                "tp_pct": risk_cfg.margin_2x_take_profit_pct,
                "can_short": True,
            },
            "margin_3x": {
                "capital_ratio": capital_cfg.margin_3x_ratio,
                "leverage": 3,
                "risk_pct": risk_cfg.margin_3x_max_risk_pct,
                "sl_pct": risk_cfg.margin_3x_stop_loss_pct,
                "tp_pct": risk_cfg.margin_3x_take_profit_pct,
                "can_short": True,
            },
        }

    def run(self, df: pd.DataFrame) -> dict:
        """
        执行回测

        参数:
            df: 包含 open/high/low/close/vol 列的 DataFrame (正序)

        返回:
            绩效指标字典
        """
        if len(df) < 50:
            raise ValueError("数据不足，至少需要50根K线")

        # 计算指标
        df = compute_all_indicators(df)

        equity = self.initial_capital
        peak_equity = equity
        open_positions: dict[str, BacktestTrade] = {}  # mode -> trade
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

            # ── 1. 检查已有仓位是否触及止盈止损 ──
            modes_to_close = []
            for mode, trade in open_positions.items():
                hit_sl = False
                hit_tp = False
                exit_price = 0
                exit_reason = ""

                if trade.side == "buy":
                    if low <= trade.stop_loss:
                        hit_sl = True
                        exit_price = trade.stop_loss
                        exit_reason = "止损"
                    elif high >= trade.take_profit:
                        hit_tp = True
                        exit_price = trade.take_profit
                        exit_reason = "止盈"
                else:  # sell (做空)
                    if high >= trade.stop_loss:
                        hit_sl = True
                        exit_price = trade.stop_loss
                        exit_reason = "止损"
                    elif low <= trade.take_profit:
                        hit_tp = True
                        exit_price = trade.take_profit
                        exit_reason = "止盈"

                if hit_sl or hit_tp:
                    # 计算 PnL
                    leverage = self.modes[mode]["leverage"]
                    if trade.side == "buy":
                        pnl = (exit_price - trade.entry_price) * trade.size * leverage
                    else:
                        pnl = (trade.entry_price - exit_price) * trade.size * leverage

                    # 计算手续费 (开仓+平仓各收一次, 按名义价值计算)
                    entry_notional = trade.size * trade.entry_price
                    exit_notional = trade.size * exit_price
                    entry_fee = entry_notional * fee_cfg.default_fee if fee_cfg.enabled else 0
                    exit_fee = exit_notional * fee_cfg.default_fee if fee_cfg.enabled else 0
                    total_fee = entry_fee + exit_fee

                    pnl_after_fee = pnl - total_fee
                    cost = entry_notional
                    pnl_pct = pnl_after_fee / cost * 100 if cost > 0 else 0

                    trade.exit_time = ts
                    trade.exit_price = exit_price
                    trade.exit_reason = exit_reason
                    trade.pnl = round(pnl, 4)
                    trade.entry_fee = round(entry_fee, 4)
                    trade.exit_fee = round(exit_fee, 4)
                    trade.total_fee = round(total_fee, 4)
                    trade.pnl_after_fee = round(pnl_after_fee, 4)
                    trade.pnl_pct = round(pnl_pct, 2)
                    trade.hold_bars = i - (trade.trade_id * 0 + int(trade.entry_time.split("_")[-1]) if "_" in trade.entry_time else 0)

                    equity += pnl_after_fee
                    if equity > peak_equity:
                        peak_equity = equity

                    if pnl_after_fee < 0:
                        consecutive_losses += 1
                        if consecutive_losses >= risk_cfg.cooldown_after_loss:
                            cooldown_until_bar = i + 4  # 冷却4根K线
                    else:
                        consecutive_losses = 0

                    self.trades.append(trade)
                    modes_to_close.append(mode)

            for mode in modes_to_close:
                del open_positions[mode]

            # ── 2. 记录净值 ──
            # 计算未实现盈亏
            unrealized = 0
            for mode, trade in open_positions.items():
                lev = self.modes[mode]["leverage"]
                if trade.side == "buy":
                    unrealized += (price - trade.entry_price) * trade.size * lev
                else:
                    unrealized += (trade.entry_price - price) * trade.size * lev

            current_equity = equity + unrealized
            dd = (peak_equity - current_equity) / peak_equity * 100 if peak_equity > 0 else 0

            if i % 5 == 0:  # 每5根记录一次
                self.equity_curve.append(EquityPoint(ts, round(current_equity, 2), round(dd, 2)))

            # ── 3. 风控熔断检查 ──
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

            # ── 4. 生成信号 ──
            # 用当前行的指标直接评分 (避免重复计算)
            signal = self._quick_signal(row, prev, price, atr)
            if signal == Signal.HOLD:
                continue

            # ── 5. 尝试开仓 ──
            for mode, params in self.modes.items():
                if mode in open_positions:
                    continue
                if signal == Signal.SHORT and not params["can_short"]:
                    continue

                side = "buy" if signal == Signal.LONG else "sell"
                cap = self.initial_capital * params["capital_ratio"]

                # 仓位计算
                risk_amount = cap * params["risk_pct"]
                sl_dist = price * params["sl_pct"]
                size = risk_amount / sl_dist if sl_dist > 0 else 0
                max_size = (cap * params["leverage"]) / price
                size = min(size, max_size)

                if size < pair_cfg.min_order_size:
                    continue

                size = round(size, pair_cfg.size_precision)

                # 止盈止损
                if side == "buy":
                    sl = round(price * (1 - params["sl_pct"]), 1)
                    tp = round(price * (1 + params["tp_pct"]), 1)
                else:
                    sl = round(price * (1 + params["sl_pct"]), 1)
                    tp = round(price * (1 - params["tp_pct"]), 1)

                self.trade_counter += 1
                trade = BacktestTrade(
                    trade_id=self.trade_counter,
                    mode=mode,
                    side=side,
                    entry_time=ts,
                    entry_price=price,
                    size=size,
                    stop_loss=sl,
                    take_profit=tp,
                )
                open_positions[mode] = trade

        # ── 强平剩余仓位 ──
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

        # ── 生成绩效指标 ──
        return self._calc_metrics(equity)

    def _quick_signal(self, row, prev, price, atr) -> Signal:
        """快速信号评分 (直接用已计算好的指标)"""
        cfg = strategy_cfg

        # EMA
        ema_score = 0
        if not pd.isna(row.get("ema_fast")) and not pd.isna(row.get("ema_slow")):
            fast, slow = row["ema_fast"], row["ema_slow"]
            pfst, pslw = prev["ema_fast"], prev["ema_slow"]
            if fast > slow and pfst <= pslw:
                ema_score = 1.0
            elif fast < slow and pfst >= pslw:
                ema_score = -1.0
            elif fast > slow:
                ema_score = min((fast - slow) / slow * 200, 0.8)
            elif fast < slow:
                ema_score = max(-(slow - fast) / slow * 200, -0.8)

        # RSI
        rsi_score = 0
        rsi = row.get("rsi")
        prsi = prev.get("rsi")
        if not pd.isna(rsi) and not pd.isna(prsi):
            if rsi < cfg.rsi_oversold and rsi > prsi:
                rsi_score = 1.0
            elif rsi > cfg.rsi_overbought and rsi < prsi:
                rsi_score = -1.0
            elif rsi < 45 and rsi > prsi:
                rsi_score = 0.4
            elif rsi > 55 and rsi < prsi:
                rsi_score = -0.4

        # BB
        bb_score = 0
        bb_pct = row.get("bb_pct")
        if not pd.isna(bb_pct):
            if bb_pct < 0.15:
                bb_score = 1.0
            elif bb_pct < 0.30:
                bb_score = 0.6
            elif bb_pct > 0.85:
                bb_score = -1.0
            elif bb_pct > 0.70:
                bb_score = -0.6

        # Volume
        vol_score = 0
        vr = row.get("vol_ratio")
        if not pd.isna(vr):
            if vr > cfg.vol_surge_ratio:
                vol_score = 1.0
            elif vr > 1.0:
                vol_score = 0.3
            else:
                vol_score = -0.2

        # 综合
        dir_score = (
            ema_score * cfg.ema_weight
            + rsi_score * cfg.rsi_weight
            + bb_score * cfg.bb_weight
        )
        dir_w = cfg.ema_weight + cfg.rsi_weight + cfg.bb_weight
        normalized = dir_score / dir_w if dir_w else 0
        final = normalized * (1 + vol_score * cfg.vol_weight)

        if final >= cfg.signal_threshold:
            return Signal.LONG
        elif final <= -cfg.signal_threshold:
            return Signal.SHORT
        return Signal.HOLD

    def _calc_metrics(self, final_equity: float) -> dict:
        """计算绩效指标"""
        total = len(self.trades)
        if total == 0:
            return {"total_trades": 0, "final_equity": round(final_equity, 2)}

        # 以扣费后净利为基准
        wins = [t for t in self.trades if t.pnl_after_fee > 0]
        losses = [t for t in self.trades if t.pnl_after_fee < 0]
        even = [t for t in self.trades if t.pnl_after_fee == 0]

        total_pnl_gross = sum(t.pnl for t in self.trades)
        total_fees = sum(t.total_fee for t in self.trades)
        total_pnl_net = sum(t.pnl_after_fee for t in self.trades)

        gross_profit = sum(t.pnl_after_fee for t in wins) if wins else 0
        gross_loss = sum(t.pnl_after_fee for t in losses) if losses else 0

        win_rate = len(wins) / total * 100
        avg_win = gross_profit / len(wins) if wins else 0
        avg_loss = abs(gross_loss) / len(losses) if losses else 0
        profit_factor = gross_profit / abs(gross_loss) if gross_loss != 0 else float("inf")

        # 平均每笔手续费
        avg_fee = total_fees / total if total > 0 else 0
        # 手续费占毛利的比例
        fee_drag_pct = total_fees / total_pnl_gross * 100 if total_pnl_gross > 0 else 0

        # 最大回撤
        max_dd = 0
        peak = self.initial_capital
        running = self.initial_capital
        for t in self.trades:
            running += t.pnl_after_fee
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # 最大连续亏损
        max_consec_loss = 0
        cur_consec = 0
        for t in self.trades:
            if t.pnl_after_fee < 0:
                cur_consec += 1
                max_consec_loss = max(max_consec_loss, cur_consec)
            else:
                cur_consec = 0

        # 按模式分组
        mode_stats = {}
        for mode in ["spot", "margin_2x", "margin_3x"]:
            mt = [t for t in self.trades if t.mode == mode]
            if mt:
                mw = [t for t in mt if t.pnl_after_fee > 0]
                mode_fees = sum(t.total_fee for t in mt)
                mode_stats[mode] = {
                    "trades": len(mt),
                    "pnl_gross": round(sum(t.pnl for t in mt), 4),
                    "fees": round(mode_fees, 4),
                    "pnl_net": round(sum(t.pnl_after_fee for t in mt), 4),
                    "win_rate": round(len(mw) / len(mt) * 100, 1),
                }

        # 按做多/做空
        longs = [t for t in self.trades if t.side == "buy"]
        shorts = [t for t in self.trades if t.side == "sell"]

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_pnl_gross": round(total_pnl_gross, 4),
            "total_fees": round(total_fees, 4),
            "total_pnl_net": round(total_pnl_net, 4),
            "total_return_pct": round(total_pnl_net / self.initial_capital * 100, 2),
            "fee_drag_pct": round(fee_drag_pct, 1),
            "avg_fee_per_trade": round(avg_fee, 4),
            "total_trades": total,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "even_trades": len(even),
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "∞",
            "max_drawdown_pct": round(max_dd, 2),
            "max_consecutive_losses": max_consec_loss,
            "long_trades": len(longs),
            "short_trades": len(shorts),
            "long_pnl": round(sum(t.pnl_after_fee for t in longs), 4),
            "short_pnl": round(sum(t.pnl_after_fee for t in shorts), 4),
            "mode_stats": mode_stats,
        }


# ══════════════════════════════════════════════
# 模拟数据生成 (用于无真实数据时的测试)
# ══════════════════════════════════════════════
def generate_synthetic_btc(
    n_bars: int = 2000,
    start_price: float = 60000,
    interval_minutes: int = 15,
    seed: int = 42,
) -> pd.DataFrame:
    """
    生成模拟 BTC 15分钟 K线数据
    使用几何布朗运动 + 均值回归 + 趋势变换
    """
    rng = np.random.default_rng(seed)

    prices = [start_price]
    volumes = []

    # 模拟参数
    mu = 0.00002          # 微小正向漂移
    sigma = 0.004         # 波动率
    mean_rev = 0.001      # 均值回归强度
    trend_change = 0.002  # 趋势变化概率

    trend = 1  # 1=上升, -1=下降
    trend_strength = 0.0003

    for i in range(1, n_bars):
        # 随机改变趋势
        if rng.random() < trend_change:
            trend *= -1

        # 收益率 = 漂移 + 趋势 + 均值回归 + 随机噪声
        drift = mu + trend * trend_strength
        noise = rng.normal(0, sigma)
        rev = -mean_rev * (prices[-1] - start_price) / start_price
        ret = drift + noise + rev

        new_price = prices[-1] * (1 + ret)
        prices.append(max(new_price, start_price * 0.5))

        # 成交量 (与波动正相关)
        base_vol = 50 + rng.exponential(30)
        vol_spike = 3.0 if abs(ret) > sigma * 2 else 1.0
        volumes.append(base_vol * vol_spike)

    volumes.insert(0, 50)

    # 生成 OHLC
    rows = []
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    for i in range(n_bars):
        ts = base_time + timedelta(minutes=interval_minutes * i)
        c = prices[i]
        # open 在 close 附近波动
        noise = rng.normal(0, sigma * 0.3)
        o = c * (1 - noise) if i == 0 else prices[i - 1] + rng.normal(0, c * sigma * 0.5)
        o = max(o, c * 0.97)
        o = min(o, c * 1.03)
        h = max(o, c) * (1 + abs(rng.normal(0, sigma * 0.5)))
        l = min(o, c) * (1 - abs(rng.normal(0, sigma * 0.5)))

        rows.append({
            "ts": ts,
            "open": round(o, 1),
            "high": round(h, 1),
            "low": round(l, 1),
            "close": round(c, 1),
            "vol": round(volumes[i], 3),
        })

    return pd.DataFrame(rows)


# ══════════════════════════════════════════════
# 报告输出
# ══════════════════════════════════════════════
def print_report(metrics: dict, trades: list[BacktestTrade]):
    """打印详细回测报告"""
    m = metrics

    print("\n" + "═" * 68)
    print("           BTC/USDT 量化策略回测报告")
    print("═" * 68)

    print(f"\n  初始资金:        ${m['initial_capital']:.2f}")
    print(f"  最终净值:        ${m['final_equity']:.2f}")
    print(f"  毛利润:          ${m['total_pnl_gross']:+.4f}")
    print(f"  总手续费:        ${m['total_fees']:.4f}  (每笔平均 ${m['avg_fee_per_trade']:.4f})")
    if m.get('fee_drag_pct', 0) > 0:
        print(f"  手续费侵蚀:      {m['fee_drag_pct']:.1f}% 的毛利润")
    print(f"  净利润:          ${m['total_pnl_net']:+.4f}  ({m['total_return_pct']:+.2f}%)")
    print(f"  最大回撤:        {m['max_drawdown_pct']:.2f}%")
    pf = m['profit_factor']
    print(f"  盈亏比:          {pf}")

    print(f"\n  ── 交易统计 ──")
    print(f"  总交易次数:      {m['total_trades']}")
    print(f"  盈利:            {m['winning_trades']}  |  亏损: {m['losing_trades']}  |  持平: {m['even_trades']}")
    print(f"  胜率:            {m['win_rate']:.1f}%")
    print(f"  平均盈利(净):    ${m['avg_win']:.4f}")
    print(f"  平均亏损(净):    ${m['avg_loss']:.4f}")
    print(f"  最大连续亏损:    {m['max_consecutive_losses']}次")

    print(f"\n  ── 多空统计 ──")
    print(f"  做多: {m['long_trades']}笔  净PnL=${m['long_pnl']:+.4f}")
    print(f"  做空: {m['short_trades']}笔  净PnL=${m['short_pnl']:+.4f}")

    print(f"\n  ── 模式明细 ──")
    for mode, s in m.get("mode_stats", {}).items():
        label = {"spot": "现货", "margin_2x": "2x杠杆", "margin_3x": "3x杠杆"}.get(mode, mode)
        print(f"  {label:>8}:  {s['trades']}笔  毛利=${s['pnl_gross']:+.4f}  手续费=${s['fees']:.4f}  净利=${s['pnl_net']:+.4f}  胜率={s['win_rate']:.1f}%")

    # 最近10笔交易明细
    print(f"\n  ── 最近交易明细 (最后10笔) ──")
    print(f"  {'模式':>8} {'方向':>4} {'入场价':>10} {'出场价':>10} {'数量':>10} {'毛利':>9} {'手续费':>7} {'净利':>9} {'原因':>6}")
    print(f"  {'-'*84}")
    for t in trades[-10:]:
        label = {"spot": "现货", "margin_2x": "2x杠杆", "margin_3x": "3x杠杆"}.get(t.mode, t.mode)
        side_label = "多" if t.side == "buy" else "空"
        print(f"  {label:>8} {side_label:>4} {t.entry_price:>10.1f} {t.exit_price:>10.1f} {t.size:>10.5f} {t.pnl:>+9.4f} {t.total_fee:>7.4f} {t.pnl_after_fee:>+9.4f} {t.exit_reason:>6}")

    print("═" * 68)


# ══════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="BTC/USDT 策略回测")
    parser.add_argument("--data", type=str, default=None, help="CSV 数据文件路径")
    parser.add_argument("--bars", type=int, default=2000, help="模拟数据K线数量")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--capital", type=float, default=100, help="初始资金")
    args = parser.parse_args()

    if args.data:
        print(f"加载数据: {args.data}")
        df = pd.read_csv(args.data)
        if "ts" not in df.columns:
            df.columns = ["ts", "open", "high", "low", "close", "vol"][:len(df.columns)]
        for col in ["open", "high", "low", "close", "vol"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"])
    else:
        print(f"生成模拟数据: {args.bars}根 15分钟K线 (seed={args.seed})")
        df = generate_synthetic_btc(n_bars=args.bars, seed=args.seed)

    print(f"数据范围: {df['ts'].iloc[0]} → {df['ts'].iloc[-1]}")
    print(f"价格范围: ${df['close'].min():.1f} ~ ${df['close'].max():.1f}")
    print(f"K线总数:  {len(df)}")

    engine = BacktestEngine(initial_capital=args.capital)
    metrics = engine.run(df)
    print_report(metrics, engine.trades)

    # 导出交易记录
    if engine.trades:
        trade_dicts = []
        for t in engine.trades:
            trade_dicts.append({
                "id": t.trade_id, "mode": t.mode, "side": t.side,
                "entry_time": t.entry_time, "entry_price": t.entry_price,
                "exit_time": t.exit_time, "exit_price": t.exit_price,
                "size": t.size, "pnl": t.pnl, "reason": t.exit_reason,
            })
        pd.DataFrame(trade_dicts).to_csv("backtest_trades.csv", index=False)
        print("\n交易记录已导出: backtest_trades.csv")

    return metrics, engine


if __name__ == "__main__":
    main()
