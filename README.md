# OKX BTC/USDT 量化交易系统

## 概述
基于 OKX 交易所 API 的 BTC/USDT 量化交易程序，支持：
- **现货交易** (Spot)
- **2倍杠杆交易** (2x Margin)
- **3倍杠杆交易** (3x Margin)

初始资金：100 USDT

## 策略说明
采用 **多信号融合策略**，结合以下技术指标：
1. **EMA 交叉** (快线 9 / 慢线 21) — 趋势方向
2. **RSI** (14周期) — 超买超卖过滤
3. **布林带** (20周期, 2σ) — 波动率与价格位置
4. **成交量确认** — 量价配合验证

## 资金分配
| 账户类型 | 分配比例 | 金额  | 最大单笔风险 |
|---------|---------|------|------------|
| 现货     | 40%     | $40  | 2%         |
| 2x杠杆  | 35%     | $35  | 1.5%       |
| 3x杠杆  | 25%     | $25  | 1%         |

## 安装

```bash
# 1. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API 密钥
cp .env.example .env
# 编辑 .env 填入你的 OKX API 密钥

# 4. 运行（模拟模式）
python main.py --mode simulation

# 5. 运行（实盘模式）⚠️ 请先充分测试
python main.py --mode live
```

## 项目结构
```
quant_trading/
├── config.py          # 配置管理
├── okx_client.py      # OKX API 封装
├── indicators.py      # 技术指标计算
├── strategy.py        # 交易策略
├── risk_manager.py    # 风险管理
├── trader.py          # 交易执行引擎
├── main.py            # 主程序入口
├── requirements.txt   # 依赖包
├── .env.example       # 环境变量模板
└── README.md
```

## ⚠️ 风险提示
- 量化交易存在亏损风险，杠杆交易风险更大
- 请务必先用模拟模式充分测试
- 100 美金为小额资金，注意 OKX 最小下单量限制
- 建议先在 OKX 模拟盘 (demo trading) 环境测试
