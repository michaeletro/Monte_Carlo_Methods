from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.ticktype import TickTypeEnum
from ibapi.wrapper import EWrapper


INFORMATIONAL_ERROR_CODES = {2104, 2106, 2107, 2108, 2158}
CONNECTION_ERROR_CODES = {502, 503, 504, 507, 1100, 1300}


class IBGateway(EWrapper, EClient):
    def __init__(self, log_file: str | None = None) -> None:
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)
        self._state_lock = threading.Lock()
        self._network_thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._failure_event = threading.Event()
        self._positions_event = threading.Event()
        self._open_orders_event = threading.Event()
        self._account_download_event = threading.Event()
        self._contract_details_events: dict[int, threading.Event] = {}
        self._account_summary_events: dict[int, threading.Event] = {}
        self._historical_events: dict[int, threading.Event] = {}
        self._option_chain_events: dict[int, threading.Event] = {}
        self._managed_accounts = ""
        self._next_request_id = 1000
        self._next_order_id: int | None = None
        self._last_error_code: int | None = None
        self._last_error_message = ""
        self._event_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._log_handle = None

        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = log_path.open("a", encoding="utf-8")

    def connect_and_start(self, host: str, port: int, client_id: int, timeout_seconds: int) -> None:
        self._ready_event.clear()
        self._failure_event.clear()
        self._positions_event.clear()
        self._open_orders_event.clear()
        self._account_download_event.clear()
        with self._state_lock:
            self._last_error_code = None
            self._last_error_message = ""

        self.connect(host, port, client_id)
        self._network_thread = threading.Thread(target=self.run, daemon=True)
        self._network_thread.start()

        deadline = time.monotonic() + timeout_seconds
        while True:
            self.raise_if_failed()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Timed out waiting for IBKR connection readiness. Check host, port, client id, and TWS or IB Gateway API settings."
                )

            if self._ready_event.wait(min(0.1, remaining)):
                self.raise_if_failed()
                return

    def shutdown(self) -> None:
        self._release_waiters()
        if self.isConnected():
            self.disconnect()
        if self._network_thread is not None and self._network_thread.is_alive():
            self._network_thread.join(timeout=5)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def acquire_request_id(self) -> int:
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
        return request_id

    def wait_for_historical(self, request_id: int, timeout_seconds: int) -> bool:
        with self._state_lock:
            event = self._historical_events.setdefault(request_id, threading.Event())
        return self._wait_for_event(event, timeout_seconds)

    def wait_for_positions(self, timeout_seconds: int) -> bool:
        return self._wait_for_event(self._positions_event, timeout_seconds)

    def wait_for_open_orders(self, timeout_seconds: int) -> bool:
        return self._wait_for_event(self._open_orders_event, timeout_seconds)

    def wait_for_account_download(self, timeout_seconds: int) -> bool:
        return self._wait_for_event(self._account_download_event, timeout_seconds)

    def wait_for_contract_details(self, request_id: int, timeout_seconds: int) -> bool:
        with self._state_lock:
            event = self._contract_details_events.setdefault(request_id, threading.Event())
        return self._wait_for_event(event, timeout_seconds)

    def wait_for_account_summary(self, request_id: int, timeout_seconds: int) -> bool:
        with self._state_lock:
            event = self._account_summary_events.setdefault(request_id, threading.Event())
        return self._wait_for_event(event, timeout_seconds)

    def wait_for_option_chain(self, request_id: int, timeout_seconds: int) -> bool:
        with self._state_lock:
            event = self._option_chain_events.setdefault(request_id, threading.Event())
        return self._wait_for_event(event, timeout_seconds)

    def ensure_ready(self) -> None:
        self.raise_if_failed()
        if not self._ready_event.is_set():
            raise RuntimeError("IBKR connection is not ready")

    def raise_if_failed(self) -> None:
        if not self._failure_event.is_set():
            return

        with self._state_lock:
            error_code = self._last_error_code
            error_message = self._last_error_message or "Interactive Brokers connection failed"

        if error_code is None:
            raise ConnectionError(error_message)
        raise ConnectionError(f"IBKR connection failed ({error_code}): {error_message}")

    def sleep_while_connected(self, seconds: int) -> None:
        deadline = time.monotonic() + max(seconds, 0)
        while True:
            self.raise_if_failed()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return

            time.sleep(min(0.1, remaining))

    def _wait_for_event(self, event: threading.Event, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + max(timeout_seconds, 0)
        while True:
            self.raise_if_failed()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return event.is_set()

            if event.wait(min(0.1, remaining)):
                self.raise_if_failed()
                return True

    def _set_failure(self, error_code: int | None, error_message: str) -> None:
        with self._state_lock:
            if self._failure_event.is_set():
                return
            self._last_error_code = error_code
            self._last_error_message = error_message

        self._failure_event.set()
        self._release_waiters()

    def request_market_data(
        self,
        contract: Contract,
        generic_ticks: str,
        snapshot: bool,
        regulatory_snapshot: bool,
        market_data_type: int,
    ) -> int:
        self.ensure_ready()
        request_id = self.acquire_request_id()
        self.reqMarketDataType(market_data_type)
        self.emit_event("request.marketDataType", marketDataType=market_data_type)
        self.reqMktData(request_id, contract, generic_ticks, snapshot, regulatory_snapshot, [])
        self.emit_event(
            "request.marketData",
            reqId=request_id,
            symbol=contract.symbol,
            secType=contract.secType,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            expiry=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            conId=contract.conId,
            genericTicks=generic_ticks,
            snapshot=snapshot,
            regulatorySnapshot=regulatory_snapshot,
            marketDataType=market_data_type,
        )
        return request_id

    def request_historical_data(
        self,
        contract: Contract,
        end_date_time: str,
        duration: str,
        bar_size: str,
        what_to_show: str,
        use_rth: int,
        format_date: int,
        keep_up_to_date: bool,
    ) -> int:
        self.ensure_ready()
        request_id = self.acquire_request_id()
        historical_event = threading.Event()
        with self._state_lock:
            self._historical_events[request_id] = historical_event
        self.reqHistoricalData(
            request_id,
            contract,
            end_date_time,
            duration,
            bar_size,
            what_to_show,
            use_rth,
            format_date,
            keep_up_to_date,
            [],
        )
        self.emit_event(
            "request.historicalData",
            reqId=request_id,
            symbol=contract.symbol,
            secType=contract.secType,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            expiry=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            conId=contract.conId,
            endDateTime=end_date_time,
            duration=duration,
            barSize=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=format_date,
            keepUpToDate=keep_up_to_date,
        )
        return request_id

    def request_contract_details(self, contract: Contract) -> int:
        self.ensure_ready()
        request_id = self.acquire_request_id()
        contract_details_event = threading.Event()
        with self._state_lock:
            self._contract_details_events[request_id] = contract_details_event
        self.reqContractDetails(request_id, contract)
        self.emit_event(
            "request.contractDetails",
            reqId=request_id,
            symbol=contract.symbol,
            secType=contract.secType,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            expiry=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            conId=contract.conId,
        )
        return request_id

    def request_option_chain(
        self,
        underlying_symbol: str,
        underlying_sec_type: str,
        underlying_con_id: int,
        fut_fop_exchange: str,
    ) -> int:
        self.ensure_ready()
        request_id = self.acquire_request_id()
        option_chain_event = threading.Event()
        with self._state_lock:
            self._option_chain_events[request_id] = option_chain_event
        self.reqSecDefOptParams(
            request_id,
            underlying_symbol,
            fut_fop_exchange,
            underlying_sec_type,
            underlying_con_id,
        )
        self.emit_event(
            "request.optionChain",
            reqId=request_id,
            underlyingSymbol=underlying_symbol,
            underlyingSecType=underlying_sec_type,
            underlyingConId=underlying_con_id,
            futFopExchange=fut_fop_exchange,
        )
        return request_id

    def request_account_summary(self, group_name: str, tags: str) -> int:
        self.ensure_ready()
        request_id = self.acquire_request_id()
        account_summary_event = threading.Event()
        with self._state_lock:
            self._account_summary_events[request_id] = account_summary_event
        self.reqAccountSummary(request_id, group_name, tags)
        self.emit_event(
            "request.accountSummary",
            reqId=request_id,
            groupName=group_name,
            tags=tags,
        )
        return request_id

    def request_positions(self) -> None:
        self.ensure_ready()
        self._positions_event.clear()
        self.reqPositions()
        self.emit_event("request.positions")

    def request_account_updates(self, account: str, subscribe: bool) -> None:
        self.ensure_ready()
        if subscribe:
            self._account_download_event.clear()
        self.reqAccountUpdates(subscribe, account)
        self.emit_event("request.accountUpdates", account=account, subscribe=subscribe)

    def request_open_orders(self, all_clients: bool) -> None:
        self.ensure_ready()
        self._open_orders_event.clear()
        if all_clients:
            self.reqAllOpenOrders()
        else:
            self.reqOpenOrders()
        self.emit_event("request.openOrders", allClients=all_clients)

    def place_limit_order(
        self,
        contract: Contract,
        *,
        action: str,
        quantity: str,
        order_type: str,
        limit_price: float,
        tif: str,
        transmit: bool,
        account: str,
    ) -> int:
        self.ensure_ready()
        with self._state_lock:
            if self._next_order_id is None:
                raise RuntimeError("IBKR did not provide nextValidId yet")
            order_id = self._next_order_id
            self._next_order_id += 1

        order = Order()
        order.action = action.upper()
        order.orderType = order_type.upper()
        cast(Any, order).totalQuantity = Decimal(quantity)
        order.lmtPrice = limit_price
        order.tif = tif.upper()
        order.transmit = transmit
        order.account = account
        order.eTradeOnly = False
        order.firmQuoteOnly = False

        self.placeOrder(order_id, contract, order)
        self.emit_event(
            "request.placeOrder",
            orderId=order_id,
            symbol=contract.symbol,
            secType=contract.secType,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            expiry=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            conId=contract.conId,
            action=order.action,
            orderType=order.orderType,
            quantity=str(order.totalQuantity),
            limitPrice=order.lmtPrice,
            tif=order.tif,
            transmit=order.transmit,
            account=order.account,
        )
        return order_id

    def cancel_order_request(self, order_id: int) -> None:
        self.ensure_ready()
        self.cancelOrder(order_id)
        self.emit_event("request.cancelOrder", orderId=order_id)

    def cancel_market_data_request(self, request_id: int) -> None:
        self.ensure_ready()
        self.cancelMktData(request_id)
        self.emit_event("request.cancelMarketData", reqId=request_id)

    def cancel_historical_request(self, request_id: int) -> None:
        self.ensure_ready()
        self.cancelHistoricalData(request_id)
        self.emit_event("request.cancelHistoricalData", reqId=request_id)

    def cancel_account_summary_request(self, request_id: int) -> None:
        self.ensure_ready()
        self.cancelAccountSummary(request_id)
        self.emit_event("request.cancelAccountSummary", reqId=request_id)

    def add_event_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._state_lock:
            self._event_listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._state_lock:
            self._event_listeners = [registered for registered in self._event_listeners if registered != listener]

    def emit_event(self, event_type: str, **payload: Any) -> None:
        event_payload = {"type": event_type, **payload}
        line = json.dumps(event_payload, default=self._json_default)
        print(line, flush=True)
        if self._log_handle is not None:
            self._log_handle.write(line + "\n")
            self._log_handle.flush()
        with self._state_lock:
            listeners = list(self._event_listeners)
        for listener in listeners:
            try:
                listener(event_payload)
            except Exception:
                continue

    def error(self, reqId: int, errorCode: int, errorString: str) -> None:  # noqa: N802
        event_type = "ib.notice" if errorCode in INFORMATIONAL_ERROR_CODES else "ib.error"
        self.emit_event(event_type, id=reqId, code=errorCode, message=errorString)
        if errorCode in CONNECTION_ERROR_CODES:
            self._set_failure(errorCode, errorString)

    def connectionClosed(self) -> None:  # noqa: N802
        self.emit_event("connection.closed")
        self._set_failure(None, "Connection closed")

    def managedAccounts(self, accountsList: str) -> None:  # noqa: N802
        with self._state_lock:
            self._managed_accounts = accountsList
        self.emit_event("connection.managedAccounts", accounts=accountsList)

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        with self._state_lock:
            self._next_order_id = orderId
        self._ready_event.set()
        self.emit_event(
            "connection.ready",
            nextOrderId=orderId,
            accounts=self._managed_accounts,
        )

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib: Any) -> None:  # noqa: N802
        self.emit_event(
            "marketData.tickPrice",
            reqId=reqId,
            field=TickTypeEnum.to_str(tickType),
            price=price,
            canAutoExecute=getattr(attrib, "canAutoExecute", None),
            pastLimit=getattr(attrib, "pastLimit", None),
            preOpen=getattr(attrib, "preOpen", None),
        )

    def tickSize(self, reqId: int, tickType: int, size: Any) -> None:  # noqa: N802
        self.emit_event(
            "marketData.tickSize",
            reqId=reqId,
            field=TickTypeEnum.to_str(tickType),
            size=str(size),
        )

    def tickString(self, reqId: int, tickType: int, value: str) -> None:  # noqa: N802
        self.emit_event(
            "marketData.tickString",
            reqId=reqId,
            field=TickTypeEnum.to_str(tickType),
            value=value,
        )

    def tickOptionComputation(  # noqa: N802
        self,
        reqId: int,
        tickType: int,
        tickAttrib: Any,
        impliedVol: float,
        delta: float,
        optPrice: float,
        pvDividend: float,
        gamma: float,
        vega: float,
        theta: float,
        undPrice: float,
    ) -> None:
        self.emit_event(
            "option.greeks",
            reqId=reqId,
            field=TickTypeEnum.to_str(tickType),
            tickAttrib=tickAttrib,
            impliedVol=impliedVol,
            delta=delta,
            optionPrice=optPrice,
            presentValueDividend=pvDividend,
            gamma=gamma,
            vega=vega,
            theta=theta,
            underlyingPrice=undPrice,
        )

    def marketDataType(self, reqId: int, marketDataType: int) -> None:  # noqa: N802
        self.emit_event("marketData.type", reqId=reqId, marketDataType=marketDataType)

    def historicalData(self, reqId: int, bar: Any) -> None:  # noqa: N802
        self.emit_event(
            "historical.bar",
            reqId=reqId,
            time=getattr(bar, "date", ""),
            open=getattr(bar, "open", None),
            high=getattr(bar, "high", None),
            low=getattr(bar, "low", None),
            close=getattr(bar, "close", None),
            volume=self._decimal_as_string(getattr(bar, "volume", "")),
            wap=self._decimal_as_string(getattr(bar, "wap", "")),
            count=getattr(bar, "barCount", None),
        )

    def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:  # noqa: N802
        with self._state_lock:
            historical_event = self._historical_events.setdefault(reqId, threading.Event())
        historical_event.set()
        self.emit_event("historical.end", reqId=reqId, start=start, end=end)

    def contractDetails(self, reqId: int, contractDetails: Any) -> None:  # noqa: N802
        summary = getattr(contractDetails, "contract", None)
        self.emit_event(
            "contract.details",
            reqId=reqId,
            symbol=getattr(summary, "symbol", ""),
            secType=getattr(summary, "secType", ""),
            exchange=getattr(summary, "exchange", ""),
            primaryExchange=getattr(summary, "primaryExchange", ""),
            currency=getattr(summary, "currency", ""),
            expiry=getattr(summary, "lastTradeDateOrContractMonth", ""),
            strike=getattr(summary, "strike", None),
            right=getattr(summary, "right", ""),
            multiplier=getattr(summary, "multiplier", ""),
            localSymbol=getattr(summary, "localSymbol", ""),
            tradingClass=getattr(summary, "tradingClass", ""),
            conId=getattr(summary, "conId", None),
            marketName=getattr(contractDetails, "marketName", ""),
            longName=getattr(contractDetails, "longName", ""),
            minTick=getattr(contractDetails, "minTick", None),
            orderTypes=getattr(contractDetails, "orderTypes", ""),
            validExchanges=getattr(contractDetails, "validExchanges", ""),
            priceMagnifier=getattr(contractDetails, "priceMagnifier", None),
            underConId=getattr(contractDetails, "underConId", None),
            timeZoneId=getattr(contractDetails, "timeZoneId", ""),
            liquidHours=getattr(contractDetails, "liquidHours", ""),
            tradingHours=getattr(contractDetails, "tradingHours", ""),
        )

    def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        with self._state_lock:
            contract_details_event = self._contract_details_events.setdefault(reqId, threading.Event())
        contract_details_event.set()
        self.emit_event("contract.detailsEnd", reqId=reqId)

    def securityDefinitionOptionParameter(  # noqa: N802
        self,
        reqId: int,
        exchange: str,
        underlyingConId: int,
        tradingClass: str,
        multiplier: str,
        expirations: Any,
        strikes: Any,
    ) -> None:
        self.emit_event(
            "option.chain",
            reqId=reqId,
            exchange=exchange,
            underlyingConId=underlyingConId,
            tradingClass=tradingClass,
            multiplier=multiplier,
            expirations=sorted(str(expiration) for expiration in expirations),
            strikes=sorted(float(strike) for strike in strikes),
        )

    def securityDefinitionOptionParameterEnd(self, reqId: int) -> None:  # noqa: N802
        with self._state_lock:
            option_chain_event = self._option_chain_events.setdefault(reqId, threading.Event())
        option_chain_event.set()
        self.emit_event("option.chainEnd", reqId=reqId)

    def orderStatus(
        self,
        orderId: int,
        status: str,
        filled: Any,
        remaining: Any,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:  # noqa: N802
        self.emit_event(
            "order.status",
            orderId=orderId,
            status=status,
            filled=self._decimal_as_string(filled),
            remaining=self._decimal_as_string(remaining),
            avgFillPrice=avgFillPrice,
            permId=permId,
            parentId=parentId,
            lastFillPrice=lastFillPrice,
            clientId=clientId,
            whyHeld=whyHeld,
            mktCapPrice=mktCapPrice,
        )

    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState: Any) -> None:  # noqa: N802
        self.emit_event(
            "order.open",
            orderId=orderId,
            symbol=contract.symbol,
            secType=contract.secType,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            expiry=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            conId=contract.conId,
            action=order.action,
            orderType=order.orderType,
            quantity=self._decimal_as_string(order.totalQuantity),
            limitPrice=order.lmtPrice,
            account=order.account,
            tif=order.tif,
            status=getattr(orderState, "status", ""),
        )

    def openOrderEnd(self) -> None:  # noqa: N802
        self._open_orders_event.set()
        self.emit_event("order.openEnd")

    def position(self, account: str, contract: Contract, position: Any, avgCost: float) -> None:
        self.emit_event(
            "portfolio.position",
            account=account,
            symbol=contract.symbol,
            secType=contract.secType,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            expiry=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            conId=contract.conId,
            position=self._decimal_as_string(position),
            avgCost=avgCost,
        )

    def positionEnd(self) -> None:  # noqa: N802
        self._positions_event.set()
        self.emit_event("portfolio.positionEnd")

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
        self.emit_event(
            "account.summary",
            reqId=reqId,
            account=account,
            tag=tag,
            value=value,
            currency=currency,
        )

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        with self._state_lock:
            account_summary_event = self._account_summary_events.setdefault(reqId, threading.Event())
        account_summary_event.set()
        self.emit_event("account.summaryEnd", reqId=reqId)

    def updateAccountValue(self, key: str, value: str, currency: str, accountName: str) -> None:  # noqa: N802
        self.emit_event(
            "account.value",
            key=key,
            value=value,
            currency=currency,
            account=accountName,
        )

    def updatePortfolio(
        self,
        contract: Contract,
        position: Any,
        marketPrice: float,
        marketValue: float,
        averageCost: float,
        unrealizedPNL: float,
        realizedPNL: float,
        accountName: str,
    ) -> None:  # noqa: N802
        self.emit_event(
            "account.portfolio",
            account=accountName,
            symbol=contract.symbol,
            secType=contract.secType,
            exchange=contract.exchange,
            primaryExchange=contract.primaryExchange,
            currency=contract.currency,
            expiry=contract.lastTradeDateOrContractMonth,
            strike=contract.strike,
            right=contract.right,
            multiplier=contract.multiplier,
            localSymbol=contract.localSymbol,
            tradingClass=contract.tradingClass,
            conId=contract.conId,
            position=self._decimal_as_string(position),
            marketPrice=marketPrice,
            marketValue=marketValue,
            averageCost=averageCost,
            unrealizedPNL=unrealizedPNL,
            realizedPNL=realizedPNL,
        )

    def updateAccountTime(self, timeStamp: str) -> None:  # noqa: N802
        self.emit_event("account.time", time=timeStamp)

    def accountDownloadEnd(self, accountName: str) -> None:  # noqa: N802
        self._account_download_event.set()
        self.emit_event("account.downloadEnd", account=accountName)

    def _release_waiters(self) -> None:
        self._ready_event.set()
        self._positions_event.set()
        self._open_orders_event.set()
        self._account_download_event.set()
        with self._state_lock:
            for contract_details_event in self._contract_details_events.values():
                contract_details_event.set()
            for account_summary_event in self._account_summary_events.values():
                account_summary_event.set()
            for historical_event in self._historical_events.values():
                historical_event.set()
            for option_chain_event in self._option_chain_events.values():
                option_chain_event.set()

    @staticmethod
    def _decimal_as_string(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _json_default(value: Any) -> str:
        return str(value)