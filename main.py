"""
OKX BTC/USDT 量化交易系统 — 主程序入口

用法:
    python main.py --mode simulation    # 模拟模式 (获取真实行情, 但不实际下单)
    python main.py --mode live          # 实盘模式 (⚠️ 真实交易)
    python main.py --mode backtest      # 回测模式 (用历史K线快速回测)
"""
import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from config import (
    okx_cfg, pair_cfg, capital_cfg, risk_cfg, strategy_cfg,
)
from okx_client import OKXClient
from strategy import MultiSignalStrategy
from risk_manager import RiskManager
from trader import Trader, SimulatedTrader

logger = logging.getLogger("main")

# ── 全局控制 ──────────────────────────────────
RUNNING = True


def signal_handler(sig, frame):
    global RUNNING
    logger.info("收到终止信号，正在安全退出...")
    RUNNING = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── 打印配置 ──────────────────────────────────
def print_config():
    mode_label = "🟡 模拟盘" if okx_cfg.is_demo else "🔴 实盘"
    print(f"""
╔══════════════════════════════════════════════════════════╗
║       OKX BTC/USDT 量化交易系统                          ║
╠══════════════════════════════════════════════════════════╣
║  交易对:    {pair_cfg.inst_id:<20}  K线周期: {pair_cfg.candle_interval:<8}║
║  环境:      {mode_label:<42}║
╠══════════════════════════════════════════════════════════╣
║  资金分配:                                               ║
║    现货:     ${capital_cfg.spot_capital:>6.1f}  ({capital_cfg.spot_ratio:.0%})    止损: {risk_cfg.spot_stop_loss_pct:.1%}      ║
║    2x杠杆:  ${capital_cfg.margin_2x_capital:>6.1f}  ({capital_cfg.margin_2x_ratio:.0%})    止损: {risk_cfg.margin_2x_stop_loss_pct:.1%}      ║
║    3x杠杆:  ${capital_cfg.margin_3x_capital:>6.1f}  ({capital_cfg.margin_3x_ratio:.0%})    止损: {risk_cfg.margin_3x_stop_loss_pct:.1%}      ║
║    合计:     ${capital_cfg.total_capital:>6.1f}                               ║
╠══════════════════════════════════════════════════════════╣
║  策略参数:                                               ║
║    EMA:   {strategy_cfg.ema_fast}/{strategy_cfg.ema_slow}          RSI: {strategy_cfg.rsi_period}期                ║
║    布林带: {strategy_cfg.bb_period}期/{strategy_cfg.bb_std_dev}σ      信号阈值: {strategy_cfg.signal_threshold}           ║
║    日最大亏损: {risk_cfg.max_daily_loss_pct:.0%}       最大回撤: {risk_cfg.max_drawdown_pct:.0%}              ║
╚══════════════════════════════════════════════════════════╝
""")


# ── K线间隔转秒数 ──────────────────────────────
INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400,
    "1D": 86400,
}


# ── 模拟/实盘运行 ─────────────────────────────
def run_trading(mode: str):
    """持续运行交易循环"""
    client = OKXClient()
    strategy = MultiSignalStrategy()
    risk_mgr = RiskManager()

    if mode == "simulation":
        trader = SimulatedTrader(client, strategy, risk_mgr)
        logger.info(">>> 模拟模式启动 — 获取真实行情但不下单 <<<")
    else:
        trader = Trader(client, strategy, risk_mgr)
        logger.info(">>> ⚠️  实盘模式启动 — 将进行真实交易 <<<")
        if okx_cfg.is_demo:
            logger.info("当前连接 OKX 模拟盘环境")
        else:
            logger.warning("当前连接 OKX 真实环境！请确认资金充足！")

    # 初始化 (设置杠杆等)
    if mode == "live":
        trader.setup()

    interval = INTERVAL_SECONDS.get(pair_cfg.candle_interval, 900)
    cycle_count = 0
    last_daily_reset = datetime.now(timezone.utc).date()

    global RUNNING
    while RUNNING:
        try:
            now = datetime.now(timezone.utc)

            # 日结重置
            if now.date() != last_daily_reset:
                risk_mgr.reset_daily()
                last_daily_reset = now.date()

            cycle_count += 1
            logger.info(f"── 第 {cycle_count} 轮 | {now.strftime('%H:%M:%S')} UTC ──")

            trader.execute_cycle()

            # 等待到下一根K线
            logger.info(f"等待 {interval}s 到下一根K线...")
            for _ in range(interval):
                if not RUNNING:
                    break
                time.sleep(1)

        except Exception as e:
            logger.exception(f"交易循环异常: {e}")
            time.sleep(30)

    # 退出时打印汇总
    if isinstance(trader, SimulatedTrader):
        trader.print_summary()

    logger.info("程序已安全退出")


# ── 回测模式 ──────────────────────────────────
def run_backtest():
    """
    简单回测: 获取历史K线，逐根遍历模拟交易
    """
    logger.info(">>> 回测模式启动 <<<")

    client = OKXClient()
    strategy = MultiSignalStrategy()
    risk_mgr = RiskManager()
    trader = SimulatedTrader(client, strategy, risk_mgr)

    # 获取尽可能多的历史K线 (OKX 限制单次300根)
    all_candles = client.get_candles(limit=300)
    if not all_candles:
        logger.error("获取历史数据失败")
        return

    logger.info(f"获取到 {len(all_candles)} 根K线")

    # OKX 返回数据是倒序的 (最新在前)，翻转为正序
    all_candles.reverse()

    # 滑动窗口回测
    window_size = strategy_cfg.lookback_candles
    for i in range(window_size, len(all_candles)):
        window = all_candles[i - window_size : i]
        # 翻转回 OKX 的倒序格式 (strategy 内部会再翻转)
        window_reversed = window[::-1]

        signal = strategy.generate_signal(window_reversed)

        # 检查已有仓位
        current_price = float(window[-1][4])  # close price
        positions_copy = list(risk_mgr.state.open_positions)
        for pos in positions_copy:
            if pos.side == "buy":
                if current_price <= pos.stop_loss:
                    trader._close_position(pos, current_price, "止损")
                elif current_price >= pos.take_profit:
                    trader._close_position(pos, current_price, "止盈")
            else:
                if current_price >= pos.stop_loss:
                    trader._close_position(pos, current_price, "止损")
                elif current_price <= pos.take_profit:
                    trader._close_position(pos, current_price, "止盈")

        # 尝试开仓
        from strategy import Signal as Sig
        if signal.signal in (Sig.LONG, Sig.SHORT):
            trader._try_open_positions(signal)

    # 强制平掉所有剩余仓位
    if risk_mgr.state.open_positions:
        last_price = float(all_candles[-1][4])
        for pos in list(risk_mgr.state.open_positions):
            trader._close_position(pos, last_price, "回测结束强平")

    trader.print_summary()


# ── 入口 ──────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="OKX BTC/USDT 量化交易系统")
    parser.add_argument(
        "--mode",
        choices=["simulation", "live", "backtest"],
        default="simulation",
        help="运行模式: simulation=模拟, live=实盘, backtest=回测",
    )
    args = parser.parse_args()

    print_config()

    if args.mode == "backtest":
        run_backtest()
    else:
        run_trading(args.mode)


if __name__ == "__main__":
    main()
