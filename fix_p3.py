#!/usr/bin/env python3
"""Fix P3: raise short signal threshold to 0.5 for all modes"""

# ── Fix backtest.py ──
filepath = '/root/quant_trading_v2/backtest.py'
with open(filepath, 'r') as f:
    content = f.read()

# 在趋势过滤之后、开仓循环之前，加入做空阈值过滤
old = '''            # ── 趋势过滤: 顺势交易 ──
            if signal == Signal.LONG and trend_is_down:
                continue  # 下跌趋势禁止做多
            if signal == Signal.SHORT and trend_is_up:
                continue  # 上涨趋势禁止做空

            # ── 5. 尝试开仓 ──'''

new = '''            # ── 趋势过滤: 顺势交易 ──
            if signal == Signal.LONG and trend_is_down:
                continue  # 下跌趋势禁止做多
            if signal == Signal.SHORT and trend_is_up:
                continue  # 上涨趋势禁止做空

            # ── 做空信号质量过滤: 只做高确定性做空 ──
            if signal == Signal.SHORT and abs(score) < 0.5:
                continue

            # ── 5. 尝试开仓 ──'''

content = content.replace(old, new)

with open(filepath, 'w') as f:
    f.write(content)
print("Fixed backtest.py: short threshold raised to 0.5")

# ── Fix trader.py ──
filepath2 = '/root/quant_trading_v2/trader.py'
with open(filepath2, 'r') as f:
    content2 = f.read()

# 在趋势过滤之后加入做空阈值过滤
old2 = '''        # 趋势过滤: 下跌趋势禁止做多, 上涨趋势禁止做空
        if signal.signal == Signal.LONG and trend_is_down:
            logger.info(f"趋势过滤: 价格在EMA50下方, 禁止做多")
            return
        if signal.signal == Signal.SHORT and trend_is_up:
            logger.info(f"趋势过滤: 价格在EMA50上方, 禁止做空")
            return

        for mode in modes:'''

new2 = '''        # 趋势过滤: 下跌趋势禁止做多, 上涨趋势禁止做空
        if signal.signal == Signal.LONG and trend_is_down:
            logger.info(f"趋势过滤: 价格在EMA50下方, 禁止做多")
            return
        if signal.signal == Signal.SHORT and trend_is_up:
            logger.info(f"趋势过滤: 价格在EMA50上方, 禁止做空")
            return

        # 做空信号质量过滤: 只做高确定性做空 (强度>=0.5)
        if signal.signal == Signal.SHORT and signal.strength < 0.5:
            logger.info(f"做空过滤: 信号强度 {signal.strength:.3f} < 0.5, 跳过")
            return

        for mode in modes:'''

content2 = content2.replace(old2, new2)

with open(filepath2, 'w') as f:
    f.write(content2)
print("Fixed trader.py: short threshold raised to 0.5")
