#!/usr/bin/env python3
"""Fix P0: daily loss limit should reset each day, not be permanent"""

# ── Fix 1: backtest.py ──
filepath = '/root/quant_trading_v2/backtest.py'
with open(filepath, 'r') as f:
    content = f.read()

# Replace the initialization section to add day tracking
old_init = '''        equity = self.initial_capital
        peak_equity = equity
        open_positions: dict[str, BacktestTrade] = {}  # mode -> trade
        consecutive_losses = 0
        cooldown_until_bar = -1

        window = strategy_cfg.lookback_candles'''

new_init = '''        equity = self.initial_capital
        peak_equity = equity
        open_positions: dict[str, BacktestTrade] = {}  # mode -> trade
        consecutive_losses = 0
        cooldown_until_bar = -1
        day_start_equity = equity  # 每日起始净值，用于日亏损限额
        current_day = None         # 当前交易日

        window = strategy_cfg.lookback_candles'''

content = content.replace(old_init, new_init)

# Replace the daily loss check to include day reset logic
old_check = '''            # ── 3. 风控熔断检查 ──
            if i < cooldown_until_bar:
                continue

            daily_loss_limit = self.initial_capital * risk_cfg.max_daily_loss_pct
            if equity < self.initial_capital - daily_loss_limit:
                continue'''

new_check = '''            # ── 3. 风控熔断检查 ──
            if i < cooldown_until_bar:
                continue

            # 日亏损限额：按天重置
            bar_ts = row.get("ts", None)
            if bar_ts is not None and hasattr(bar_ts, 'date'):
                bar_day = bar_ts.date()
            else:
                bar_day = i // 96  # 15分钟K线，96根=1天
            if bar_day != current_day:
                current_day = bar_day
                day_start_equity = equity  # 新的一天，重置起始净值
            daily_loss_limit = day_start_equity * risk_cfg.max_daily_loss_pct
            if equity < day_start_equity - daily_loss_limit:
                continue'''

content = content.replace(old_check, new_check)

with open(filepath, 'w') as f:
    f.write(content)
print("Fixed backtest.py: daily loss limit now resets each day")

# ── Fix 2: trader.py (if it has the same issue) ──
filepath2 = '/root/quant_trading_v2/trader.py'
with open(filepath2, 'r') as f:
    content2 = f.read()

# Check if trader.py has similar daily loss logic
if 'max_daily_loss_pct' in content2:
    # Look for the pattern in trader.py
    # The trader likely tracks daily_pnl differently, let's check
    print(f"\ntrader.py also uses max_daily_loss_pct, checking...")

    # Common pattern: daily_pnl tracking
    if 'daily_pnl' in content2 or 'day_start' in content2:
        print("  trader.py already has daily tracking, skipping")
    else:
        # Find and fix the daily loss check in trader.py
        # First let's see what's there
        lines = content2.split('\n')
        for i, line in enumerate(lines):
            if 'max_daily_loss' in line.lower() or 'daily_loss' in line.lower():
                print(f"  Line {i+1}: {line.strip()}")
else:
    print("\ntrader.py doesn't use max_daily_loss_pct directly")

print("\nDone!")
