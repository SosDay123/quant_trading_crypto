"""
技术指标计算模块
将原始K线数据转换为交易信号所需的技术指标
"""
import numpy as np
import pandas as pd
from config import strategy_cfg


def parse_candles_to_df(raw_candles: list) -> pd.DataFrame:
    """
    将 OKX K线原始数据转换为 DataFrame
    OKX 格式: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
    注意: OKX 返回的数据是倒序的 (最新在前)
    """
    if not raw_candles:
        return pd.DataFrame()

    df = pd.DataFrame(
        raw_candles,
        columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"],
    )
    # 类型转换
    for col in ["open", "high", "low", "close", "vol", "volCcy", "volCcyQuote"]:
        df[col] = df[col].astype(float)
    df["ts"] = pd.to_datetime(df["ts"].astype(int), unit="ms")
    df = df.sort_values("ts").reset_index(drop=True)  # 按时间正序排列
    return df


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均线"""
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """相对强弱指数 (RSI)"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0
) -> tuple:
    """
    布林带
    返回: (upper, middle, lower)
    """
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """平均真实波幅 (ATR) — 用于动态止损"""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_volume_ma(vol: pd.Series, period: int = 20) -> pd.Series:
    """成交量移动平均"""
    return vol.rolling(window=period).mean()


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算所有指标并附加到 DataFrame
    """
    if df.empty or len(df) < strategy_cfg.lookback_candles * 0.5:
        return df

    close = df["close"]
    vol = df["vol"]

    # EMA
    df["ema_fast"] = calc_ema(close, strategy_cfg.ema_fast)
    df["ema_slow"] = calc_ema(close, strategy_cfg.ema_slow)
    df["ema_trend"] = calc_ema(close, strategy_cfg.ema_trend)

    # RSI
    df["rsi"] = calc_rsi(close, strategy_cfg.rsi_period)

    # 布林带
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = calc_bollinger_bands(
        close, strategy_cfg.bb_period, strategy_cfg.bb_std_dev
    )

    # 布林带宽度百分比 (价格在布林带中的位置, 0~1)
    bb_range = df["bb_upper"] - df["bb_lower"]
    df["bb_pct"] = (close - df["bb_lower"]) / bb_range.replace(0, np.nan)

    # ATR
    df["atr"] = calc_atr(df, period=14)

    # 成交量
    df["vol_ma"] = calc_volume_ma(vol, strategy_cfg.vol_ma_period)
    df["vol_ratio"] = vol / df["vol_ma"].replace(0, np.nan)

    return df
