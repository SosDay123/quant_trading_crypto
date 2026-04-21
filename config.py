"""
配置管理模块
管理所有交易参数、API配置和风险控制参数
"""
import os
import logging
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO"))

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trading.log", encoding="utf-8"),
    ],
)


# ──────────────────────────────────────────────
# API 配置
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class OKXConfig:
    api_key: str = os.getenv("OKX_API_KEY", "")
    secret_key: str = os.getenv("OKX_SECRET_KEY", "")
    passphrase: str = os.getenv("OKX_PASSPHRASE", "")
    is_demo: bool = os.getenv("OKX_DEMO", "true").lower() == "true"

    # OKX API 端点
    base_url: str = "https://www.okx.com"
    ws_public: str = "wss://ws.okx.com:8443/ws/v5/public"
    ws_private: str = "wss://ws.okx.com:8443/ws/v5/private"

    # 模拟盘端点
    demo_base_url: str = "https://www.okx.com"
    demo_ws_public: str = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
    demo_ws_private: str = "wss://wspap.okx.com:8443/ws/v5/private?brokerId=9999"

    @property
    def active_base_url(self):
        return self.demo_base_url if self.is_demo else self.base_url

    @property
    def active_ws_public(self):
        return self.demo_ws_public if self.is_demo else self.ws_public

    @property
    def active_ws_private(self):
        return self.demo_ws_private if self.is_demo else self.ws_private

    @property
    def flag(self):
        """OKX API 的 simulated trading flag"""
        return "1" if self.is_demo else "0"


# ──────────────────────────────────────────────
# 交易对配置
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class PairConfig:
    inst_id: str = os.getenv("TRADE_PAIR", "BTC-USDT")
    candle_interval: str = os.getenv("CANDLE_INTERVAL", "15m")
    # OKX BTC-USDT 最小下单数量
    min_order_size: float = 0.00001  # BTC
    price_precision: int = 1  # 价格小数位
    size_precision: int = 5  # 数量小数位


# ──────────────────────────────────────────────
# 资金管理配置
# ──────────────────────────────────────────────
@dataclass
class CapitalConfig:
    total_capital: float = float(os.getenv("INITIAL_CAPITAL", "100"))

    # 三个账户的资金分配比例
    spot_ratio: float = 0.40      # 现货 40%
    margin_2x_ratio: float = 0.35  # 2x杠杆 35%
    margin_3x_ratio: float = 0.25  # 3x杠杆 25%

    @property
    def spot_capital(self):
        return self.total_capital * self.spot_ratio

    @property
    def margin_2x_capital(self):
        return self.total_capital * self.margin_2x_ratio

    @property
    def margin_3x_capital(self):
        return self.total_capital * self.margin_3x_ratio


# ──────────────────────────────────────────────
# 风险管理配置
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class RiskConfig:
    # 每种交易模式的单笔最大风险 (占该账户资金的百分比)
    spot_max_risk_pct: float = 0.02       # 现货: 2%
    margin_2x_max_risk_pct: float = 0.015  # 2x杠杆: 1.5%
    margin_3x_max_risk_pct: float = 0.01   # 3x杠杆: 1%

    # 止损百分比
    spot_stop_loss_pct: float = 0.03       # 现货止损 3%
    margin_2x_stop_loss_pct: float = 0.02  # 2x杠杆止损 2%
    margin_3x_stop_loss_pct: float = 0.015  # 3x杠杆止损 1.5%

    # 止盈百分比
    spot_take_profit_pct: float = 0.045     # 现货止盈 4.5%
    margin_2x_take_profit_pct: float = 0.03  # 2x杠杆止盈 3%
    margin_3x_take_profit_pct: float = 0.02  # 3x杠杆止盈 2%

    # 全局风险控制
    max_daily_loss_pct: float = 0.05   # 日最大亏损 5%
    max_drawdown_pct: float = 0.15     # 最大回撤 15%
    max_open_positions: int = 3         # 最大持仓数
    cooldown_after_loss: int = 2        # 连续亏损N次后冷却

    # 冷却期 (秒)
    cooldown_period: int = 3600  # 1小时


# ──────────────────────────────────────────────
# 策略参数配置
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class StrategyConfig:
    # EMA 参数
    ema_fast: int = 9
    ema_slow: int = 21
    ema_trend: int = 50

    # RSI 参数
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0

    # 布林带参数
    bb_period: int = 20
    bb_std_dev: float = 2.0

    # 成交量参数
    vol_ma_period: int = 20
    vol_surge_ratio: float = 1.5  # 量能放大倍数阈值

    # 信号综合权重
    ema_weight: float = 0.35
    rsi_weight: float = 0.25
    bb_weight: float = 0.25
    vol_weight: float = 0.15

    # 信号阈值 (综合得分达到此值才开仓)
    signal_threshold: float = 0.3

    # 3x 杠杆做空专用过滤
    margin_3x_short_threshold: float = 0.5
    margin_3x_trend_filter: bool = True

    # K线回溯数量
    lookback_candles: int = 100


# ──────────────────────────────────────────────
# 手续费配置
# ──────────────────────────────────────────────
@dataclass(frozen=True)
class FeeConfig:
    # OKX 普通用户费率 (Lv1)
    maker_fee: float = 0.0008    # 挂单 0.08%
    taker_fee: float = 0.001     # 吃单 0.10%
    # 市价单默认走 taker
    default_fee: float = 0.001   # 开仓 + 平仓各收一次
    # 是否启用手续费 (回测时可关闭对比)
    enabled: bool = True


# ──────────────────────────────────────────────
# 全局配置实例
# ──────────────────────────────────────────────
okx_cfg = OKXConfig()
pair_cfg = PairConfig()
capital_cfg = CapitalConfig()
risk_cfg = RiskConfig()
strategy_cfg = StrategyConfig()
fee_cfg = FeeConfig()
