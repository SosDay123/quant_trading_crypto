#!/usr/bin/env python3
"""Fix P1: Add trend filter - no longs below EMA50, no shorts above EMA50"""

# ── Fix 1: backtest.py ──
filepath = '/root/quant_trading_v2/backtest.py'
with open(filepath, 'r') as f:
    content = f.read()

old = '''            ema_trend_val = row.get("ema_trend", None)
            trend_is_down = (ema_trend_val is not None) and (not pd.isna(ema_trend_val)) and (price < ema_trend_val)

            # ── 5. 尝试开仓 ──
            for mode, params in self.modes.items():
                if mode in open_positions:
                    continue
                if signal == Signal.SHORT and not params["can_short"]:
                    continue
                # 3x 做空专用过滤: 更高阈值 + 趋势向下
                if mode == "margin_3x" and signal == Signal.SHORT:
                    if abs(score) < strategy_cfg.margin_3x_short_threshold:
                        continue
                    if strategy_cfg.margin_3x_trend_filter and not trend_is_down:
                        continue'''

new = '''            ema_trend_val = row.get("ema_trend", None)
            has_trend = (ema_trend_val is not None) and (not pd.isna(ema_trend_val))
            trend_is_down = has_trend and (price < ema_trend_val)
            trend_is_up = has_trend and (price > ema_trend_val)

            # ── 趋势过滤: 顺势交易 ──
            if signal == Signal.LONG and trend_is_down:
                continue  # 下跌趋势禁止做多
            if signal == Signal.SHORT and trend_is_up:
                continue  # 上涨趋势禁止做空

            # ── 5. 尝试开仓 ──
            for mode, params in self.modes.items():
                if mode in open_positions:
                    continue
                if signal == Signal.SHORT and not params["can_short"]:
                    continue
                # 3x 做空专用过滤: 更高阈值
                if mode == "margin_3x" and signal == Signal.SHORT:
                    if abs(score) < strategy_cfg.margin_3x_short_threshold:
                        continue'''

content = content.replace(old, new)

with open(filepath, 'w') as f:
    f.write(content)
print("Fixed backtest.py: added trend filter for all modes")

# ── Fix 2: trader.py ──
filepath2 = '/root/quant_trading_v2/trader.py'
with open(filepath2, 'r') as f:
    content2 = f.read()

old2 = '''        # 长周期趋势判断 (3x 做空过滤用)
        trend_is_down = False
        try:
            candles = self.client.get_candles(limit=100)
            from indicators import parse_candles_to_df, compute_all_indicators
            df_t = compute_all_indicators(parse_candles_to_df(candles))
            if len(df_t) > 0:
                ema_t = df_t.iloc[-1].get("ema_trend")
                close_t = df_t.iloc[-1].get("close")
                if ema_t and close_t and close_t < ema_t:
                    trend_is_down = True
        except Exception as e:
            logger.warning(f"趋势判断失败: {e}")

        for mode in modes:
            if mode == "spot" and signal.signal == Signal.SHORT:
                continue

            # 3x 做空专用过滤
            if mode == "margin_3x" and signal.signal == Signal.SHORT:
                if signal.strength < strategy_cfg.margin_3x_short_threshold:
                    logger.info(f"[3x] 做空信号强度 {signal.strength:.3f} 不足, 跳过")
                    continue
                if strategy_cfg.margin_3x_trend_filter and not trend_is_down:
                    logger.info(f"[3x] 长周期趋势向上, 禁止做空")
                    continue'''

new2 = '''        # 长周期趋势判断 (顺势交易过滤)
        trend_is_down = False
        trend_is_up = False
        try:
            candles = self.client.get_candles(limit=100)
            from indicators import parse_candles_to_df, compute_all_indicators
            df_t = compute_all_indicators(parse_candles_to_df(candles))
            if len(df_t) > 0:
                ema_t = df_t.iloc[-1].get("ema_trend")
                close_t = df_t.iloc[-1].get("close")
                if ema_t and close_t:
                    if close_t < ema_t:
                        trend_is_down = True
                    elif close_t > ema_t:
                        trend_is_up = True
        except Exception as e:
            logger.warning(f"趋势判断失败: {e}")

        # 趋势过滤: 下跌趋势禁止做多, 上涨趋势禁止做空
        if signal.signal == Signal.LONG and trend_is_down:
            logger.info(f"趋势过滤: 价格在EMA50下方, 禁止做多")
            return
        if signal.signal == Signal.SHORT and trend_is_up:
            logger.info(f"趋势过滤: 价格在EMA50上方, 禁止做空")
            return

        for mode in modes:
            if mode == "spot" and signal.signal == Signal.SHORT:
                continue

            # 3x 做空专用过滤: 更高阈值
            if mode == "margin_3x" and signal.signal == Signal.SHORT:
                if signal.strength < strategy_cfg.margin_3x_short_threshold:
                    logger.info(f"[3x] 做空信号强度 {signal.strength:.3f} 不足, 跳过")
                    continue'''

content2 = content2.replace(old2, new2)

with open(filepath2, 'w') as f:
    f.write(content2)
print("Fixed trader.py: added trend filter for live trading")

print("\nDone! Changes:")
print("  - backtest.py: LONG blocked when price < EMA50, SHORT blocked when price > EMA50")
print("  - trader.py: same trend filter applied to live trading")
