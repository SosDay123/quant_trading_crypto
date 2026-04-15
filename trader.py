"""
交易执行引擎
负责将策略信号转化为实际交易操作
"""
import time
import logging
from typing import Optional

from config import pair_cfg, risk_cfg
from okx_client import OKXClient
from strategy import MultiSignalStrategy, Signal, TradeSignal
from risk_manager import RiskManager, Position

logger = logging.getLogger(__name__)


# 交易模式 → OKX tdMode 映射
TD_MODE_MAP = {
    "spot": "cash",
    "margin_2x": "cross",
    "margin_3x": "cross",
}

LEVERAGE_MAP = {
    "spot": 1,
    "margin_2x": 2,
    "margin_3x": 3,
}


class Trader:
    """交易执行器"""

    def __init__(self, client: OKXClient, strategy: MultiSignalStrategy, risk_mgr: RiskManager):
        self.client = client
        self.strategy = strategy
        self.risk = risk_mgr
        self.inst_id = pair_cfg.inst_id

    def setup(self):
        """初始化: 设置账户模式和杠杆"""
        logger.info("=== 初始化交易环境 ===")

        # 设置为单币种保证金模式
        result = self.client.set_account_mode("2")
        logger.info(f"账户模式设置: {result.get('msg', 'ok')}")

        # 设置杠杆
        for mode, lever in [("margin_2x", 2), ("margin_3x", 3)]:
            result = self.client.set_leverage(self.inst_id, lever, "cross")
            logger.info(f"设置 {mode} 杠杆 {lever}x: {result.get('msg', 'ok')}")

        # 查询余额
        balance = self.client.get_balance("USDT")
        if balance:
            details = balance.get("details", [])
            for d in details:
                if d.get("ccy") == "USDT":
                    avail = d.get("availBal", "0")
                    logger.info(f"USDT 可用余额: {avail}")

        logger.info("=== 初始化完成 ===")

    # ── 核心交易循环 ──────────────────────────
    def execute_cycle(self):
        """
        单次交易循环:
        1. 获取K线 → 2. 生成信号 → 3. 风险检查 → 4. 执行交易 → 5. 管理仓位
        """
        # 1. 获取K线数据
        candles = self.client.get_candles(limit=100)
        if not candles:
            logger.warning("获取K线数据失败，跳过本轮")
            return

        # 2. 生成信号
        signal = self.strategy.generate_signal(candles)

        # 3. 检查并管理已有仓位
        self._check_open_positions()

        # 4. 若有新信号，尝试开仓
        if signal.signal in (Signal.LONG, Signal.SHORT):
            self._try_open_positions(signal)

        # 5. 状态汇报
        status = self.risk.get_status()
        logger.debug(
            f"状态: 净值={status['equity']} | 日PnL={status['daily_pnl']} | "
            f"回撤={status['drawdown_pct']}% | 持仓={status['open_positions']}"
        )

    # ── 开仓 ──────────────────────────────────
    def _try_open_positions(self, signal: TradeSignal):
        """对三种交易模式分别尝试开仓"""
        modes = ["spot", "margin_2x", "margin_3x"]

        for mode in modes:
            # 现货不做空
            if mode == "spot" and signal.signal == Signal.SHORT:
                continue

            allowed, reason = self.risk.can_trade(mode)
            if not allowed:
                logger.debug(f"[{mode}] 风控拒绝: {reason}")
                continue

            size = self.risk.calc_position_size(mode, signal.price, signal.atr)
            if size <= 0:
                continue

            side = "buy" if signal.signal == Signal.LONG else "sell"
            td_mode = TD_MODE_MAP[mode]

            # 计算止盈止损
            sl = self.risk.calc_stop_loss(mode, signal.price, side, signal.atr)
            tp = self.risk.calc_take_profit(mode, signal.price, side)

            logger.info(
                f"[{mode}] 准备下单: {side} {size} BTC @ ~{signal.price} "
                f"| SL={sl} TP={tp} | 信号强度={signal.strength:.3f}"
            )

            # 下市价单
            result = self.client.place_order(
                inst_id=self.inst_id,
                td_mode=td_mode,
                side=side,
                sz=str(size),
                ord_type="market",
            )

            if result.get("code") == "0" and result.get("data"):
                ord_id = result["data"][0].get("ordId", "")

                # 查询成交价
                time.sleep(0.5)
                order_info = self.client.get_order(self.inst_id, ord_id)
                fill_price = float(order_info.get("avgPx", signal.price) or signal.price)

                # 重新计算止盈止损 (以实际成交价为准)
                sl = self.risk.calc_stop_loss(mode, fill_price, side, signal.atr)
                tp = self.risk.calc_take_profit(mode, fill_price, side)

                # 注册仓位
                pos = Position(
                    trade_mode=mode,
                    side=side,
                    entry_price=fill_price,
                    size=size,
                    stop_loss=sl,
                    take_profit=tp,
                    order_id=ord_id,
                    entry_time=time.time(),
                )
                self.risk.register_position(pos)

                # 挂止盈止损委托单
                close_side = "sell" if side == "buy" else "buy"
                self.client.place_algo_order(
                    inst_id=self.inst_id,
                    td_mode=td_mode,
                    side=close_side,
                    sz=str(size),
                    order_type="conditional",
                    tp_trigger_px=str(tp),
                    sl_trigger_px=str(sl),
                )
                logger.info(f"[{mode}] 止盈止损委托已挂出: SL={sl} TP={tp}")

    # ── 仓位检查 ──────────────────────────────
    def _check_open_positions(self):
        """检查已有仓位是否触及止盈止损"""
        if not self.risk.state.open_positions:
            return

        ticker = self.client.get_ticker()
        if not ticker:
            return

        current_price = float(ticker.get("last", 0))
        if current_price <= 0:
            return

        positions_to_close = []
        for pos in list(self.risk.state.open_positions):
            should_close, reason = self._should_close(pos, current_price)
            if should_close:
                positions_to_close.append((pos, reason, current_price))

        for pos, reason, price in positions_to_close:
            self._close_position(pos, price, reason)

    def _should_close(self, pos: Position, current_price: float) -> tuple[bool, str]:
        """判断是否应该平仓"""
        if pos.side == "buy":
            # 多仓
            if current_price <= pos.stop_loss:
                return True, "触发止损"
            if current_price >= pos.take_profit:
                return True, "触发止盈"
        else:
            # 空仓
            if current_price >= pos.stop_loss:
                return True, "触发止损"
            if current_price <= pos.take_profit:
                return True, "触发止盈"

        # 超时检查 (持仓超过24小时强制平仓)
        if time.time() - pos.entry_time > 86400:
            return True, "持仓超时(24h)"

        return False, ""

    def _close_position(self, pos: Position, price: float, reason: str):
        """执行平仓"""
        close_side = "sell" if pos.side == "buy" else "buy"
        td_mode = TD_MODE_MAP[pos.trade_mode]

        logger.info(f"[{pos.trade_mode}] 平仓: {reason} | 当前价={price}")

        result = self.client.place_order(
            inst_id=self.inst_id,
            td_mode=td_mode,
            side=close_side,
            sz=str(pos.size),
            ord_type="market",
        )

        if result.get("code") == "0":
            self.risk.close_position(pos, price)
        else:
            logger.error(f"平仓失败: {result.get('msg')}")


