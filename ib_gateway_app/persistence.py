from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class SQLiteEventStore:
    def __init__(self, database_path: str) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._market_data_requests: dict[int, dict[str, Any]] = {}
        self._historical_requests: dict[int, dict[str, Any]] = {}
        self._option_chain_requests: dict[int, dict[str, Any]] = {}
        self._initialize_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def handle_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._insert_raw_event(event)
            event_type = _text(event.get("type"))

            if event_type.startswith("request."):
                self._store_request_context(event)

            if event_type == "contract.details":
                self._upsert_instrument(event)
                return

            if event_type == "historical.bar":
                self._insert_historical_bar(event)
                return

            if event_type in {"marketData.tickPrice", "marketData.tickSize", "marketData.tickString", "marketData.type"}:
                self._insert_market_data_tick(event)
                return

            if event_type == "option.greeks":
                self._insert_option_greeks(event)
                return

            if event_type == "option.chain":
                self._insert_option_chain(event)
                return

            if event_type == "account.summary":
                self._insert_account_summary(event)

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_gateway_events (
                id INTEGER PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS instruments (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                sec_type TEXT NOT NULL,
                exchange TEXT NOT NULL,
                primary_exchange TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL,
                expiry TEXT NOT NULL DEFAULT '',
                strike REAL,
                right_code TEXT NOT NULL DEFAULT '',
                multiplier TEXT NOT NULL DEFAULT '',
                local_symbol TEXT NOT NULL DEFAULT '',
                trading_class TEXT NOT NULL DEFAULT '',
                con_id INTEGER,
                market_name TEXT NOT NULL DEFAULT '',
                long_name TEXT NOT NULL DEFAULT '',
                min_tick REAL,
                order_types TEXT NOT NULL DEFAULT '',
                valid_exchanges TEXT NOT NULL DEFAULT '',
                time_zone_id TEXT NOT NULL DEFAULT '',
                liquid_hours TEXT NOT NULL DEFAULT '',
                trading_hours TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, sec_type, exchange, primary_exchange, currency, expiry, strike, right_code, multiplier, local_symbol, trading_class)
            );

            CREATE TABLE IF NOT EXISTS historical_prices (
                id INTEGER PRIMARY KEY,
                instrument_id INTEGER NOT NULL,
                bar_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL DEFAULT 0,
                trade_count INTEGER NOT NULL DEFAULT 0,
                what_to_show TEXT NOT NULL,
                bar_size TEXT NOT NULL,
                duration TEXT NOT NULL,
                use_rth INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'ib_gateway',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(instrument_id, bar_time, what_to_show, bar_size, use_rth),
                FOREIGN KEY(instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_historical_prices_instrument_time ON historical_prices(instrument_id, bar_time);

            CREATE TABLE IF NOT EXISTS market_data_ticks (
                id INTEGER PRIMARY KEY,
                instrument_id INTEGER,
                req_id INTEGER,
                event_type TEXT NOT NULL,
                field TEXT NOT NULL DEFAULT '',
                price REAL,
                size_text TEXT NOT NULL DEFAULT '',
                value_text TEXT NOT NULL DEFAULT '',
                market_data_type INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(instrument_id) REFERENCES instruments(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_market_data_ticks_instrument_time ON market_data_ticks(instrument_id, created_at);

            CREATE TABLE IF NOT EXISTS option_greeks (
                id INTEGER PRIMARY KEY,
                instrument_id INTEGER,
                req_id INTEGER,
                field TEXT NOT NULL DEFAULT '',
                implied_vol REAL,
                delta REAL,
                option_price REAL,
                present_value_dividend REAL,
                gamma REAL,
                vega REAL,
                theta REAL,
                underlying_price REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(instrument_id) REFERENCES instruments(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS option_chain_snapshots (
                id INTEGER PRIMARY KEY,
                req_id INTEGER NOT NULL,
                underlying_symbol TEXT NOT NULL DEFAULT '',
                underlying_sec_type TEXT NOT NULL DEFAULT '',
                underlying_con_id INTEGER,
                fut_fop_exchange TEXT NOT NULL DEFAULT '',
                exchange TEXT NOT NULL DEFAULT '',
                trading_class TEXT NOT NULL DEFAULT '',
                multiplier TEXT NOT NULL DEFAULT '',
                expirations_json TEXT NOT NULL,
                strikes_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS account_summaries (
                id INTEGER PRIMARY KEY,
                req_id INTEGER,
                account TEXT NOT NULL,
                tag TEXT NOT NULL,
                value_text TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._connection.commit()

    def _insert_raw_event(self, event: dict[str, Any]) -> None:
        self._connection.execute(
            "INSERT INTO raw_gateway_events (event_type, payload_json) VALUES (?, ?)",
            (_text(event.get("type")), json.dumps(event, default=str)),
        )
        self._connection.commit()

    def _store_request_context(self, event: dict[str, Any]) -> None:
        event_type = _text(event.get("type"))
        req_id = _optional_int(event.get("reqId"))

        if event_type == "request.marketData" and req_id is not None:
            self._market_data_requests[req_id] = dict(event)
            self._upsert_instrument(event)
            return

        if event_type == "request.historicalData" and req_id is not None:
            self._historical_requests[req_id] = dict(event)
            self._upsert_instrument(event)
            return

        if event_type == "request.contractDetails":
            self._upsert_instrument(event)
            return

        if event_type == "request.placeOrder":
            self._upsert_instrument(event)
            return

        if event_type == "request.optionChain" and req_id is not None:
            self._option_chain_requests[req_id] = dict(event)

    def _upsert_instrument(self, event: dict[str, Any]) -> int:
        symbol = _text(event.get("symbol"))
        sec_type = _text(event.get("secType", "STK"))
        exchange = _text(event.get("exchange", "SMART"))
        primary_exchange = _text(event.get("primaryExchange"))
        currency = _text(event.get("currency", "USD"))
        expiry = _text(event.get("expiry", event.get("lastTradeDateOrContractMonth")))
        strike = _optional_float(event.get("strike"))
        right_code = _text(event.get("right"))
        multiplier = _text(event.get("multiplier"))
        local_symbol = _text(event.get("localSymbol"))
        trading_class = _text(event.get("tradingClass"))
        con_id = _optional_int(event.get("conId"))
        market_name = _text(event.get("marketName"))
        long_name = _text(event.get("longName"))
        min_tick = _optional_float(event.get("minTick"))
        order_types = _text(event.get("orderTypes"))
        valid_exchanges = _text(event.get("validExchanges"))
        time_zone_id = _text(event.get("timeZoneId"))
        liquid_hours = _text(event.get("liquidHours"))
        trading_hours = _text(event.get("tradingHours"))
        has_detail = any([local_symbol, trading_class, con_id is not None, market_name, long_name, min_tick is not None])

        if has_detail:
            provisional_id = self._find_instrument_id(
                symbol=symbol,
                sec_type=sec_type,
                exchange=exchange,
                primary_exchange=primary_exchange,
                currency=currency,
                expiry=expiry,
                strike=strike,
                right_code=right_code,
                multiplier=multiplier,
                local_symbol="",
                trading_class="",
            )
            exact_id = self._find_instrument_id(
                symbol=symbol,
                sec_type=sec_type,
                exchange=exchange,
                primary_exchange=primary_exchange,
                currency=currency,
                expiry=expiry,
                strike=strike,
                right_code=right_code,
                multiplier=multiplier,
                local_symbol=local_symbol,
                trading_class=trading_class,
            )
            if provisional_id is not None:
                if exact_id is not None and exact_id != provisional_id:
                    self._merge_duplicate_instrument_rows(target_id=provisional_id, duplicate_id=exact_id)
                self._update_instrument_row(
                    provisional_id,
                    con_id=con_id,
                    market_name=market_name,
                    long_name=long_name,
                    min_tick=min_tick,
                    order_types=order_types,
                    valid_exchanges=valid_exchanges,
                    time_zone_id=time_zone_id,
                    liquid_hours=liquid_hours,
                    trading_hours=trading_hours,
                    local_symbol=local_symbol,
                    trading_class=trading_class,
                )
                self._connection.commit()
                return provisional_id

        cursor = self._connection.cursor()
        cursor.execute(
            """
            INSERT INTO instruments (
                symbol, sec_type, exchange, primary_exchange, currency, expiry, strike, right_code,
                multiplier, local_symbol, trading_class, con_id, market_name, long_name, min_tick,
                order_types, valid_exchanges, time_zone_id, liquid_hours, trading_hours, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(symbol, sec_type, exchange, primary_exchange, currency, expiry, strike, right_code, multiplier, local_symbol, trading_class)
            DO UPDATE SET
                con_id=excluded.con_id,
                market_name=CASE WHEN excluded.market_name = '' THEN instruments.market_name ELSE excluded.market_name END,
                long_name=CASE WHEN excluded.long_name = '' THEN instruments.long_name ELSE excluded.long_name END,
                min_tick=COALESCE(excluded.min_tick, instruments.min_tick),
                order_types=CASE WHEN excluded.order_types = '' THEN instruments.order_types ELSE excluded.order_types END,
                valid_exchanges=CASE WHEN excluded.valid_exchanges = '' THEN instruments.valid_exchanges ELSE excluded.valid_exchanges END,
                time_zone_id=CASE WHEN excluded.time_zone_id = '' THEN instruments.time_zone_id ELSE excluded.time_zone_id END,
                liquid_hours=CASE WHEN excluded.liquid_hours = '' THEN instruments.liquid_hours ELSE excluded.liquid_hours END,
                trading_hours=CASE WHEN excluded.trading_hours = '' THEN instruments.trading_hours ELSE excluded.trading_hours END,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                symbol,
                sec_type,
                exchange,
                primary_exchange,
                currency,
                expiry,
                strike,
                right_code,
                multiplier,
                local_symbol,
                trading_class,
                con_id,
                market_name,
                long_name,
                min_tick,
                order_types,
                valid_exchanges,
                time_zone_id,
                liquid_hours,
                trading_hours,
            ),
        )
        cursor.execute(
            """
            SELECT id FROM instruments
            WHERE symbol = ? AND sec_type = ? AND exchange = ? AND primary_exchange = ? AND currency = ?
              AND expiry = ? AND ((strike IS NULL AND ? IS NULL) OR strike = ?)
              AND right_code = ? AND multiplier = ? AND local_symbol = ? AND trading_class = ?
            """,
            (
                symbol,
                sec_type,
                exchange,
                primary_exchange,
                currency,
                expiry,
                strike,
                strike,
                right_code,
                multiplier,
                local_symbol,
                trading_class,
            ),
        )
        row = cursor.fetchone()
        self._connection.commit()
        if row is None:
            raise RuntimeError("failed to resolve instrument row after upsert")
        return int(row[0])

    def _find_instrument_id(
        self,
        *,
        symbol: str,
        sec_type: str,
        exchange: str,
        primary_exchange: str,
        currency: str,
        expiry: str,
        strike: float | None,
        right_code: str,
        multiplier: str,
        local_symbol: str,
        trading_class: str,
    ) -> int | None:
        row = self._connection.execute(
            """
            SELECT id FROM instruments
            WHERE symbol = ? AND sec_type = ? AND exchange = ? AND primary_exchange = ? AND currency = ?
              AND expiry = ? AND ((strike IS NULL AND ? IS NULL) OR strike = ?)
              AND right_code = ? AND multiplier = ? AND local_symbol = ? AND trading_class = ?
            LIMIT 1
            """,
            (
                symbol,
                sec_type,
                exchange,
                primary_exchange,
                currency,
                expiry,
                strike,
                strike,
                right_code,
                multiplier,
                local_symbol,
                trading_class,
            ),
        ).fetchone()
        return None if row is None else int(row[0])

    def _update_instrument_row(
        self,
        instrument_id: int,
        *,
        con_id: int | None,
        market_name: str,
        long_name: str,
        min_tick: float | None,
        order_types: str,
        valid_exchanges: str,
        time_zone_id: str,
        liquid_hours: str,
        trading_hours: str,
        local_symbol: str,
        trading_class: str,
    ) -> None:
        self._connection.execute(
            """
            UPDATE instruments
            SET con_id = COALESCE(?, con_id),
                market_name = CASE WHEN ? = '' THEN market_name ELSE ? END,
                long_name = CASE WHEN ? = '' THEN long_name ELSE ? END,
                min_tick = COALESCE(?, min_tick),
                order_types = CASE WHEN ? = '' THEN order_types ELSE ? END,
                valid_exchanges = CASE WHEN ? = '' THEN valid_exchanges ELSE ? END,
                time_zone_id = CASE WHEN ? = '' THEN time_zone_id ELSE ? END,
                liquid_hours = CASE WHEN ? = '' THEN liquid_hours ELSE ? END,
                trading_hours = CASE WHEN ? = '' THEN trading_hours ELSE ? END,
                local_symbol = CASE WHEN ? = '' THEN local_symbol ELSE ? END,
                trading_class = CASE WHEN ? = '' THEN trading_class ELSE ? END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                con_id,
                market_name,
                market_name,
                long_name,
                long_name,
                min_tick,
                order_types,
                order_types,
                valid_exchanges,
                valid_exchanges,
                time_zone_id,
                time_zone_id,
                liquid_hours,
                liquid_hours,
                trading_hours,
                trading_hours,
                local_symbol,
                local_symbol,
                trading_class,
                trading_class,
                instrument_id,
            ),
        )

    def _merge_duplicate_instrument_rows(self, *, target_id: int, duplicate_id: int) -> None:
        for table_name in ("historical_prices", "market_data_ticks", "option_greeks"):
            self._connection.execute(
                f"UPDATE {table_name} SET instrument_id = ? WHERE instrument_id = ?",
                (target_id, duplicate_id),
            )
        self._connection.execute("DELETE FROM instruments WHERE id = ?", (duplicate_id,))

    def _instrument_id_for_request(self, req_id: int, request_store: dict[int, dict[str, Any]]) -> int | None:
        request_event = request_store.get(req_id)
        if request_event is None:
            return None
        return self._upsert_instrument(request_event)

    def _insert_historical_bar(self, event: dict[str, Any]) -> None:
        req_id = _optional_int(event.get("reqId"))
        if req_id is None:
            return
        instrument_id = self._instrument_id_for_request(req_id, self._historical_requests)
        if instrument_id is None:
            return
        request_event = self._historical_requests[req_id]
        self._connection.execute(
            """
            INSERT INTO historical_prices (
                instrument_id, bar_time, open, high, low, close, volume, trade_count, what_to_show, bar_size, duration, use_rth, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id, bar_time, what_to_show, bar_size, use_rth)
            DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                trade_count=excluded.trade_count,
                duration=excluded.duration,
                source=excluded.source
            """,
            (
                instrument_id,
                _text(event.get("time")),
                _optional_float(event.get("open")) or 0.0,
                _optional_float(event.get("high")) or 0.0,
                _optional_float(event.get("low")) or 0.0,
                _optional_float(event.get("close")) or 0.0,
                _optional_int(event.get("volume")) or 0,
                _optional_int(event.get("count")) or 0,
                _text(request_event.get("whatToShow")),
                _text(request_event.get("barSize")),
                _text(request_event.get("duration")),
                _optional_int(request_event.get("useRTH")) or 0,
                "ib_gateway",
            ),
        )
        self._connection.commit()

    def _insert_market_data_tick(self, event: dict[str, Any]) -> None:
        req_id = _optional_int(event.get("reqId"))
        instrument_id = self._instrument_id_for_request(req_id, self._market_data_requests) if req_id is not None else None
        self._connection.execute(
            """
            INSERT INTO market_data_ticks (
                instrument_id, req_id, event_type, field, price, size_text, value_text, market_data_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument_id,
                req_id,
                _text(event.get("type")),
                _text(event.get("field")),
                _optional_float(event.get("price")),
                _text(event.get("size")),
                _text(event.get("value")),
                _optional_int(event.get("marketDataType")),
            ),
        )
        self._connection.commit()

    def _insert_option_greeks(self, event: dict[str, Any]) -> None:
        req_id = _optional_int(event.get("reqId"))
        instrument_id = self._instrument_id_for_request(req_id, self._market_data_requests) if req_id is not None else None
        self._connection.execute(
            """
            INSERT INTO option_greeks (
                instrument_id, req_id, field, implied_vol, delta, option_price, present_value_dividend, gamma, vega, theta, underlying_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument_id,
                req_id,
                _text(event.get("field")),
                _optional_float(event.get("impliedVol")),
                _optional_float(event.get("delta")),
                _optional_float(event.get("optionPrice")),
                _optional_float(event.get("presentValueDividend")),
                _optional_float(event.get("gamma")),
                _optional_float(event.get("vega")),
                _optional_float(event.get("theta")),
                _optional_float(event.get("underlyingPrice")),
            ),
        )
        self._connection.commit()

    def _insert_option_chain(self, event: dict[str, Any]) -> None:
        req_id = _optional_int(event.get("reqId"))
        request_event = self._option_chain_requests.get(req_id or -1, {})
        self._connection.execute(
            """
            INSERT INTO option_chain_snapshots (
                req_id, underlying_symbol, underlying_sec_type, underlying_con_id, fut_fop_exchange,
                exchange, trading_class, multiplier, expirations_json, strikes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req_id,
                _text(request_event.get("underlyingSymbol")),
                _text(request_event.get("underlyingSecType")),
                _optional_int(request_event.get("underlyingConId")),
                _text(request_event.get("futFopExchange")),
                _text(event.get("exchange")),
                _text(event.get("tradingClass")),
                _text(event.get("multiplier")),
                json.dumps(event.get("expirations", []), default=str),
                json.dumps(event.get("strikes", []), default=str),
            ),
        )
        self._connection.commit()

    def _insert_account_summary(self, event: dict[str, Any]) -> None:
        self._connection.execute(
            "INSERT INTO account_summaries (req_id, account, tag, value_text, currency) VALUES (?, ?, ?, ?, ?)",
            (
                _optional_int(event.get("reqId")),
                _text(event.get("account")),
                _text(event.get("tag")),
                _text(event.get("value")),
                _text(event.get("currency")),
            ),
        )
        self._connection.commit()