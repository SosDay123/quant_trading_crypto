"""
交易策略模块
多信号融合策略: EMA交叉 + RSI + 布林带 + 成交量
"""
import logging
from enum import Enum
from dataclasses import dataclass

import pandas as pd
import numpy as np

from config import strategy_cfg
from indicators import compute_all_indicators, parse_candles_to_df

logger = logging.getLogger(__name__)


class Signal(Enum):
    LONG = "LONG"       # 做多信号
    SHORT = "SHORT"     # 做空信号 (仅杠杆可用)
    CLOSE = "CLOSE"     # 平仓信号
    HOLD = "HOLD"       # 持仓等待


@dataclass
class TradeSignal:
    signal: Signal
    strength: float      # 信号强度 0~1
    price: float         # 当前价格
    reason: str          # 信号原因描述
    atr: float           # 当前ATR (用于止损计算)
    timestamp: str


class MultiSignalStrategy:
    """
    多信号融合策略

    做多条件 (得分 > threshold):
      - EMA 快线在慢线上方 → +0.35
      - RSI < 70 且向上拐头 → +0.25
      - 价格在布林带下轨附近 (bb_pct < 0.3) → +0.25
      - 成交量放大 > 1.5 倍 → +0.15

    做空条件 (得分 < -threshold):
      - EMA 快线在慢线下方 → -0.35
      - RSI > 30 且向下拐头 → -0.25
      - 价格在布林带上轨附近 (bb_pct > 0.7) → -0.25
      - 成交量放大 > 1.5 倍 → -0.15
    """

    def __init__(self):
        self.cfg = strategy_cfg

    def _score_ema(self, row: pd.Series, prev_row: pd.Series) -> float:
        """EMA 交叉得分"""
        if pd.isna(row.get("ema_fast")) or pd.isna(row.get("ema_slow")):
            return 0.0

        fast, slow = row["ema_fast"], row["ema_slow"]
        prev_fast, prev_slow = prev_row["ema_fast"], prev_row["ema_slow"]

        # 金叉 (快线上穿慢线)
        if fast > slow and prev_fast <= prev_slow:
            return 1.0
        # 死叉 (快线下穿慢线)
        elif fast < slow and prev_fast >= prev_slow:
            return -1.0
        # 快线在上方但未交叉
        elif fast > slow:
            gap_pct = (fast - slow) / slow * 100
            return min(gap_pct / 0.5, 0.8)  # 最高0.8
        # 快线在下方
        elif fast < slow:
            gap_pct = (slow - fast) / slow * 100
            return max(-gap_pct / 0.5, -0.8)

        return 0.0

    def _score_rsi(self, row: pd.Series, prev_row: pd.Series) -> float:
        """RSI 得分"""
        rsi = row.get("rsi")
        prev_rsi = prev_row.get("rsi")
        if pd.isna(rsi) or pd.isna(prev_rsi):
            return 0.0

        # 超卖区域且向上拐头 → 做多
        if rsi < self.cfg.rsi_oversold and rsi > prev_rsi:
            return 1.0
        # 超买区域且向下拐头 → 做空
        elif rsi > self.cfg.rsi_overbought and rsi < prev_rsi:
            return -1.0
        # 中性区域的微调
        elif rsi < 45 and rsi > prev_rsi:
            return 0.4
        elif rsi > 55 and rsi < prev_rsi:
            return -0.4

        return 0.0

    def _score_bollinger(self, row: pd.Series) -> float:
        """布林带得分"""
        bb_pct = row.get("bb_pct")
        if pd.isna(bb_pct):
            return 0.0

        # 价格在下轨附近 → 看多
        if bb_pct < 0.15:
            return 1.0
        elif bb_pct < 0.30:
            return 0.6
        # 价格在上轨附近 → 看空
        elif bb_pct > 0.85:
            return -1.0
        elif bb_pct > 0.70:
            return -0.6

        return 0.0

    def _score_volume(self, row: pd.Series) -> float:
        """成交量得分 (仅作为确认, 不分方向)"""
        vol_ratio = row.get("vol_ratio")
        if pd.isna(vol_ratio):
            return 0.0

        if vol_ratio > self.cfg.vol_surge_ratio:
            return 1.0  # 量能放大 → 增强信号
        elif vol_ratio > 1.0:
            return 0.3  # 正常量能
        else:
            return -0.2  # 量能萎缩 → 信号减弱

    def generate_signal(self, raw_candles: list) -> TradeSignal:
        """
        生成交易信号

        参数:
            raw_candles: OKX 返回的原始K线数据

        返回:
            TradeSignal 包含信号方向、强度和其他信息
        """
        df = parse_candles_to_df(raw_candles)
        if len(df) < 30:
            return TradeSignal(
                Signal.HOLD, 0.0, 0.0,
                "数据不足，等待更多K线", 0.0, ""
            )

        df = compute_all_indicators(df)

        # 取最新两根K线
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        price = latest["close"]
        atr = latest.get("atr", 0.0)
        ts = str(latest["ts"])

        # 计算各维度得分
        ema_score = self._score_ema(latest, prev)
        rsi_score = self._score_rsi(latest, prev)
        bb_score = self._score_bollinger(latest)
        vol_score = self._score_volume(latest)

        # 成交量作为方向中性的确认因子
        # 如果其他三个方向一致，成交量加强信号；否则不影响方向
        directional_score = (
            ema_score * self.cfg.ema_weight
            + rsi_score * self.cfg.rsi_weight
            + bb_score * self.cfg.bb_weight
        )

        # 成交量增强或削弱信号强度
        dir_weight_sum = self.cfg.ema_weight + self.cfg.rsi_weight + self.cfg.bb_weight
        normalized_dir = directional_score / dir_weight_sum if dir_weight_sum else 0

        vol_multiplier = 1.0 + (vol_score * self.cfg.vol_weight)
        final_score = normalized_dir * vol_multiplier

        # 构建原因描述
        reasons = []
        if abs(ema_score) > 0.3:
            reasons.append(f"EMA{'金叉' if ema_score > 0 else '死叉'}({ema_score:+.2f})")
        if abs(rsi_score) > 0.3:
            reasons.append(f"RSI={latest.get('rsi', 0):.1f}({rsi_score:+.2f})")
        if abs(bb_score) > 0.3:
            reasons.append(f"BB%={latest.get('bb_pct', 0):.2f}({bb_score:+.2f})")
        if abs(vol_score) > 0.2:
            reasons.append(f"量比={latest.get('vol_ratio', 0):.1f}")

        reason = " | ".join(reasons) if reasons else "无明显信号"

        # 根据得分判定信号
        if final_score >= self.cfg.signal_threshold:
            signal = Signal.LONG
        elif final_score <= -self.cfg.signal_threshold:
            signal = Signal.SHORT
        else:
            signal = Signal.HOLD

        strength = min(abs(final_score), 1.0)

        logger.info(
            f"信号: {signal.value} | 强度: {strength:.3f} | "
            f"价格: {price:.1f} | ATR: {atr:.1f} | {reason}"
        )

        return TradeSignal(
            signal=signal,
            strength=strength,
            price=price,
            reason=reason,
            atr=atr if not pd.isna(atr) else 0.0,
            timestamp=ts,
        )
