"""
风险管理模块
仓位计算、止盈止损、日亏损限制、回撤控制
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from config import risk_cfg, capital_cfg, pair_cfg

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """持仓记录"""
    trade_mode: str          # spot / margin_2x / margin_3x
    side: str                # buy / sell
    entry_price: float
    size: float              # BTC 数量
    stop_loss: float
    take_profit: float
    order_id: str = ""
    entry_time: float = 0.0  # Unix timestamp
    pnl: float = 0.0


@dataclass
class RiskState:
    """风险状态"""
    daily_pnl: float = 0.0
    peak_equity: float = capital_cfg.total_capital
    current_equity: float = capital_cfg.total_capital
    consecutive_losses: int = 0
    last_loss_time: float = 0.0
    open_positions: list = field(default_factory=list)
    trade_count_today: int = 0
    day_start_equity: float = capital_cfg.total_capital


class RiskManager:
    """风险管理器"""

    def __init__(self):
        self.cfg = risk_cfg
        self.state = RiskState()

    # ── 仓位计算 ──────────────────────────────
    def calc_position_size(
        self,
        trade_mode: str,
        price: float,
        atr: float,
    ) -> float:
        """
        基于风险预算计算仓位大小

        公式: position_size = (capital * risk_pct) / stop_distance
        stop_distance = ATR * multiplier 或 固定百分比止损
        """
        if price <= 0:
            return 0.0

        # 获取该交易模式的参数
        if trade_mode == "spot":
            capital = capital_cfg.spot_capital
            risk_pct = self.cfg.spot_max_risk_pct
            sl_pct = self.cfg.spot_stop_loss_pct
        elif trade_mode == "margin_2x":
            capital = capital_cfg.margin_2x_capital
            risk_pct = self.cfg.margin_2x_max_risk_pct
            sl_pct = self.cfg.margin_2x_stop_loss_pct
        elif trade_mode == "margin_3x":
            capital = capital_cfg.margin_3x_capital
            risk_pct = self.cfg.margin_3x_max_risk_pct
            sl_pct = self.cfg.margin_3x_stop_loss_pct
        else:
            return 0.0

        # 用实际可用资金更新 (扣除已用)
        used = sum(
            p.size * p.entry_price
            for p in self.state.open_positions
            if p.trade_mode == trade_mode
        )
        available = max(capital - used, 0)

        # 风险金额
        risk_amount = available * risk_pct

        # 止损距离 (用 ATR 或固定百分比，取较小值以更保守)
        atr_stop = atr * 1.5 if atr > 0 else price * sl_pct
        pct_stop = price * sl_pct
        stop_distance = min(atr_stop, pct_stop)

        if stop_distance <= 0:
            return 0.0

        # 仓位 = 风险金额 / 止损距离
        size = risk_amount / stop_distance

        # 杠杆放大可用资金
        leverage = {"spot": 1, "margin_2x": 2, "margin_3x": 3}.get(trade_mode, 1)
        max_size = (available * leverage) / price

        # 不超过最大可用仓位
        size = min(size, max_size)

        # 不低于最小下单量
        if size < pair_cfg.min_order_size:
            logger.warning(
                f"[{trade_mode}] 计算仓位 {size:.8f} BTC 低于最小下单量 "
                f"{pair_cfg.min_order_size}，跳过交易"
            )
            return 0.0

        # 精度处理
        size = round(size, pair_cfg.size_precision)
        return size

    # ── 止盈止损价格 ──────────────────────────
    def calc_stop_loss(self, trade_mode: str, entry_price: float, side: str, atr: float) -> float:
        """计算止损价格"""
        sl_pct_map = {
            "spot": self.cfg.spot_stop_loss_pct,
            "margin_2x": self.cfg.margin_2x_stop_loss_pct,
            "margin_3x": self.cfg.margin_3x_stop_loss_pct,
        }
        sl_pct = sl_pct_map.get(trade_mode, 0.03)

        # ATR 动态止损
        atr_sl = atr * 1.5 if atr > 0 else entry_price * sl_pct
        sl_distance = min(atr_sl, entry_price * sl_pct)

        if side == "buy":
            return round(entry_price - sl_distance, pair_cfg.price_precision)
        else:
            return round(entry_price + sl_distance, pair_cfg.price_precision)

    def calc_take_profit(self, trade_mode: str, entry_price: float, side: str) -> float:
        """计算止盈价格"""
        tp_pct_map = {
            "spot": self.cfg.spot_take_profit_pct,
            "margin_2x": self.cfg.margin_2x_take_profit_pct,
            "margin_3x": self.cfg.margin_3x_take_profit_pct,
        }
        tp_pct = tp_pct_map.get(trade_mode, 0.04)

        if side == "buy":
            return round(entry_price * (1 + tp_pct), pair_cfg.price_precision)
        else:
            return round(entry_price * (1 - tp_pct), pair_cfg.price_precision)

    # ── 交易前置检查 ──────────────────────────
    def can_trade(self, trade_mode: str) -> tuple[bool, str]:
        """
        检查是否允许开新仓
        返回: (allowed, reason)
        """
        # 1. 最大持仓数检查
        if len(self.state.open_positions) >= self.cfg.max_open_positions:
            return False, f"已达最大持仓数 {self.cfg.max_open_positions}"

        # 2. 日亏损限制
        if self.state.daily_pnl < 0:
            loss_ratio = abs(self.state.daily_pnl) / self.state.day_start_equity
            if loss_ratio >= self.cfg.max_daily_loss_pct:
                return False, f"日亏损已达 {loss_ratio:.1%}, 超过限制 {self.cfg.max_daily_loss_pct:.1%}"

        # 3. 最大回撤检查
        drawdown = (self.state.peak_equity - self.state.current_equity) / self.state.peak_equity
        if drawdown >= self.cfg.max_drawdown_pct:
            return False, f"回撤 {drawdown:.1%} 超过限制 {self.cfg.max_drawdown_pct:.1%}"

        # 4. 连续亏损冷却
        if self.state.consecutive_losses >= self.cfg.cooldown_after_loss:
            elapsed = time.time() - self.state.last_loss_time
            if elapsed < self.cfg.cooldown_period:
                remaining = int(self.cfg.cooldown_period - elapsed)
                return False, f"连续亏损冷却中, 还需 {remaining}s"

        # 5. 同一模式是否已有仓位
        mode_positions = [p for p in self.state.open_positions if p.trade_mode == trade_mode]
        if mode_positions:
            return False, f"{trade_mode} 已有持仓"

        return True, "通过所有风险检查"

    # ── 仓位管理 ──────────────────────────────
    def register_position(self, pos: Position):
        """注册新仓位"""
        self.state.open_positions.append(pos)
        self.state.trade_count_today += 1
        logger.info(
            f"新仓位: {pos.trade_mode} {pos.side} {pos.size} BTC "
            f"@ {pos.entry_price} | SL={pos.stop_loss} TP={pos.take_profit}"
        )

    def close_position(self, pos: Position, exit_price: float):
        """关闭仓位并更新状态"""
        if pos.side == "buy":
            pnl = (exit_price - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - exit_price) * pos.size

        # 杠杆效果
        leverage = {"spot": 1, "margin_2x": 2, "margin_3x": 3}.get(pos.trade_mode, 1)
        pnl *= leverage

        pos.pnl = pnl
        self.state.daily_pnl += pnl
        self.state.current_equity += pnl

        # 更新峰值
        if self.state.current_equity > self.state.peak_equity:
            self.state.peak_equity = self.state.current_equity

        # 连续亏损计数
        if pnl < 0:
            self.state.consecutive_losses += 1
            self.state.last_loss_time = time.time()
        else:
            self.state.consecutive_losses = 0

        # 移除仓位
        if pos in self.state.open_positions:
            self.state.open_positions.remove(pos)

        logger.info(
            f"平仓: {pos.trade_mode} {pos.side} {pos.size} BTC "
            f"@ {exit_price} | PnL={pnl:+.4f} USDT | 当日PnL={self.state.daily_pnl:+.4f}"
        )

    def reset_daily(self):
        """每日重置"""
        logger.info(
            f"日结: PnL={self.state.daily_pnl:+.4f} | "
            f"净值={self.state.current_equity:.2f} | "
            f"交易次数={self.state.trade_count_today}"
        )
        self.state.daily_pnl = 0.0
        self.state.trade_count_today = 0
        self.state.day_start_equity = self.state.current_equity

    def get_status(self) -> dict:
        """获取当前风险状态摘要"""
        drawdown = (
            (self.state.peak_equity - self.state.current_equity) / self.state.peak_equity
            if self.state.peak_equity > 0
            else 0
        )
        return {
            "equity": round(self.state.current_equity, 2),
            "daily_pnl": round(self.state.daily_pnl, 4),
            "drawdown_pct": round(drawdown * 100, 2),
            "open_positions": len(self.state.open_positions),
            "consecutive_losses": self.state.consecutive_losses,
            "trades_today": self.state.trade_count_today,
        }
