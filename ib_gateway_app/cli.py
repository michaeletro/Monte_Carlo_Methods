from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from .contracts import build_contract
from .gateway import IBGateway
from .persistence import SQLiteEventStore
from .parsing import (
    default_account_summary_tags,
    ensure_limit_price,
    parse_bool,
    parse_market_data_type,
    parse_quantity,
)
from .websocket_bridge import WebSocketBridge


def _sleep_for_runtime(gateway: IBGateway, runtime_seconds: int) -> None:
    if runtime_seconds > 0:
        gateway.sleep_while_connected(runtime_seconds)
        return

    while True:
        gateway.sleep_while_connected(1)


_INTERVAL_UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
    "y": 31536000,
    "year": 31536000,
    "years": 31536000,
}


def _parse_interval_seconds(value: str) -> int | None:
    parts = value.strip().split()
    if len(parts) != 2:
        return None

    try:
        amount = float(parts[0])
    except ValueError:
        return None

    unit_seconds = _INTERVAL_UNITS.get(parts[1].lower())
    if unit_seconds is None:
        return None
    return max(1, int(amount * unit_seconds))


def _parse_ib_timestamp(value: Any) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None

    normalized = " ".join(text.split())
    if normalized.isdigit():
        if len(normalized) == 8:
            try:
                return datetime.strptime(normalized, "%Y%m%d")
            except ValueError:
                return None
        if len(normalized) <= 10:
            try:
                return datetime.fromtimestamp(int(normalized))
            except (OverflowError, OSError, ValueError):
                return None

    for pattern in ("%Y%m%d %H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    return None


class _HistoricalBarTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest_bar_time: datetime | None = None
        self._last_bar_event_monotonic: float | None = None
        self._pacing_errors: set[int] = set()

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "ib.error":
            request_id = event.get("id")
            error_code = event.get("code")
            if isinstance(request_id, int) and request_id >= 0 and error_code == 162:
                with self._lock:
                    self._pacing_errors.add(request_id)
            return

        if event_type != "historical.bar":
            return

        bar_time = _parse_ib_timestamp(event.get("time"))
        if bar_time is None:
            return

        with self._lock:
            if self._latest_bar_time is None or bar_time > self._latest_bar_time:
                self._latest_bar_time = bar_time
            self._last_bar_event_monotonic = time.monotonic()

    def latest_bar_time(self) -> datetime | None:
        with self._lock:
            return self._latest_bar_time

    def seconds_since_last_bar(self) -> float | None:
        with self._lock:
            if self._last_bar_event_monotonic is None:
                return None
            return time.monotonic() - self._last_bar_event_monotonic

    def consume_pacing_error(self, request_id: int) -> bool:
        with self._lock:
            if request_id not in self._pacing_errors:
                return False
            self._pacing_errors.remove(request_id)
            return True


def _current_tail_duration(duration: str, bar_size: str, latest_bar_time: datetime | None) -> str:
    if latest_bar_time is None:
        return duration

    duration_seconds = _parse_interval_seconds(duration)
    bar_seconds = _parse_interval_seconds(bar_size)
    if duration_seconds is None or bar_seconds is None:
        return duration

    gap_seconds = max(0, math.ceil((datetime.now() - latest_bar_time).total_seconds()))
    uncovered_window_seconds = max(bar_seconds, gap_seconds)
    rounded_window_seconds = math.ceil(uncovered_window_seconds / bar_seconds) * bar_seconds
    recent_window_seconds = max(bar_seconds, min(duration_seconds, rounded_window_seconds))
    return f"{recent_window_seconds} S"


def _effective_refresh_interval_seconds(requested_poll_seconds: int, refresh_duration: str) -> int:
    refresh_duration_seconds = _parse_interval_seconds(refresh_duration)
    if refresh_duration_seconds is None or refresh_duration_seconds <= 300:
        return max(1, requested_poll_seconds)

    # Large delayed tails require slower refreshes to avoid HMDS pacing violations.
    throttled_poll_seconds = min(60, math.ceil(refresh_duration_seconds / 60))
    return max(1, requested_poll_seconds, throttled_poll_seconds)


def _pacing_backoff_seconds(requested_poll_seconds: int, refresh_duration: str) -> int:
    return max(30, _effective_refresh_interval_seconds(requested_poll_seconds, refresh_duration) * 2)


def _sleep_for_retry(gateway: IBGateway, deadline: float | None, sleep_seconds: int) -> bool:
    if sleep_seconds <= 0:
        return True
    if deadline is None:
        gateway.sleep_while_connected(sleep_seconds)
        return True

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False

    gateway.sleep_while_connected(min(sleep_seconds, math.ceil(remaining)))
    return time.monotonic() < deadline


def _run_historical_collector(gateway: IBGateway, args: argparse.Namespace, tracker: _HistoricalBarTracker) -> int:
    contract = build_contract(args)
    deadline = time.monotonic() + args.runtime_seconds if args.runtime_seconds > 0 else None
    while True:
        request_id = gateway.request_historical_data(
            contract,
            args.end_date_time,
            args.duration,
            args.bar_size,
            args.what_to_show.upper(),
            args.use_rth,
            args.format_date,
            True,
        )

        if gateway.wait_for_historical(request_id, args.ready_timeout):
            break

        gateway.cancel_historical_request(request_id)
        if tracker.consume_pacing_error(request_id):
            if not _sleep_for_retry(gateway, deadline, _pacing_backoff_seconds(args.poll_seconds, args.duration)):
                return 1
            continue
        return 1

    while True:
        gateway.raise_if_failed()

        if deadline is not None and time.monotonic() >= deadline:
            gateway.cancel_historical_request(request_id)
            return 0

        refresh_duration = _current_tail_duration(args.duration, args.bar_size, tracker.latest_bar_time())
        effective_poll_seconds = _effective_refresh_interval_seconds(args.poll_seconds, refresh_duration)
        seconds_since_last_bar = tracker.seconds_since_last_bar()
        if seconds_since_last_bar is not None and seconds_since_last_bar < effective_poll_seconds:
            sleep_seconds = min(1, max(effective_poll_seconds - seconds_since_last_bar, 0.0))
            if deadline is not None:
                sleep_seconds = min(sleep_seconds, max(deadline - time.monotonic(), 0.0))
            if sleep_seconds <= 0:
                continue
            gateway.sleep_while_connected(math.ceil(sleep_seconds))
            continue

        refresh_request_id = gateway.request_historical_data(
            contract,
            "",
            refresh_duration,
            args.bar_size,
            args.what_to_show.upper(),
            args.use_rth,
            args.format_date,
            False,
        )
        if not gateway.wait_for_historical(refresh_request_id, args.ready_timeout):
            gateway.cancel_historical_request(refresh_request_id)
            if tracker.consume_pacing_error(refresh_request_id):
                if not _sleep_for_retry(gateway, deadline, _pacing_backoff_seconds(args.poll_seconds, refresh_duration)):
                    gateway.cancel_historical_request(request_id)
                    return 0
                continue
            gateway.cancel_historical_request(request_id)
            return 1


def _connect_db(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _underlying_contract_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        symbol=args.symbol,
        sec_type=args.sec_type,
        exchange=args.exchange,
        primary_exchange=args.primary_exchange,
        currency=args.currency,
        expiry="",
        strike=None,
        right="",
        multiplier="",
        local_symbol="",
        trading_class="",
        con_id=args.con_id,
    )


def _option_contract_args(
    args: argparse.Namespace,
    *,
    expiry: str,
    strike: float,
    right: str,
    multiplier: str,
    trading_class: str,
) -> argparse.Namespace:
    return argparse.Namespace(
        symbol=args.symbol,
        sec_type="OPT",
        exchange=args.exchange,
        primary_exchange=args.primary_exchange,
        currency=args.currency,
        expiry=expiry,
        strike=strike,
        right=right,
        multiplier=multiplier,
        local_symbol="",
        trading_class=trading_class,
        con_id=None,
    )


def _parse_option_expiry(value: str) -> datetime | None:
    digits = "".join(character for character in str(value).strip() if character.isdigit())
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d")
    except ValueError:
        return None


def _is_monthly_expiry(expiry: datetime) -> bool:
    return expiry.weekday() == 4 and 15 <= expiry.day <= 21


def _load_underlying_con_id(database_path: str, args: argparse.Namespace) -> int | None:
    with _connect_db(database_path) as connection:
        row = connection.execute(
            """
            SELECT con_id
            FROM instruments
            WHERE symbol = ? AND sec_type = ? AND exchange = ? AND currency = ? AND con_id IS NOT NULL
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (args.symbol.upper(), args.sec_type.upper(), args.exchange.upper(), args.currency.upper()),
        ).fetchone()
    if row is None or row["con_id"] is None:
        return None
    return int(row["con_id"])


def _load_option_chain_snapshot(database_path: str, request_id: int) -> dict[str, Any]:
    with _connect_db(database_path) as connection:
        rows = connection.execute(
            """
            SELECT exchange, trading_class, multiplier, expirations_json, strikes_json
            FROM option_chain_snapshots
            WHERE req_id = ?
            ORDER BY id DESC
            """,
            (request_id,),
        ).fetchall()

    if not rows:
        raise ValueError("no option-chain snapshot was persisted for the request")

    expiries: set[str] = set()
    strikes: set[float] = set()
    preferred_row = next((row for row in rows if str(row["exchange"]).upper() == "SMART"), rows[0])
    for row in rows:
        expiries.update(str(expiry).strip() for expiry in json.loads(str(row["expirations_json"] or "[]")) if str(expiry).strip())
        strikes.update(float(strike) for strike in json.loads(str(row["strikes_json"] or "[]")))

    if not expiries or not strikes:
        raise ValueError("option-chain snapshot did not include expiries and strikes")

    return {
        "expiries": sorted(expiries),
        "strikes": sorted(strikes),
        "multiplier": str(preferred_row["multiplier"] or "100"),
        "trading_class": str(preferred_row["trading_class"] or ""),
    }


def _load_qualified_option_contract(
    database_path: str,
    args: argparse.Namespace,
    *,
    expiry: str,
    strike: float,
    right: str,
    multiplier: str,
    trading_class: str,
) -> argparse.Namespace | None:
    with _connect_db(database_path) as connection:
        row = connection.execute(
            """
            SELECT exchange, primary_exchange, currency, multiplier, local_symbol, trading_class, con_id
            FROM instruments
            WHERE symbol = ? AND sec_type = 'OPT' AND exchange = ? AND currency = ?
              AND expiry = ? AND strike = ? AND right_code = ? AND multiplier = ? AND trading_class = ?
              AND con_id IS NOT NULL
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (
                args.symbol.upper(),
                args.exchange.upper(),
                args.currency.upper(),
                expiry,
                strike,
                right,
                multiplier,
                trading_class,
            ),
        ).fetchone()

    if row is None or row["con_id"] is None:
        return None

    return argparse.Namespace(
        symbol=args.symbol,
        sec_type="OPT",
        exchange=str(row["exchange"] or args.exchange),
        primary_exchange=str(row["primary_exchange"] or args.primary_exchange),
        currency=str(row["currency"] or args.currency),
        expiry=expiry,
        strike=strike,
        right=right,
        multiplier=str(row["multiplier"] or multiplier),
        local_symbol=str(row["local_symbol"] or ""),
        trading_class=str(row["trading_class"] or trading_class),
        con_id=int(row["con_id"]),
    )


def _qualify_option_contract(
    gateway: IBGateway,
    database_path: str,
    args: argparse.Namespace,
    *,
    expiry: str,
    strike: float,
    right: str,
    multiplier: str,
    trading_class: str,
) -> argparse.Namespace:
    option_args = _option_contract_args(
        args,
        expiry=expiry,
        strike=strike,
        right=right,
        multiplier=multiplier,
        trading_class=trading_class,
    )
    request_id = gateway.request_contract_details(build_contract(option_args))
    if gateway.wait_for_contract_details(request_id, args.ready_timeout):
        qualified_option = _load_qualified_option_contract(
            database_path,
            args,
            expiry=expiry,
            strike=strike,
            right=right,
            multiplier=multiplier,
            trading_class=trading_class,
        )
        if qualified_option is not None:
            return qualified_option
    return option_args


def _select_option_expiries(expiries: list[str], expiry_mode: str) -> list[str]:
    parsed_expiries = [(expiry, _parse_option_expiry(expiry)) for expiry in expiries]
    future_expiries = [
        (expiry, expiry_time)
        for expiry, expiry_time in parsed_expiries
        if expiry_time is not None and expiry_time + timedelta(days=1) >= datetime.now()
    ]
    future_expiries.sort(key=lambda item: item[1])
    if not future_expiries:
        raise ValueError("no future option expiries were available in the option chain")

    weekly_expiry = future_expiries[0][0]
    monthly_expiry = next((expiry for expiry, expiry_time in future_expiries if _is_monthly_expiry(expiry_time)), weekly_expiry)

    if expiry_mode == "weekly":
        return [weekly_expiry]
    if expiry_mode == "monthly":
        return [monthly_expiry]

    selected: list[str] = []
    for expiry in (weekly_expiry, monthly_expiry):
        if expiry not in selected:
            selected.append(expiry)
    return selected


def _select_strike_strip(strikes: list[float], spot_price: float, strikes_around: int) -> list[float]:
    if not strikes:
        raise ValueError("no strikes were available in the option chain")

    ordered = sorted(float(strike) for strike in strikes)
    nearest_index = min(range(len(ordered)), key=lambda index: abs(ordered[index] - spot_price))
    lower_index = max(0, nearest_index - max(strikes_around, 0))
    upper_index = min(len(ordered), nearest_index + max(strikes_around, 0) + 1)
    return ordered[lower_index:upper_index]


def _load_spot_from_ticks(database_path: str, args: argparse.Namespace) -> float | None:
    with _connect_db(database_path) as connection:
        rows = connection.execute(
            """
            SELECT m.field, m.price
            FROM market_data_ticks m
            JOIN instruments i ON i.id = m.instrument_id
            WHERE i.symbol = ? AND i.sec_type = ? AND i.exchange = ? AND i.currency = ?
              AND m.event_type = 'marketData.tickPrice' AND m.price IS NOT NULL
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT 50
            """,
            (args.symbol.upper(), args.sec_type.upper(), args.exchange.upper(), args.currency.upper()),
        ).fetchall()

    latest_by_field: dict[str, float] = {}
    for row in rows:
        field = str(row["field"] or "").upper().replace(" ", "_")
        if field in latest_by_field:
            continue
        latest_by_field[field] = float(row["price"])

    for field in ("LAST", "DELAYED_LAST", "CLOSE", "DELAYED_CLOSE"):
        if field in latest_by_field:
            return latest_by_field[field]

    bid = latest_by_field.get("BID") or latest_by_field.get("DELAYED_BID")
    ask = latest_by_field.get("ASK") or latest_by_field.get("DELAYED_ASK")
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    return None


def _standard_normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _load_spot_from_history(database_path: str, args: argparse.Namespace) -> float | None:
    with _connect_db(database_path) as connection:
        row = connection.execute(
            """
            SELECT hp.close
            FROM historical_prices hp
            JOIN instruments i ON i.id = hp.instrument_id
            WHERE i.symbol = ? AND i.sec_type = ? AND i.exchange = ? AND i.currency = ?
            ORDER BY hp.bar_time DESC, hp.id DESC
            LIMIT 1
            """,
            (args.symbol.upper(), args.sec_type.upper(), args.exchange.upper(), args.currency.upper()),
        ).fetchone()
    if row is None or row["close"] is None:
        return None
    return float(row["close"])


def _time_to_expiry_years(expiry: str) -> float | None:
    expiry_time = _parse_option_expiry(expiry)
    if expiry_time is None:
        return None
    expiry_close = expiry_time + timedelta(days=1)
    return max((expiry_close - datetime.utcnow()).total_seconds(), 0.0) / (365.0 * 24.0 * 60.0 * 60.0)


def _black_scholes_price(*, spot_price: float, strike: float, time_to_expiry_years: float, implied_vol: float, right_code: str) -> float:
    if time_to_expiry_years <= 0 or implied_vol <= 0:
        intrinsic_value = max(spot_price - strike, 0.0)
        if right_code.upper() == "P":
            intrinsic_value = max(strike - spot_price, 0.0)
        return intrinsic_value

    sigma_sqrt_t = implied_vol * math.sqrt(time_to_expiry_years)
    d1 = (math.log(spot_price / strike) + 0.5 * (implied_vol**2) * time_to_expiry_years) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t
    if right_code.upper() == "P":
        return strike * _standard_normal_cdf(-d2) - spot_price * _standard_normal_cdf(-d1)
    return spot_price * _standard_normal_cdf(d1) - strike * _standard_normal_cdf(d2)


def _solve_implied_volatility(*, spot_price: float, strike: float, expiry: str, right_code: str, option_price: float) -> float | None:
    if spot_price <= 0 or strike <= 0 or option_price <= 0:
        return None

    time_to_expiry_years = _time_to_expiry_years(expiry)
    if time_to_expiry_years is None:
        return None
    if time_to_expiry_years <= 0:
        return None

    intrinsic_value = max(spot_price - strike, 0.0)
    if right_code.upper() == "P":
        intrinsic_value = max(strike - spot_price, 0.0)
    if option_price < intrinsic_value - 1e-6:
        return None

    lower_bound = 1e-4
    upper_bound = 5.0
    lower_price = _black_scholes_price(
        spot_price=spot_price,
        strike=strike,
        time_to_expiry_years=time_to_expiry_years,
        implied_vol=lower_bound,
        right_code=right_code,
    )
    upper_price = _black_scholes_price(
        spot_price=spot_price,
        strike=strike,
        time_to_expiry_years=time_to_expiry_years,
        implied_vol=upper_bound,
        right_code=right_code,
    )
    if option_price < lower_price - 1e-6 or option_price > upper_price + 1e-6:
        return None

    for _ in range(80):
        midpoint = (lower_bound + upper_bound) / 2.0
        midpoint_price = _black_scholes_price(
            spot_price=spot_price,
            strike=strike,
            time_to_expiry_years=time_to_expiry_years,
            implied_vol=midpoint,
            right_code=right_code,
        )
        if abs(midpoint_price - option_price) <= 1e-6:
            return midpoint
        if midpoint_price < option_price:
            lower_bound = midpoint
        else:
            upper_bound = midpoint
    return (lower_bound + upper_bound) / 2.0


def _load_latest_option_quote(database_path: str, request_id: int) -> dict[str, Any] | None:
    with _connect_db(database_path) as connection:
        rows = connection.execute(
            """
            SELECT m.instrument_id, m.field, m.price, i.expiry, i.strike, i.right_code
            FROM market_data_ticks m
            JOIN instruments i ON i.id = m.instrument_id
            WHERE m.req_id = ? AND m.event_type = 'marketData.tickPrice' AND m.price IS NOT NULL
            ORDER BY m.id DESC
            LIMIT 50
            """,
            (request_id,),
        ).fetchall()

    if not rows:
        return None

    latest_by_field: dict[str, float] = {}
    reference_row: sqlite3.Row | None = None
    for row in rows:
        if reference_row is None and row["instrument_id"] is not None:
            reference_row = row
        field = str(row["field"] or "").upper().replace(" ", "_")
        if field in latest_by_field:
            continue
        latest_by_field[field] = float(row["price"])

    if reference_row is None:
        return None

    option_price = None
    for field in ("LAST", "DELAYED_LAST", "CLOSE", "DELAYED_CLOSE"):
        candidate = latest_by_field.get(field)
        if candidate is not None and candidate > 0:
            option_price = candidate
            break

    if option_price is None:
        bid = latest_by_field.get("BID") or latest_by_field.get("DELAYED_BID")
        ask = latest_by_field.get("ASK") or latest_by_field.get("DELAYED_ASK")
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            option_price = (bid + ask) / 2.0

    if option_price is None:
        return None

    return {
        "instrument_id": int(reference_row["instrument_id"]),
        "expiry": str(reference_row["expiry"] or ""),
        "strike": float(reference_row["strike"]),
        "right_code": str(reference_row["right_code"] or ""),
        "option_price": float(option_price),
    }


def _option_greeks_exist_for_request(database_path: str, request_id: int) -> bool:
    with _connect_db(database_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM option_greeks WHERE req_id = ? LIMIT 1",
            (request_id,),
        ).fetchone()
    return row is not None


def _insert_synthetic_option_greek(
    database_path: str,
    *,
    instrument_id: int,
    request_id: int,
    implied_vol: float,
    option_price: float,
    underlying_price: float,
) -> None:
    with _connect_db(database_path) as connection:
        connection.execute(
            """
            INSERT INTO option_greeks (
                instrument_id, req_id, field, implied_vol, delta, option_price, present_value_dividend, gamma, vega, theta, underlying_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument_id,
                request_id,
                "SYNTHETIC_IV",
                implied_vol,
                None,
                option_price,
                None,
                None,
                None,
                None,
                underlying_price,
            ),
        )
        connection.commit()


def _backfill_option_greeks_from_ticks(database_path: str, request_ids: list[int], spot_price: float) -> int:
    inserted_rows = 0
    for request_id in request_ids:
        if _option_greeks_exist_for_request(database_path, request_id):
            continue

        quote_row = _load_latest_option_quote(database_path, request_id)
        if quote_row is None:
            continue

        implied_vol = _solve_implied_volatility(
            spot_price=spot_price,
            strike=float(quote_row["strike"]),
            expiry=str(quote_row["expiry"]),
            right_code=str(quote_row["right_code"]),
            option_price=float(quote_row["option_price"]),
        )
        if implied_vol is None:
            continue

        _insert_synthetic_option_greek(
            database_path,
            instrument_id=int(quote_row["instrument_id"]),
            request_id=request_id,
            implied_vol=implied_vol,
            option_price=float(quote_row["option_price"]),
            underlying_price=spot_price,
        )
        inserted_rows += 1
    return inserted_rows


def _bootstrap_spot_from_history(gateway: IBGateway, database_path: str, args: argparse.Namespace) -> float | None:
    what_to_show = "MIDPOINT" if args.sec_type.upper() in {"CASH", "IND"} else "TRADES"
    request_id = gateway.request_historical_data(
        build_contract(_underlying_contract_args(args)),
        "",
        "2 D",
        "1 min",
        what_to_show,
        0,
        1,
        False,
    )
    if not gateway.wait_for_historical(request_id, args.ready_timeout):
        gateway.cancel_historical_request(request_id)
        return None
    return _load_spot_from_history(database_path, args)


def _resolve_spot_price(gateway: IBGateway, database_path: str, args: argparse.Namespace) -> float:
    spot_price = _load_spot_from_ticks(database_path, args)
    if spot_price is None:
        spot_price = _load_spot_from_history(database_path, args)
    if spot_price is None:
        spot_price = _bootstrap_spot_from_history(gateway, database_path, args)
    if spot_price is None:
        underlying_contract = build_contract(_underlying_contract_args(args))
        request_id = gateway.request_market_data(
            underlying_contract,
            "233",
            False,
            False,
            args.market_data_type,
        )
        try:
            gateway.sleep_while_connected(args.spot_runtime_seconds)
        finally:
            gateway.cancel_market_data_request(request_id)

        spot_price = _load_spot_from_ticks(database_path, args)
    if spot_price is None:
        raise ValueError(f"could not resolve a spot price for {args.symbol} from market data or historical cache")
    return spot_price


def _run_option_greeks_strip_collector(gateway: IBGateway, args: argparse.Namespace) -> int:
    if not args.db:
        raise ValueError("--db is required for option-greeks-strip")
    if args.runtime_seconds <= 0:
        raise ValueError("--runtime-seconds must be greater than zero for option-greeks-strip")
    if args.spot_runtime_seconds <= 0:
        raise ValueError("--spot-runtime-seconds must be greater than zero")

    underlying_args = _underlying_contract_args(args)
    underlying_con_id = args.con_id
    if underlying_con_id is None:
        contract_details_request_id = gateway.request_contract_details(build_contract(underlying_args))
        if not gateway.wait_for_contract_details(contract_details_request_id, args.ready_timeout):
            return 1
        underlying_con_id = _load_underlying_con_id(args.db, args)
    if underlying_con_id is None:
        raise ValueError(f"could not resolve an underlying conId for {args.symbol}")

    option_chain_request_id = gateway.request_option_chain(
        args.symbol.upper(),
        args.sec_type.upper(),
        underlying_con_id,
        args.fut_fop_exchange,
    )
    if not gateway.wait_for_option_chain(option_chain_request_id, args.ready_timeout):
        return 1

    chain_snapshot = _load_option_chain_snapshot(args.db, option_chain_request_id)
    spot_price = _resolve_spot_price(gateway, args.db, args)
    selected_expiries = _select_option_expiries(chain_snapshot["expiries"], args.expiry_mode)
    selected_strikes = _select_strike_strip(chain_snapshot["strikes"], spot_price, args.strikes_around)

    request_ids: list[int] = []
    try:
        for expiry in selected_expiries:
            for strike in selected_strikes:
                for right in ("C", "P"):
                    option_args = _qualify_option_contract(
                        gateway,
                        args.db,
                        args,
                        expiry=expiry,
                        strike=strike,
                        right=right,
                        multiplier=chain_snapshot["multiplier"],
                        trading_class=chain_snapshot["trading_class"],
                    )
                    request_ids.append(
                        gateway.request_market_data(
                            build_contract(option_args),
                            args.generic_ticks,
                            False,
                            False,
                            args.market_data_type,
                        )
                    )

        print(
            f"collecting option greeks for {args.symbol.upper()} around spot {spot_price:.2f} across expiries {', '.join(selected_expiries)} and {len(selected_strikes)} strikes ({len(request_ids)} option contracts)",
            flush=True,
        )
        gateway.sleep_while_connected(args.runtime_seconds)
    finally:
        for request_id in request_ids:
            try:
                gateway.cancel_market_data_request(request_id)
            except Exception:
                pass

    synthetic_greeks = _backfill_option_greeks_from_ticks(args.db, request_ids, spot_price)
    if synthetic_greeks > 0:
        print(
            f"backfilled {synthetic_greeks} option greek rows from delayed option prices for {args.symbol.upper()}",
            flush=True,
        )

    print(
        f"finished collecting option greeks for {args.symbol.upper()} into {args.db}",
        flush=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    connection_parent = argparse.ArgumentParser(add_help=False)
    connection_parent.add_argument("--host", default="127.0.0.1")
    connection_parent.add_argument("--port", type=int, default=7497)
    connection_parent.add_argument("--client-id", type=int, default=7)
    connection_parent.add_argument("--ready-timeout", type=int, default=15)
    connection_parent.add_argument("--log-file", default="")
    connection_parent.add_argument("--db", default="")

    parser = argparse.ArgumentParser(
        description="Interactive Brokers JSONL gateway for market data, orders, and account events.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    contract_parent = argparse.ArgumentParser(add_help=False)
    contract_parent.add_argument("--symbol", required=True)
    contract_parent.add_argument("--sec-type", default="STK")
    contract_parent.add_argument("--exchange", default="SMART")
    contract_parent.add_argument("--primary-exchange", default="")
    contract_parent.add_argument("--currency", default="USD")
    contract_parent.add_argument("--expiry", default="")
    contract_parent.add_argument("--strike", type=float)
    contract_parent.add_argument("--right", default="")
    contract_parent.add_argument("--multiplier", default="")
    contract_parent.add_argument("--local-symbol", default="")
    contract_parent.add_argument("--trading-class", default="")
    contract_parent.add_argument("--con-id", type=int)

    market_data = subparsers.add_parser("market-data", parents=[connection_parent, contract_parent])
    market_data.add_argument("--generic-ticks", default="233")
    market_data.add_argument("--snapshot", type=parse_bool, default=False)
    market_data.add_argument("--regulatory-snapshot", type=parse_bool, default=False)
    market_data.add_argument("--market-data-type", type=parse_market_data_type, default=1)
    market_data.add_argument("--runtime-seconds", type=int, default=20)

    historical = subparsers.add_parser("historical", parents=[connection_parent, contract_parent])
    historical.add_argument("--end-date-time", default="")
    historical.add_argument("--duration", default="1 D")
    historical.add_argument("--bar-size", default="5 mins")
    historical.add_argument("--what-to-show", default="TRADES")
    historical.add_argument("--use-rth", type=int, default=1)
    historical.add_argument("--format-date", type=int, default=1)
    historical.add_argument("--keep-up-to-date", type=parse_bool, default=False)
    historical.add_argument("--poll-seconds", type=int, default=5)
    historical.add_argument("--runtime-seconds", type=int, default=20)

    contract_details = subparsers.add_parser("contract-details", parents=[connection_parent, contract_parent])
    contract_details.add_argument("--runtime-seconds", type=int, default=10)

    option_chain = subparsers.add_parser("option-chain", parents=[connection_parent])
    option_chain.add_argument("--underlying-symbol", required=True)
    option_chain.add_argument("--underlying-sec-type", default="STK")
    option_chain.add_argument("--underlying-con-id", type=int, required=True)
    option_chain.add_argument("--fut-fop-exchange", default="")
    option_chain.add_argument("--runtime-seconds", type=int, default=10)

    option_greeks_strip = subparsers.add_parser("option-greeks-strip", parents=[connection_parent, contract_parent])
    option_greeks_strip.add_argument("--market-data-type", type=parse_market_data_type, default=3)
    option_greeks_strip.add_argument("--generic-ticks", default="106,100,101")
    option_greeks_strip.add_argument("--fut-fop-exchange", default="")
    option_greeks_strip.add_argument("--expiry-mode", choices=["weekly", "monthly", "both"], default="both")
    option_greeks_strip.add_argument("--strikes-around", type=int, default=4)
    option_greeks_strip.add_argument("--spot-runtime-seconds", type=int, default=4)
    option_greeks_strip.add_argument("--runtime-seconds", type=int, default=20)

    account_summary = subparsers.add_parser("account-summary", parents=[connection_parent])
    account_summary.add_argument("--group-name", default="All")
    account_summary.add_argument("--tags", default=default_account_summary_tags())
    account_summary.add_argument("--runtime-seconds", type=int, default=10)

    positions = subparsers.add_parser("positions", parents=[connection_parent])
    positions.add_argument("--runtime-seconds", type=int, default=15)

    account_updates = subparsers.add_parser("account-updates", parents=[connection_parent])
    account_updates.add_argument("--account", required=True)
    account_updates.add_argument("--runtime-seconds", type=int, default=30)

    open_orders = subparsers.add_parser("open-orders", parents=[connection_parent])
    open_orders.add_argument("--all-clients", type=parse_bool, default=False)
    open_orders.add_argument("--runtime-seconds", type=int, default=10)

    place_limit = subparsers.add_parser("place-limit", parents=[connection_parent, contract_parent])
    place_limit.add_argument("--action", default="BUY")
    place_limit.add_argument("--quantity", type=parse_quantity, required=True)
    place_limit.add_argument("--order-type", default="LMT")
    place_limit.add_argument("--limit-price", type=ensure_limit_price, required=True)
    place_limit.add_argument("--tif", default="DAY")
    place_limit.add_argument("--transmit", type=parse_bool, default=False)
    place_limit.add_argument("--account", default="")
    place_limit.add_argument("--runtime-seconds", type=int, default=15)

    cancel_order = subparsers.add_parser("cancel-order", parents=[connection_parent])
    cancel_order.add_argument("--order-id", type=int, required=True)
    cancel_order.add_argument("--runtime-seconds", type=int, default=5)

    websocket_server = subparsers.add_parser("websocket-server", parents=[connection_parent])
    websocket_server.add_argument("--websocket-host", default="127.0.0.1")
    websocket_server.add_argument("--websocket-port", type=int, default=8765)

    return parser


def run_command(args: argparse.Namespace) -> int:
    if args.command == "historical" and args.keep_up_to_date and args.end_date_time:
        raise ValueError("IBKR does not allow --end-date-time together with --keep-up-to-date=true")

    gateway = IBGateway(log_file=args.log_file or None)
    event_store = SQLiteEventStore(args.db) if args.db else None
    historical_tracker = _HistoricalBarTracker() if args.command == "historical" else None
    try:
        if event_store is not None:
            gateway.add_event_listener(event_store.handle_event)
        if historical_tracker is not None:
            gateway.add_event_listener(historical_tracker.handle_event)
        gateway.connect_and_start(args.host, args.port, args.client_id, args.ready_timeout)

        if args.command == "market-data":
            request_id = gateway.request_market_data(
                build_contract(args),
                args.generic_ticks,
                args.snapshot,
                args.regulatory_snapshot,
                args.market_data_type,
            )
            _sleep_for_runtime(gateway, 12 if args.snapshot else args.runtime_seconds)
            if not args.snapshot:
                gateway.cancel_market_data_request(request_id)
            return 0

        if args.command == "historical":
            if args.keep_up_to_date:
                assert historical_tracker is not None
                return _run_historical_collector(gateway, args, historical_tracker)
            request_id = gateway.request_historical_data(
                build_contract(args),
                args.end_date_time,
                args.duration,
                args.bar_size,
                args.what_to_show.upper(),
                args.use_rth,
                args.format_date,
                False,
            )
            return 0 if gateway.wait_for_historical(request_id, args.runtime_seconds) else 1

        if args.command == "contract-details":
            request_id = gateway.request_contract_details(build_contract(args))
            return 0 if gateway.wait_for_contract_details(request_id, args.runtime_seconds) else 1

        if args.command == "option-chain":
            request_id = gateway.request_option_chain(
                args.underlying_symbol,
                args.underlying_sec_type.upper(),
                args.underlying_con_id,
                args.fut_fop_exchange,
            )
            return 0 if gateway.wait_for_option_chain(request_id, args.runtime_seconds) else 1

        if args.command == "option-greeks-strip":
            return _run_option_greeks_strip_collector(gateway, args)

        if args.command == "account-summary":
            request_id = gateway.request_account_summary(args.group_name, args.tags)
            completed = gateway.wait_for_account_summary(request_id, args.runtime_seconds)
            gateway.cancel_account_summary_request(request_id)
            return 0 if completed else 1

        if args.command == "positions":
            gateway.request_positions()
            completed = gateway.wait_for_positions(args.runtime_seconds)
            gateway.cancelPositions()
            gateway.emit_event("request.cancelPositions")
            return 0 if completed else 1

        if args.command == "account-updates":
            gateway.request_account_updates(args.account, True)
            completed = gateway.wait_for_account_download(args.runtime_seconds)
            if completed and args.runtime_seconds > 0:
                gateway.sleep_while_connected(args.runtime_seconds)
            gateway.request_account_updates(args.account, False)
            return 0 if completed else 1

        if args.command == "open-orders":
            gateway.request_open_orders(args.all_clients)
            return 0 if gateway.wait_for_open_orders(args.runtime_seconds) else 1

        if args.command == "place-limit":
            gateway.place_limit_order(
                build_contract(args),
                action=args.action,
                quantity=args.quantity,
                order_type=args.order_type,
                limit_price=args.limit_price,
                tif=args.tif,
                transmit=args.transmit,
                account=args.account,
            )
            gateway.sleep_while_connected(args.runtime_seconds)
            return 0

        if args.command == "cancel-order":
            gateway.cancel_order_request(args.order_id)
            gateway.sleep_while_connected(args.runtime_seconds)
            return 0

        if args.command == "websocket-server":
            asyncio.run(WebSocketBridge(gateway, args.websocket_host, args.websocket_port).serve_forever())
            return 0

        raise ValueError(f"unsupported command: {args.command}")
    finally:
        if event_store is not None:
            gateway.remove_event_listener(event_store.handle_event)
            event_store.close()
        gateway.shutdown()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_command(args)
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1