class SimulatedTrader(Trader):
    """
    模拟交易器 (不实际下单, 用于回测和验证)
    """

    def __init__(self, client: OKXClient, strategy: MultiSignalStrategy, risk_mgr: RiskManager):
        super().__init__(client, strategy, risk_mgr)
        self.trade_log = []

    def _try_open_positions(self, signal: TradeSignal):
        """模拟开仓 (不调用下单API)"""
        modes = ["spot", "margin_2x", "margin_3x"]

        for mode in modes:
            if mode == "spot" and signal.signal == Signal.SHORT:
                continue

            allowed, reason = self.risk.can_trade(mode)
            if not allowed:
                logger.debug(f"[SIM][{mode}] 风控拒绝: {reason}")
                continue

            size = self.risk.calc_position_size(mode, signal.price, signal.atr)
            if size <= 0:
                continue

            side = "buy" if signal.signal == Signal.LONG else "sell"

            sl = self.risk.calc_stop_loss(mode, signal.price, side, signal.atr)
            tp = self.risk.calc_take_profit(mode, signal.price, side)

            pos = Position(
                trade_mode=mode,
                side=side,
                entry_price=signal.price,
                size=size,
                stop_loss=sl,
                take_profit=tp,
                order_id=f"SIM_{int(time.time())}_{mode}",
                entry_time=time.time(),
            )
            self.risk.register_position(pos)

            self.trade_log.append({
                "time": signal.timestamp,
                "mode": mode,
                "side": side,
                "price": signal.price,
                "size": size,
                "sl": sl,
                "tp": tp,
                "signal_strength": signal.strength,
                "reason": signal.reason,
            })

            logger.info(
                f"[SIM][{mode}] 模拟开仓: {side} {size} BTC "
                f"@ {signal.price} | SL={sl} TP={tp}"
            )

    def _close_position(self, pos: Position, price: float, reason: str):
        """模拟平仓"""
        logger.info(f"[SIM][{pos.trade_mode}] 模拟平仓: {reason} @ {price}")
        self.risk.close_position(pos, price)
        self.trade_log.append({
            "time": time.time(),
            "mode": pos.trade_mode,
            "side": "close",
            "price": price,
            "size": pos.size,
            "pnl": pos.pnl,
            "reason": reason,
        })

    def print_summary(self):
        """打印模拟交易汇总"""
        status = self.risk.get_status()
        total_trades = len([t for t in self.trade_log if t.get("side") != "close"])
        wins = len([t for t in self.trade_log if t.get("pnl", 0) > 0])
        losses = len([t for t in self.trade_log if t.get("pnl", 0) < 0])

        print("\n" + "=" * 60)
        print("           模拟交易汇总")
        print("=" * 60)
        print(f"  最终净值:        ${status['equity']:.2f}")
        print(f"  总PnL:          ${status['daily_pnl']:+.4f}")
        print(f"  最大回撤:        {status['drawdown_pct']:.2f}%")
        print(f"  总交易次数:      {total_trades}")
        print(f"  盈利/亏损:       {wins}/{losses}")
        if total_trades > 0:
            win_rate = wins / total_trades * 100
            print(f"  胜率:            {win_rate:.1f}%")
        print("=" * 60)
