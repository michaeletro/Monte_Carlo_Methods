from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import time
from collections.abc import Mapping
from typing import Any

from .contracts import build_contract_from_payload
from .gateway import IBGateway
from .parsing import (
    coerce_bool,
    coerce_int,
    coerce_limit_price,
    coerce_market_data_type,
    coerce_quantity,
    default_account_summary_tags,
)


class WebSocketBridge:
    def __init__(self, gateway: IBGateway, listen_host: str, listen_port: int) -> None:
        self._gateway = gateway
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._clients: set[Any] = set()

    async def serve_forever(self) -> None:
        try:
            websockets = importlib.import_module("websockets")
        except ImportError as error:
            raise RuntimeError(
                "WebSocket mode requires the Python package 'websockets'. Install it with: "
                "python -m pip install websockets"
            ) from error

        self._loop = asyncio.get_running_loop()
        self._gateway.add_event_listener(self._enqueue_gateway_event)
        broadcaster = asyncio.create_task(self._broadcast_events())
        monitor = asyncio.create_task(self._monitor_gateway())

        try:
            async with websockets.serve(self._handle_client, self._listen_host, self._listen_port):
                self._gateway.emit_event(
                    "websocket.ready",
                    listenHost=self._listen_host,
                    listenPort=self._listen_port,
                )
                await asyncio.gather(self._wait_forever(), monitor)
        finally:
            self._gateway.remove_event_listener(self._enqueue_gateway_event)
            broadcaster.cancel()
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await broadcaster
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
            await self._close_clients()

    async def _wait_forever(self) -> None:
        await asyncio.Future()

    async def _monitor_gateway(self) -> None:
        while True:
            self._gateway.raise_if_failed()
            await asyncio.sleep(0.25)

    def _enqueue_gateway_event(self, event_payload: dict[str, Any]) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._event_queue.put_nowait, event_payload)

    async def _broadcast_events(self) -> None:
        while True:
            event_payload = await self._event_queue.get()
            if not self._clients:
                continue

            message = json.dumps(event_payload, default=IBGateway._json_default)
            stale_clients: list[Any] = []
            for client in tuple(self._clients):
                try:
                    await client.send(message)
                except Exception:
                    stale_clients.append(client)

            for client in stale_clients:
                self._clients.discard(client)

    async def _close_clients(self) -> None:
        for client in tuple(self._clients):
            with contextlib.suppress(Exception):
                await client.close()
        self._clients.clear()

    async def _handle_client(self, websocket: Any) -> None:
        self._clients.add(websocket)
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "websocket.connected",
                        "listenHost": self._listen_host,
                        "listenPort": self._listen_port,
                    }
                )
            )
            async for raw_message in websocket:
                await self._handle_message(websocket, raw_message)
        finally:
            self._clients.discard(websocket)

    async def _handle_message(self, websocket: Any, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError as error:
            await self._send_rejection(websocket, "invalid-json", str(error))
            return

        if not isinstance(payload, dict):
            await self._send_rejection(websocket, "invalid-payload", "message must be a JSON object")
            return

        command = str(payload.get("type", "")).strip()
        if not command:
            await self._send_rejection(websocket, "missing-type", "message.type is required")
            return

        try:
            response = self._dispatch_command(command, payload)
        except Exception as error:
            await websocket.send(
                json.dumps(
                    {
                        "type": "websocket.commandRejected",
                        "command": command,
                        "message": str(error),
                    }
                )
            )
            return

        await websocket.send(json.dumps(response, default=IBGateway._json_default))

    async def _send_rejection(self, websocket: Any, code: str, message: str) -> None:
        await websocket.send(json.dumps({"type": "websocket.commandRejected", "code": code, "message": message}))

    def _dispatch_command(self, command: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if command == "ping":
            return {"type": "pong", "time": time.time()}

        if command == "market-data.subscribe":
            request_id = self._gateway.request_market_data(
                build_contract_from_payload(payload),
                str(payload.get("genericTicks", "233")),
                coerce_bool(payload.get("snapshot", False), "snapshot"),
                coerce_bool(payload.get("regulatorySnapshot", False), "regulatorySnapshot"),
                coerce_market_data_type(payload.get("marketDataType", 1)),
            )
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "market-data.unsubscribe":
            request_id = coerce_int(payload.get("reqId"), "reqId")
            self._gateway.cancel_market_data_request(request_id)
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "historical.request":
            end_date_time = str(payload.get("endDateTime", ""))
            keep_up_to_date = coerce_bool(payload.get("keepUpToDate", False), "keepUpToDate")
            if keep_up_to_date and end_date_time:
                raise ValueError("IBKR does not allow endDateTime together with keepUpToDate=true")

            request_id = self._gateway.request_historical_data(
                build_contract_from_payload(payload),
                end_date_time,
                str(payload.get("duration", "1 D")),
                str(payload.get("barSize", "5 mins")),
                str(payload.get("whatToShow", "TRADES")).upper(),
                coerce_int(payload.get("useRTH", 1), "useRTH"),
                coerce_int(payload.get("formatDate", 1), "formatDate"),
                keep_up_to_date,
            )
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "historical.cancel":
            request_id = coerce_int(payload.get("reqId"), "reqId")
            self._gateway.cancel_historical_request(request_id)
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "contract-details.request":
            request_id = self._gateway.request_contract_details(build_contract_from_payload(payload))
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "option-chain.request":
            request_id = self._gateway.request_option_chain(
                str(payload.get("underlyingSymbol", "")).strip().upper(),
                str(payload.get("underlyingSecType", "STK")).strip().upper(),
                coerce_int(payload.get("underlyingConId"), "underlyingConId"),
                str(payload.get("futFopExchange", "")).strip().upper(),
            )
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "account-summary.request":
            request_id = self._gateway.request_account_summary(
                str(payload.get("groupName", "All")),
                str(payload.get("tags", default_account_summary_tags())),
            )
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "account-summary.cancel":
            request_id = coerce_int(payload.get("reqId"), "reqId")
            self._gateway.cancel_account_summary_request(request_id)
            return {"type": "websocket.commandAccepted", "command": command, "reqId": request_id}

        if command == "positions.request":
            self._gateway.request_positions()
            return {"type": "websocket.commandAccepted", "command": command}

        if command == "account-updates.subscribe":
            account = str(payload.get("account", "")).strip()
            if not account:
                raise ValueError("account is required")
            self._gateway.request_account_updates(account, True)
            return {"type": "websocket.commandAccepted", "command": command, "account": account}

        if command == "account-updates.unsubscribe":
            account = str(payload.get("account", "")).strip()
            if not account:
                raise ValueError("account is required")
            self._gateway.request_account_updates(account, False)
            return {"type": "websocket.commandAccepted", "command": command, "account": account}

        if command == "open-orders.request":
            all_clients = coerce_bool(payload.get("allClients", False), "allClients")
            self._gateway.request_open_orders(all_clients)
            return {"type": "websocket.commandAccepted", "command": command, "allClients": all_clients}

        if command == "order.place-limit":
            order_id = self._gateway.place_limit_order(
                build_contract_from_payload(payload),
                action=str(payload.get("action", "BUY")),
                quantity=coerce_quantity(payload.get("quantity")),
                order_type=str(payload.get("orderType", "LMT")),
                limit_price=coerce_limit_price(payload.get("limitPrice")),
                tif=str(payload.get("tif", "DAY")),
                transmit=coerce_bool(payload.get("transmit", False), "transmit"),
                account=str(payload.get("account", "")),
            )
            return {"type": "websocket.commandAccepted", "command": command, "orderId": order_id}

        if command == "order.cancel":
            order_id = coerce_int(payload.get("orderId"), "orderId")
            self._gateway.cancel_order_request(order_id)
            return {"type": "websocket.commandAccepted", "command": command, "orderId": order_id}

        raise ValueError(f"unsupported websocket command: {command}")