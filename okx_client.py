"""
OKX API 客户端封装
处理认证签名、REST请求和WebSocket连接
"""
import hmac
import hashlib
import base64
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from config import okx_cfg, pair_cfg

logger = logging.getLogger(__name__)


class OKXClient:
    """OKX REST API 客户端"""

    def __init__(self):
        self.api_key = okx_cfg.api_key
        self.secret_key = okx_cfg.secret_key
        self.passphrase = okx_cfg.passphrase
        self.base_url = okx_cfg.active_base_url
        self.flag = okx_cfg.flag
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ── 签名 ──────────────────────────────────
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """生成 OKX API 签名"""
        message = timestamp + method.upper() + path + body
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _get_timestamp(self) -> str:
        """ISO 8601 UTC 时间戳"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = self._get_timestamp()
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "x-simulated-trading": self.flag,
        }

    # ── 通用请求 ──────────────────────────────
    def _request(self, method: str, path: str, params: dict = None, data: dict = None):
        url = self.base_url + path
        body = json.dumps(data) if data else ""

        headers = self._headers(method, path, body)
        self.session.headers.update(headers)

        try:
            if method == "GET":
                resp = self.session.get(url, params=params, timeout=10)
            else:
                resp = self.session.post(url, data=body, timeout=10)

            result = resp.json()

            if result.get("code") != "0":
                logger.error(f"API 错误: {result.get('msg')} (code={result.get('code')})")

            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"请求异常: {e}")
            return {"code": "-1", "msg": str(e), "data": []}

    def get(self, path: str, params: dict = None):
        return self._request("GET", path, params=params)

    def post(self, path: str, data: dict = None):
        return self._request("POST", path, data=data)

    # ── 市场数据 ──────────────────────────────
    def get_ticker(self, inst_id: str = None) -> dict:
        """获取最新行情"""
        inst_id = inst_id or pair_cfg.inst_id
        result = self.get("/api/v5/market/ticker", {"instId": inst_id})
        if result["code"] == "0" and result["data"]:
            return result["data"][0]
        return {}

    def get_candles(
        self,
        inst_id: str = None,
        bar: str = None,
        limit: int = 100,
    ) -> list:
        """
        获取K线数据
        返回: [[ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm], ...]
        """
        inst_id = inst_id or pair_cfg.inst_id
        bar = bar or pair_cfg.candle_interval
        result = self.get(
            "/api/v5/market/candles",
            {"instId": inst_id, "bar": bar, "limit": str(limit)},
        )
        if result["code"] == "0":
            return result["data"]
        return []

    def get_order_book(self, inst_id: str = None, depth: int = 5) -> dict:
        """获取深度数据"""
        inst_id = inst_id or pair_cfg.inst_id
        result = self.get(
            "/api/v5/market/books", {"instId": inst_id, "sz": str(depth)}
        )
        if result["code"] == "0" and result["data"]:
            return result["data"][0]
        return {}

    # ── 账户信息 ──────────────────────────────
    def get_balance(self, ccy: str = "USDT") -> dict:
        """查询账户余额"""
        result = self.get("/api/v5/account/balance", {"ccy": ccy})
        if result["code"] == "0" and result["data"]:
            return result["data"][0]
        return {}

    def get_positions(self, inst_id: str = None) -> list:
        """查询持仓"""
        params = {}
        if inst_id:
            params["instId"] = inst_id
        result = self.get("/api/v5/account/positions", params)
        if result["code"] == "0":
            return result["data"]
        return []

    def set_leverage(self, inst_id: str, lever: int, mgn_mode: str = "cross"):
        """
        设置杠杆倍数
        mgn_mode: cross=全仓, isolated=逐仓
        """
        data = {
            "instId": inst_id,
            "lever": str(lever),
            "mgnMode": mgn_mode,
        }
        return self.post("/api/v5/account/set-leverage", data)

    def set_account_mode(self, acct_lv: str = "2"):
        """
        设置账户模式
        1=简单交易, 2=单币种保证金, 3=多币种保证金, 4=组合保证金
        """
        return self.post("/api/v5/account/set-account-level", {"acctLv": acct_lv})

    # ── 下单接口 ──────────────────────────────
    def place_order(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        sz: str,
        ord_type: str = "market",
        px: str = None,
        sl_trigger_px: str = None,
        sl_ord_px: str = None,
        tp_trigger_px: str = None,
        tp_ord_px: str = None,
    ) -> dict:
        """
        下单
        td_mode: cash=现货, cross=全仓杠杆, isolated=逐仓杠杆
        side: buy / sell
        ord_type: market / limit / post_only / fok / ioc
        """
        data = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": sz,
        }
        if px:
            data["px"] = px
        if sl_trigger_px:
            data["slTriggerPx"] = sl_trigger_px
            data["slOrdPx"] = sl_ord_px or "-1"  # -1 = 市价止损
        if tp_trigger_px:
            data["tpTriggerPx"] = tp_trigger_px
            data["tpOrdPx"] = tp_ord_px or "-1"

        result = self.post("/api/v5/trade/order", data)
        if result["code"] == "0" and result["data"]:
            order_id = result["data"][0].get("ordId", "")
            logger.info(f"下单成功: {side} {sz} {inst_id} | ordId={order_id}")
        else:
            logger.error(f"下单失败: {result.get('msg')}")
        return result

    def cancel_order(self, inst_id: str, ord_id: str) -> dict:
        """撤单"""
        return self.post(
            "/api/v5/trade/cancel-order",
            {"instId": inst_id, "ordId": ord_id},
        )

    def get_order(self, inst_id: str, ord_id: str) -> dict:
        """查询订单详情"""
        result = self.get(
            "/api/v5/trade/order",
            {"instId": inst_id, "ordId": ord_id},
        )
        if result["code"] == "0" and result["data"]:
            return result["data"][0]
        return {}

    def get_pending_orders(self, inst_id: str = None) -> list:
        """查询未完成订单"""
        params = {}
        if inst_id:
            params["instId"] = inst_id
        result = self.get("/api/v5/trade/orders-pending", params)
        if result["code"] == "0":
            return result["data"]
        return []

    # ── 策略委托（止盈止损单） ──────────────────
    def place_algo_order(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        sz: str,
        order_type: str = "conditional",
        tp_trigger_px: str = None,
        tp_ord_px: str = None,
        sl_trigger_px: str = None,
        sl_ord_px: str = None,
    ) -> dict:
        """
        策略委托下单 (止盈止损)
        order_type: conditional / oco / trigger
        """
        data = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": order_type,
            "sz": sz,
        }
        if tp_trigger_px:
            data["tpTriggerPx"] = tp_trigger_px
            data["tpOrdPx"] = tp_ord_px or "-1"
        if sl_trigger_px:
            data["slTriggerPx"] = sl_trigger_px
            data["slOrdPx"] = sl_ord_px or "-1"

        result = self.post("/api/v5/trade/order-algo", data)
        if result["code"] == "0":
            logger.info(f"策略委托成功: {side} {sz} {inst_id}")
        return result
