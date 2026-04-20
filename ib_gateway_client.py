from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from ib_gateway_app.parsing import default_account_summary_tags


class IBGatewayWebSocketClient:
    def __init__(self, url: str = "ws://127.0.0.1:8765") -> None:
        self._url = url
        self._websocket: Any | None = None

    async def connect(self) -> dict[str, Any]:
        self._websocket = await websockets.connect(self._url)
        return await self.recv()

    async def close(self) -> None:
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None

    async def recv(self) -> dict[str, Any]:
        if self._websocket is None:
            raise RuntimeError("websocket is not connected")
        return json.loads(await self._websocket.recv())

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self.recv()

    async def send(self, payload: dict[str, Any]) -> None:
        if self._websocket is None:
            raise RuntimeError("websocket is not connected")
        await self._websocket.send(json.dumps(payload))

    async def ping(self) -> None:
        await self.send({"type": "ping"})

    async def market_data_subscribe(
        self,
        symbol: str,
        *,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        primary_exchange: str = "",
        expiry: str = "",
        strike: float | None = None,
        right: str = "",
        multiplier: str = "",
        local_symbol: str = "",
        trading_class: str = "",
        con_id: int | None = None,
        generic_ticks: str = "233",
        snapshot: bool = False,
        regulatory_snapshot: bool = False,
        market_data_type: int = 1,
    ) -> None:
        payload = {
            "type": "market-data.subscribe",
            "symbol": symbol,
            "secType": sec_type,
            "exchange": exchange,
            "currency": currency,
            "primaryExchange": primary_exchange,
            "expiry": expiry,
            "right": right,
            "multiplier": multiplier,
            "localSymbol": local_symbol,
            "tradingClass": trading_class,
            "genericTicks": generic_ticks,
            "snapshot": snapshot,
            "regulatorySnapshot": regulatory_snapshot,
            "marketDataType": market_data_type,
        }
        if strike is not None:
            payload["strike"] = strike
        if con_id is not None:
            payload["conId"] = con_id
        await self.send(payload)

    async def market_data_unsubscribe(self, req_id: int) -> None:
        await self.send({"type": "market-data.unsubscribe", "reqId": req_id})

    async def historical_request(
        self,
        symbol: str,
        *,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        primary_exchange: str = "",
        expiry: str = "",
        strike: float | None = None,
        right: str = "",
        multiplier: str = "",
        local_symbol: str = "",
        trading_class: str = "",
        con_id: int | None = None,
        end_date_time: str = "",
        duration: str = "1 D",
        bar_size: str = "5 mins",
        what_to_show: str = "TRADES",
        use_rth: int = 1,
        format_date: int = 1,
        keep_up_to_date: bool = False,
    ) -> None:
        payload = {
            "type": "historical.request",
            "symbol": symbol,
            "secType": sec_type,
            "exchange": exchange,
            "currency": currency,
            "primaryExchange": primary_exchange,
            "expiry": expiry,
            "right": right,
            "multiplier": multiplier,
            "localSymbol": local_symbol,
            "tradingClass": trading_class,
            "endDateTime": end_date_time,
            "duration": duration,
            "barSize": bar_size,
            "whatToShow": what_to_show,
            "useRTH": use_rth,
            "formatDate": format_date,
            "keepUpToDate": keep_up_to_date,
        }
        if strike is not None:
            payload["strike"] = strike
        if con_id is not None:
            payload["conId"] = con_id
        await self.send(payload)

    async def historical_cancel(self, req_id: int) -> None:
        await self.send({"type": "historical.cancel", "reqId": req_id})

    async def contract_details_request(
        self,
        symbol: str,
        *,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        primary_exchange: str = "",
        expiry: str = "",
        strike: float | None = None,
        right: str = "",
        multiplier: str = "",
        local_symbol: str = "",
        trading_class: str = "",
        con_id: int | None = None,
    ) -> None:
        payload = {
            "type": "contract-details.request",
            "symbol": symbol,
            "secType": sec_type,
            "exchange": exchange,
            "currency": currency,
            "primaryExchange": primary_exchange,
            "expiry": expiry,
            "right": right,
            "multiplier": multiplier,
            "localSymbol": local_symbol,
            "tradingClass": trading_class,
        }
        if strike is not None:
            payload["strike"] = strike
        if con_id is not None:
            payload["conId"] = con_id
        await self.send(payload)

    async def option_chain_request(
        self,
        underlying_symbol: str,
        *,
        underlying_sec_type: str = "STK",
        underlying_con_id: int,
        fut_fop_exchange: str = "",
    ) -> None:
        await self.send(
            {
                "type": "option-chain.request",
                "underlyingSymbol": underlying_symbol,
                "underlyingSecType": underlying_sec_type,
                "underlyingConId": underlying_con_id,
                "futFopExchange": fut_fop_exchange,
            }
        )

    async def positions_request(self) -> None:
        await self.send({"type": "positions.request"})

    async def account_updates_subscribe(self, account: str) -> None:
        await self.send({"type": "account-updates.subscribe", "account": account})

    async def account_updates_unsubscribe(self, account: str) -> None:
        await self.send({"type": "account-updates.unsubscribe", "account": account})

    async def account_summary_request(self, *, group_name: str = "All", tags: str | None = None) -> None:
        await self.send(
            {
                "type": "account-summary.request",
                "groupName": group_name,
                "tags": tags or default_account_summary_tags(),
            }
        )

    async def account_summary_cancel(self, req_id: int) -> None:
        await self.send({"type": "account-summary.cancel", "reqId": req_id})

    async def open_orders_request(self, *, all_clients: bool = False) -> None:
        await self.send({"type": "open-orders.request", "allClients": all_clients})

    async def order_place_limit(
        self,
        symbol: str,
        *,
        action: str,
        quantity: str,
        limit_price: float,
        account: str,
        sec_type: str = "STK",
        exchange: str = "SMART",
        currency: str = "USD",
        primary_exchange: str = "",
        expiry: str = "",
        strike: float | None = None,
        right: str = "",
        multiplier: str = "",
        local_symbol: str = "",
        trading_class: str = "",
        con_id: int | None = None,
        order_type: str = "LMT",
        tif: str = "DAY",
        transmit: bool = False,
    ) -> None:
        payload = {
            "type": "order.place-limit",
            "symbol": symbol,
            "secType": sec_type,
            "exchange": exchange,
            "currency": currency,
            "primaryExchange": primary_exchange,
            "expiry": expiry,
            "right": right,
            "multiplier": multiplier,
            "localSymbol": local_symbol,
            "tradingClass": trading_class,
            "action": action,
            "quantity": quantity,
            "orderType": order_type,
            "limitPrice": limit_price,
            "tif": tif,
            "transmit": transmit,
            "account": account,
        }
        if strike is not None:
            payload["strike"] = strike
        if con_id is not None:
            payload["conId"] = con_id
        await self.send(payload)

    async def order_cancel(self, order_id: int) -> None:
        await self.send({"type": "order.cancel", "orderId": order_id})


async def _demo() -> None:
    client = IBGatewayWebSocketClient()
    print(await client.connect())
    await client.ping()
    print(await client.recv())
    await client.close()


if __name__ == "__main__":
    asyncio.run(_demo())