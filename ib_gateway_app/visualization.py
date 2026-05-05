from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .contracts import build_contract
from .gateway import IBGateway
from .persistence import SQLiteEventStore


_SQLITE_BUSY_TIMEOUT_MS = 5000
_DASHBOARD_BOOTSTRAP_FORMAT_DATE = 1


def _add_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--sec-type", default="STK")
    parser.add_argument("--exchange", default="SMART")
    parser.add_argument("--primary-exchange", default="")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--expiry", default="")
    parser.add_argument("--strike", type=float)
    parser.add_argument("--right", default="")
    parser.add_argument("--multiplier", default="")
    parser.add_argument("--local-symbol", default="")
    parser.add_argument("--trading-class", default="")
    parser.add_argument("--con-id", type=int)


def build_visualization_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render interactive Plotly charts from the SQLite data captured by ib_gateway.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bars = subparsers.add_parser("bars", help="Render a candlestick chart from historical_prices.")
    bars.add_argument("--db", required=True)
    _add_contract_arguments(bars)
    bars.add_argument("--what-to-show", default="TRADES")
    bars.add_argument("--bar-size", default="")
    bars.add_argument("--use-rth", type=int, default=1)
    bars.add_argument("--limit", type=int, default=300)
    bars.add_argument("--output", default="")
    bars.add_argument("--open-browser", action="store_true")

    ticks = subparsers.add_parser("ticks", help="Render a line chart from market_data_ticks.")
    ticks.add_argument("--db", required=True)
    _add_contract_arguments(ticks)
    ticks.add_argument("--fields", default="Bid,Ask,Last")
    ticks.add_argument("--limit", type=int, default=500)
    ticks.add_argument("--output", default="")
    ticks.add_argument("--open-browser", action="store_true")

    dashboard = subparsers.add_parser("dashboard", help="Serve a live browser dashboard backed by the WebSocket bridge.")
    dashboard.add_argument("--db", default="data/ib_market_data.db")
    dashboard.add_argument("--web-host", default="127.0.0.1")
    dashboard.add_argument("--web-port", type=int, default=8000)
    dashboard.add_argument("--websocket-url", default="ws://127.0.0.1:8765")
    dashboard.add_argument("--symbol", default="AAPL")
    dashboard.add_argument("--sec-type", default="STK")
    dashboard.add_argument("--exchange", default="SMART")
    dashboard.add_argument("--currency", default="USD")
    dashboard.add_argument("--stream-mode", default="hybrid", choices=["historical", "hybrid"])
    dashboard.add_argument("--market-data-type", type=int, default=1)
    dashboard.add_argument("--duration", default="1800 S")
    dashboard.add_argument("--bar-size", default="5 secs")
    dashboard.add_argument("--use-rth", type=int, default=0)
    dashboard.add_argument("--poll-seconds", type=int, default=5)
    dashboard.add_argument("--generic-ticks", default="233")
    dashboard.add_argument("--ib-host", default="127.0.0.1")
    dashboard.add_argument("--ib-port", type=int, default=7497)
    dashboard.add_argument("--ib-client-id", type=int, default=11)
    dashboard.add_argument("--ib-ready-timeout", type=int, default=15)
    dashboard.add_argument("--bootstrap-timeout", type=int, default=20)
    dashboard.add_argument("--bootstrap-missing", type=int, choices=[0, 1], default=1)
    dashboard.add_argument("--open-browser", action="store_true")

    return parser


def _connect(database_path: str) -> sqlite3.Connection:
    path = Path(database_path)
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path}")
    connection = sqlite3.connect(path, timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000)
    connection.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
    connection.row_factory = sqlite3.Row
    return connection


def _contract_where_clause(args: argparse.Namespace) -> tuple[list[str], list[Any]]:
    clauses = [
        "i.symbol = ?",
        "i.sec_type = ?",
        "i.exchange = ?",
        "i.currency = ?",
    ]
    params: list[Any] = [
        args.symbol.upper(),
        args.sec_type.upper(),
        args.exchange.upper(),
        args.currency.upper(),
    ]

    optional_fields = [
        ("i.primary_exchange", args.primary_exchange.upper()),
        ("i.expiry", args.expiry),
        ("i.right_code", args.right.upper()),
        ("i.multiplier", args.multiplier),
        ("i.local_symbol", args.local_symbol),
        ("i.trading_class", args.trading_class),
    ]
    for column, value in optional_fields:
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)

    if args.strike is not None:
        clauses.append("i.strike = ?")
        params.append(args.strike)

    if args.con_id is not None:
        clauses.append("i.con_id = ?")
        params.append(args.con_id)

    return clauses, params


def _resolve_bar_size(connection: sqlite3.Connection, args: argparse.Namespace) -> str:
    if args.bar_size:
        return args.bar_size

    clauses, params = _contract_where_clause(args)
    where_clause = " AND ".join(clauses + ["hp.what_to_show = ?", "hp.use_rth = ?"])
    row = connection.execute(
        f"""
        SELECT hp.bar_size
        FROM historical_prices hp
        JOIN instruments i ON i.id = hp.instrument_id
        WHERE {where_clause}
        ORDER BY hp.created_at DESC, hp.id DESC
        LIMIT 1
        """,
        [*params, args.what_to_show.upper(), args.use_rth],
    ).fetchone()
    if row is None:
        raise ValueError("no historical bars matched the requested contract filters")
    return str(row[0])


def _fetch_bar_rows(connection: sqlite3.Connection, args: argparse.Namespace) -> tuple[list[sqlite3.Row], str]:
    bar_size = _resolve_bar_size(connection, args)
    clauses, params = _contract_where_clause(args)
    where_clause = " AND ".join(clauses + ["hp.what_to_show = ?", "hp.bar_size = ?", "hp.use_rth = ?"])
    rows = connection.execute(
        f"""
        SELECT *
        FROM (
            SELECT
                hp.bar_time,
                hp.open,
                hp.high,
                hp.low,
                hp.close,
                hp.volume,
                hp.trade_count,
                hp.what_to_show,
                hp.bar_size,
                hp.duration,
                hp.use_rth,
                i.symbol,
                i.sec_type,
                i.exchange,
                i.currency,
                i.expiry,
                i.strike,
                i.right_code,
                i.multiplier
            FROM historical_prices hp
            JOIN instruments i ON i.id = hp.instrument_id
            WHERE {where_clause}
            ORDER BY hp.bar_time DESC, hp.id DESC
            LIMIT ?
        ) recent
        ORDER BY bar_time ASC
        """,
        [*params, args.what_to_show.upper(), bar_size, args.use_rth, args.limit],
    ).fetchall()
    if not rows:
        raise ValueError("no historical bars matched the requested filters")
    return rows, bar_size


def _fetch_tick_rows(connection: sqlite3.Connection, args: argparse.Namespace) -> tuple[list[sqlite3.Row], list[str]]:
    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    if not fields:
        raise ValueError("--fields must include at least one market-data field")

    clauses, params = _contract_where_clause(args)
    placeholders = ", ".join("?" for _ in fields)
    where_clause = " AND ".join(clauses + [
        "m.event_type = 'marketData.tickPrice'",
        "m.price IS NOT NULL",
        f"m.field IN ({placeholders})",
    ])
    rows = connection.execute(
        f"""
        SELECT *
        FROM (
            SELECT
                m.created_at,
                m.field,
                m.price,
                i.symbol,
                i.sec_type,
                i.exchange,
                i.currency,
                i.expiry,
                i.strike,
                i.right_code,
                i.multiplier
            FROM market_data_ticks m
            JOIN instruments i ON i.id = m.instrument_id
            WHERE {where_clause}
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT ?
        ) recent
        ORDER BY created_at ASC, field ASC
        """,
        [*params, *fields, args.limit],
    ).fetchall()
    if not rows:
        raise ValueError("no market-data ticks matched the requested filters")
    return rows, fields


def _parse_timestamp(value: Any) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")

    numeric = text.replace(".", "", 1)
    if numeric.isdigit():
        if len(text) == 8:
            return datetime.strptime(text, "%Y%m%d")
        if len(text) >= 10:
            epoch_value = float(text)
            if len(text) >= 13:
                epoch_value /= 1000.0
            return datetime.fromtimestamp(epoch_value, tz=timezone.utc).replace(tzinfo=None)

    for fmt in (
        "%Y%m%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y%m%d",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as error:
        raise ValueError(f"unsupported timestamp format: {text}") from error


def _contract_label(row: sqlite3.Row) -> str:
    parts = [str(row["symbol"]), str(row["sec_type"]), str(row["exchange"])]
    if row["expiry"]:
        parts.append(str(row["expiry"]))
    if row["strike"] is not None:
        parts.append(str(row["strike"]))
    if row["right_code"]:
        parts.append(str(row["right_code"]))
    return " ".join(parts)


def _default_output_path(chart_type: str, symbol: str) -> Path:
    return Path("data") / "plots" / f"{symbol.lower()}-{chart_type}.html"


def _dashboard_directory() -> Path:
    return Path(__file__).resolve().parent.parent / "dashboard"


def _render_bars_chart(rows: list[sqlite3.Row], output_path: Path) -> None:
    timestamps = [_parse_timestamp(row["bar_time"]) for row in rows]
    opens = [float(row["open"]) for row in rows]
    highs = [float(row["high"]) for row in rows]
    lows = [float(row["low"]) for row in rows]
    closes = [float(row["close"]) for row in rows]
    volumes = [int(row["volume"]) for row in rows]

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.72, 0.28],
    )
    figure.add_trace(
        go.Candlestick(
            x=timestamps,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            name="OHLC",
            increasing_line_color="#0f766e",
            decreasing_line_color="#c2410c",
            increasing_fillcolor="#99f6e4",
            decreasing_fillcolor="#fdba74",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=timestamps,
            y=volumes,
            name="Volume",
            marker_color="#2563eb",
            opacity=0.75,
        ),
        row=2,
        col=1,
    )
    label = _contract_label(rows[0])
    figure.update_layout(
        template="plotly_white",
        title={
            "text": f"{label} historical bars",
            "x": 0.02,
            "xanchor": "left",
        },
        font={"family": "IBM Plex Sans, Arial, sans-serif", "size": 14},
        paper_bgcolor="#f7f3eb",
        plot_bgcolor="#fffdf8",
        legend={"orientation": "h", "y": 1.02, "x": 0.01},
        margin={"l": 48, "r": 28, "t": 72, "b": 40},
    )
    figure.update_xaxes(showgrid=True, gridcolor="#eadfce", rangeslider_visible=False)
    figure.update_yaxes(showgrid=True, gridcolor="#eadfce", row=1, col=1)
    figure.update_yaxes(showgrid=True, gridcolor="#eadfce", row=2, col=1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(output_path, include_plotlyjs="cdn")


def _render_ticks_chart(rows: list[sqlite3.Row], fields: list[str], output_path: Path) -> None:
    figure = go.Figure()
    palette = ["#2563eb", "#db2777", "#0f766e", "#9333ea", "#ea580c"]

    for index, field in enumerate(fields):
        matching_rows = [row for row in rows if row["field"] == field]
        if not matching_rows:
            continue
        timestamps = [_parse_timestamp(row["created_at"]) for row in matching_rows]
        prices = [float(row["price"]) for row in matching_rows]
        figure.add_trace(
            go.Scatter(
                x=timestamps,
                y=prices,
                mode="lines+markers",
                name=field,
                line={"width": 2.3, "color": palette[index % len(palette)]},
                marker={"size": 6},
            )
        )

    if not figure.data:
        raise ValueError("the selected tick fields did not produce any price series")

    label = _contract_label(rows[0])
    figure.update_layout(
        template="plotly_white",
        title={
            "text": f"{label} live tick prices",
            "x": 0.02,
            "xanchor": "left",
        },
        font={"family": "IBM Plex Sans, Arial, sans-serif", "size": 14},
        paper_bgcolor="#f7f3eb",
        plot_bgcolor="#fffdf8",
        legend={"orientation": "h", "y": 1.02, "x": 0.01},
        margin={"l": 48, "r": 28, "t": 72, "b": 40},
        hovermode="x unified",
    )
    figure.update_xaxes(showgrid=True, gridcolor="#eadfce")
    figure.update_yaxes(showgrid=True, gridcolor="#eadfce")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(output_path, include_plotlyjs="cdn")


def _finalize_output(output_path: Path, *, open_browser: bool) -> None:
    resolved = output_path.resolve()
    print(f"wrote visualization: {resolved}", flush=True)
    if open_browser:
        _maybe_open_browser(resolved.as_uri())


def _is_headless_linux() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return False
    return True


def _maybe_open_browser(target: str) -> None:
    if _is_headless_linux():
        print(f"open this URL in a browser: {target}", flush=True)
        return

    try:
        opened = webbrowser.open(target)
    except Exception:
        opened = False

    if not opened:
        print(f"open this URL in a browser: {target}", flush=True)


def _build_dashboard_url(args: argparse.Namespace) -> str:
    params = urlencode(
        {
            "websocketUrl": args.websocket_url,
            "symbol": args.symbol,
            "secType": args.sec_type,
            "exchange": args.exchange,
            "currency": args.currency,
            "streamMode": args.stream_mode,
            "marketDataType": args.market_data_type,
            "duration": args.duration,
            "barSize": args.bar_size,
            "useRTH": args.use_rth,
            "pollSeconds": args.poll_seconds,
            "genericTicks": args.generic_ticks,
            "autoStart": "1",
        }
    )
    return f"http://{args.web_host}:{args.web_port}/live_ticker.html?{params}"


def _optional_query_int(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _optional_query_float(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _dashboard_bar_query_args(query_params: dict[str, str]) -> argparse.Namespace:
    return argparse.Namespace(
        symbol=str(query_params.get("symbol", "")).strip().upper(),
        sec_type=str(query_params.get("secType", "STK")).strip().upper(),
        exchange=str(query_params.get("exchange", "SMART")).strip().upper(),
        primary_exchange=str(query_params.get("primaryExchange", "")).strip().upper(),
        currency=str(query_params.get("currency", "USD")).strip().upper(),
        expiry=str(query_params.get("expiry", "")).strip(),
        strike=_optional_query_float(str(query_params.get("strike", ""))),
        right=str(query_params.get("right", "")).strip().upper(),
        multiplier=str(query_params.get("multiplier", "")).strip(),
        local_symbol=str(query_params.get("localSymbol", "")).strip(),
        trading_class=str(query_params.get("tradingClass", "")).strip(),
        con_id=_optional_query_int(str(query_params.get("conId", ""))),
        duration=str(query_params.get("duration", "1800 S")).strip(),
        what_to_show=str(query_params.get("whatToShow", "TRADES")).strip().upper(),
        bar_size=str(query_params.get("barSize", "")).strip(),
        options_expiry_mode=str(query_params.get("optionsExpiryMode", "nearest")).strip().lower(),
        use_rth=_optional_query_int(str(query_params.get("useRTH", "0"))) or 0,
        limit=max(_optional_query_int(str(query_params.get("limit", "300"))) or 300, 1),
    )


def _parse_option_expiry(value: str) -> datetime | None:
    digits = "".join(character for character in str(value).strip() if character.isdigit())
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_monthly_expiry(expiry: datetime) -> bool:
    return expiry.weekday() == 4 and 15 <= expiry.day <= 21


def _standard_normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _implied_terminal_probability(*, spot_price: float, strike: float, implied_vol: float, expiry: str, right_code: str) -> float | None:
    if spot_price <= 0 or strike <= 0 or implied_vol <= 0:
        return None

    expiry_time = _parse_option_expiry(expiry)
    if expiry_time is None:
        return None

    expiry_close = expiry_time + timedelta(days=1)
    time_to_expiry_years = max((expiry_close - datetime.now(timezone.utc)).total_seconds(), 0.0) / (365.0 * 24.0 * 60.0 * 60.0)
    if time_to_expiry_years <= 0:
        if right_code.upper() == "P":
            return 1.0 if spot_price <= strike else 0.0
        return 1.0 if spot_price >= strike else 0.0

    sigma_sqrt_t = implied_vol * math.sqrt(time_to_expiry_years)
    if sigma_sqrt_t <= 0:
        return None

    d2 = (math.log(spot_price / strike) - 0.5 * (implied_vol**2) * time_to_expiry_years) / sigma_sqrt_t
    call_probability = _standard_normal_cdf(d2)
    if right_code.upper() == "P":
        return max(0.0, min(1.0, 1.0 - call_probability))
    return max(0.0, min(1.0, call_probability))


def _select_probability_expiry(rows: list[sqlite3.Row], expiry_mode: str = "nearest") -> str:
    expiries = sorted({str(row["expiry"]).strip() for row in rows if str(row["expiry"]).strip()})
    if not expiries:
        raise ValueError("no option expiry metadata matched the requested filters")

    now = datetime.now(timezone.utc)
    parsed_expiries = [(expiry, _parse_option_expiry(expiry)) for expiry in expiries]
    future_expiries = [
        (expiry, expiry_time)
        for expiry, expiry_time in parsed_expiries
        if expiry_time is not None and expiry_time + timedelta(days=1) >= now
    ]
    if future_expiries:
        future_expiries.sort(key=lambda item: item[1])
        weekly_expiry = future_expiries[0][0]
        monthly_expiry = next((expiry for expiry, expiry_time in future_expiries if _is_monthly_expiry(expiry_time)), weekly_expiry)
        if expiry_mode == "weekly":
            return weekly_expiry
        if expiry_mode == "monthly":
            return monthly_expiry
        return future_expiries[0][0]

    dated_expiries = [(expiry, expiry_time) for expiry, expiry_time in parsed_expiries if expiry_time is not None]
    if dated_expiries:
        dated_expiries.sort(key=lambda item: item[1])
        return dated_expiries[-1][0]

    return expiries[0]


def _fetch_options_probability_payload(connection: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    query = [
        "i.symbol = ?",
        "i.sec_type = 'OPT'",
        "i.exchange = ?",
        "i.currency = ?",
    ]
    params: list[Any] = [args.symbol.upper(), args.exchange.upper(), args.currency.upper()]
    if args.expiry:
        query.append("i.expiry = ?")
        params.append(args.expiry)

    rows = connection.execute(
        f"""
        WITH latest_option_greeks AS (
            SELECT
                og.id,
                og.instrument_id,
                og.implied_vol,
                og.delta,
                og.option_price,
                og.gamma,
                og.vega,
                og.theta,
                og.underlying_price,
                og.created_at,
                i.symbol,
                i.expiry,
                i.strike,
                i.right_code,
                ROW_NUMBER() OVER (
                    PARTITION BY og.instrument_id
                    ORDER BY
                        CASE WHEN og.implied_vol IS NOT NULL AND og.option_price IS NOT NULL THEN 0 ELSE 1 END,
                        CASE WHEN og.underlying_price IS NOT NULL THEN 0 ELSE 1 END,
                        og.created_at DESC,
                        og.id DESC
                ) AS row_number
            FROM option_greeks og
            JOIN instruments i ON i.id = og.instrument_id
            WHERE {' AND '.join(query)}
        )
        SELECT *
        FROM latest_option_greeks
        WHERE row_number = 1
        ORDER BY created_at DESC, strike ASC
        LIMIT ?
        """,
        [*params, max(min(args.limit, 1000), 1)],
    ).fetchall()

    if not rows:
        raise ValueError(f"no cached option greeks matched {args.symbol}; populate SQLite with option market-data requests first")

    selected_expiry = args.expiry or _select_probability_expiry(rows, getattr(args, "options_expiry_mode", "nearest"))
    selected_rows = [row for row in rows if str(row["expiry"]).strip() == selected_expiry]
    if not selected_rows:
        raise ValueError(f"no cached option greeks matched {args.symbol} expiry {selected_expiry}")

    spot_candidates = [float(row["underlying_price"]) for row in selected_rows if row["underlying_price"] is not None and float(row["underlying_price"]) > 0]
    spot_price = _median(spot_candidates)

    points: list[dict[str, Any]] = []
    for row in selected_rows:
        strike_value = row["strike"]
        implied_vol = row["implied_vol"]
        if strike_value is None or implied_vol is None:
            continue

        strike = float(strike_value)
        underlying_price = row["underlying_price"]
        if underlying_price is not None and float(underlying_price) > 0:
            spot = float(underlying_price)
        elif spot_price is not None and spot_price > 0:
            spot = float(spot_price)
        else:
            continue

        probability = _implied_terminal_probability(
            spot_price=spot,
            strike=strike,
            implied_vol=float(implied_vol),
            expiry=selected_expiry,
            right_code=str(row["right_code"]),
        )
        if probability is None:
            continue

        points.append(
            {
                "strike": strike,
                "right": str(row["right_code"]),
                "probability": probability,
                "impliedVol": float(implied_vol),
                "delta": float(row["delta"]) if row["delta"] is not None else None,
                "optionPrice": float(row["option_price"]) if row["option_price"] is not None else None,
                "underlyingPrice": spot,
                "createdAt": str(row["created_at"]),
            }
        )

    if not points:
        raise ValueError(f"no implied probability points could be computed for {args.symbol} expiry {selected_expiry}")

    latest_created_at = max((point["createdAt"] for point in points), default="")
    return {
        "cached": True,
        "symbol": args.symbol.upper(),
        "expiryMode": getattr(args, "options_expiry_mode", "nearest"),
        "expiry": selected_expiry,
        "spotPrice": spot_price,
        "updatedAt": latest_created_at,
        "points": sorted(points, key=lambda point: (point["right"], point["strike"])),
    }


class _HistoricalBootstrapWatcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[int, threading.Event] = {}
        self._errors: dict[int, tuple[int | None, str]] = {}

    def _event_for(self, request_id: int) -> threading.Event:
        with self._lock:
            return self._events.setdefault(request_id, threading.Event())

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "historical.end":
            request_id = event.get("reqId")
            if isinstance(request_id, int):
                self._event_for(request_id).set()
            return

        if event_type != "ib.error":
            return

        request_id = event.get("id")
        if not isinstance(request_id, int) or request_id < 0:
            return

        error_code = event.get("code")
        error_message = str(event.get("message", "IBKR historical request failed")).strip()
        with self._lock:
            self._errors[request_id] = (int(error_code) if isinstance(error_code, int) else None, error_message)
        self._event_for(request_id).set()

    def wait_for_request(self, request_id: int, timeout_seconds: int) -> tuple[bool, str | None]:
        request_event = self._event_for(request_id)
        if not request_event.wait(max(timeout_seconds, 0)):
            with self._lock:
                self._events.pop(request_id, None)
                self._errors.pop(request_id, None)
            return False, f"timed out waiting for IBKR historical data for request {request_id}"

        with self._lock:
            error = self._errors.pop(request_id, None)
            self._events.pop(request_id, None)

        if error is None:
            return True, None

        error_code, error_message = error
        if error_code is None:
            return False, error_message
        return False, f"IBKR historical request failed ({error_code}): {error_message}"


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        directory: str,
        database_path: str,
        dashboard_args: argparse.Namespace,
        bootstrap_lock: threading.Lock,
    ) -> None:
        self._database_path = database_path
        self._dashboard_args = dashboard_args
        self._bootstrap_lock = bootstrap_lock
        super().__init__(*args, directory=directory)

    def _bootstrap_historical_cache(self, args: argparse.Namespace) -> tuple[bool, str | None]:
        if not bool(self._dashboard_args.bootstrap_missing):
            return False, "no historical bars matched the requested contract filters"

        database_path = Path(self._database_path)
        with self._bootstrap_lock:
            try:
                with _connect(str(database_path)) as connection:
                    _fetch_bar_rows(connection, args)
                    return True, None
            except ValueError:
                pass

            gateway = IBGateway()
            event_store = SQLiteEventStore(self._database_path)
            watcher = _HistoricalBootstrapWatcher()
            request_id: int | None = None
            try:
                gateway.add_event_listener(event_store.handle_event)
                gateway.add_event_listener(watcher.handle_event)
                gateway.connect_and_start(
                    self._dashboard_args.ib_host,
                    self._dashboard_args.ib_port,
                    self._dashboard_args.ib_client_id,
                    self._dashboard_args.ib_ready_timeout,
                )
                request_id = gateway.request_historical_data(
                    build_contract(args),
                    "",
                    args.duration,
                    args.bar_size,
                    args.what_to_show.upper(),
                    args.use_rth,
                    _DASHBOARD_BOOTSTRAP_FORMAT_DATE,
                    False,
                )
                completed, message = watcher.wait_for_request(request_id, self._dashboard_args.bootstrap_timeout)
                if not completed:
                    gateway.cancel_historical_request(request_id)
                    return False, message
                return True, None
            except Exception as error:
                if request_id is not None and gateway.isConnected():
                    try:
                        gateway.cancel_historical_request(request_id)
                    except Exception:
                        pass
                return False, str(error)
            finally:
                event_store.close()
                gateway.shutdown()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/bars":
            self._handle_bars_api(parsed.query)
            return
        if parsed.path == "/api/options-probability":
            self._handle_options_probability_api(parsed.query)
            return
        super().do_GET()

    def _handle_bars_api(self, query_string: str) -> None:
        query_params = {key: values[-1] for key, values in parse_qs(query_string).items() if values}
        if not str(query_params.get("symbol", "")).strip():
            self._write_json(400, {"error": "symbol is required"})
            return

        database_path = Path(self._database_path)
        if not database_path.exists():
            self._write_json(200, {"cached": False, "bars": [], "message": f"database not found: {database_path}"})
            return

        args = _dashboard_bar_query_args(query_params)

        try:
            with _connect(str(database_path)) as connection:
                rows, bar_size = _fetch_bar_rows(connection, args)
        except ValueError as error:
            bootstrapped, bootstrap_message = self._bootstrap_historical_cache(args)
            if not bootstrapped:
                self._write_json(200, {"cached": True, "bars": [], "message": bootstrap_message or str(error)})
                return
            try:
                with _connect(str(database_path)) as connection:
                    rows, bar_size = _fetch_bar_rows(connection, args)
            except ValueError as retry_error:
                self._write_json(200, {"cached": True, "bars": [], "message": bootstrap_message or str(retry_error)})
                return
        except sqlite3.OperationalError as error:
            if "database is locked" in str(error).lower():
                self._write_json(503, {"cached": False, "bars": [], "message": "database is busy; retry shortly"})
                return
            raise

        self._write_json(
            200,
            {
                "cached": True,
                "barSize": bar_size,
                "bars": [
                    {
                        "time": str(row["bar_time"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row["volume"]),
                    }
                    for row in rows
                ],
            },
        )

    def _handle_options_probability_api(self, query_string: str) -> None:
        query_params = {key: values[-1] for key, values in parse_qs(query_string).items() if values}
        if not str(query_params.get("symbol", "")).strip():
            self._write_json(400, {"error": "symbol is required"})
            return

        database_path = Path(self._database_path)
        if not database_path.exists():
            self._write_json(200, {"cached": False, "points": [], "message": f"database not found: {database_path}"})
            return

        args = _dashboard_bar_query_args(query_params)
        try:
            with _connect(str(database_path)) as connection:
                payload = _fetch_options_probability_payload(connection, args)
        except ValueError as error:
            self._write_json(200, {"cached": True, "points": [], "message": str(error)})
            return
        except sqlite3.OperationalError as error:
            if "database is locked" in str(error).lower():
                self._write_json(503, {"cached": False, "points": [], "message": "database is busy; retry shortly"})
                return
            raise

        self._write_json(200, payload)

    def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve_dashboard(args: argparse.Namespace) -> int:
    dashboard_dir = _dashboard_directory()
    if not dashboard_dir.exists():
        raise FileNotFoundError(f"dashboard assets not found: {dashboard_dir}")

    handler = partial(
        DashboardRequestHandler,
        directory=str(dashboard_dir),
        database_path=args.db,
        dashboard_args=args,
        bootstrap_lock=threading.Lock(),
    )
    server = ThreadingHTTPServer((args.web_host, args.web_port), handler)
    dashboard_url = _build_dashboard_url(args)

    print(f"live dashboard ready: {dashboard_url}", flush=True)
    print(f"dashboard cache database: {Path(args.db).resolve()}", flush=True)
    print("press Ctrl+C to stop the dashboard server", flush=True)
    if args.open_browser:
        _maybe_open_browser(dashboard_url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping dashboard server", flush=True)
    finally:
        server.server_close()
    return 0


def run_visualization_command(args: argparse.Namespace) -> int:
    if args.command in {"bars", "ticks"} and args.limit <= 0:
        raise ValueError("--limit must be greater than zero")

    if args.command == "dashboard" and args.poll_seconds <= 0:
        raise ValueError("--poll-seconds must be greater than zero")

    if args.command == "dashboard" and args.bootstrap_timeout <= 0:
        raise ValueError("--bootstrap-timeout must be greater than zero")

    if args.command == "dashboard":
        return _serve_dashboard(args)

    with _connect(args.db) as connection:
        if args.command == "bars":
            rows, bar_size = _fetch_bar_rows(connection, args)
            output_path = Path(args.output) if args.output else _default_output_path("bars", args.symbol)
            _render_bars_chart(rows, output_path)
            print(
                f"rendered {len(rows)} bars for {rows[0]['symbol']} with bar size {bar_size}",
                flush=True,
            )
            _finalize_output(output_path, open_browser=args.open_browser)
            return 0

        if args.command == "ticks":
            rows, fields = _fetch_tick_rows(connection, args)
            output_path = Path(args.output) if args.output else _default_output_path("ticks", args.symbol)
            _render_ticks_chart(rows, fields, output_path)
            print(
                f"rendered {len(rows)} tick observations for {rows[0]['symbol']} across {', '.join(fields)}",
                flush=True,
            )
            _finalize_output(output_path, open_browser=args.open_browser)
            return 0

    raise ValueError(f"unsupported visualization command: {args.command}")


def main() -> int:
    parser = build_visualization_parser()
    args = parser.parse_args()
    try:
        return run_visualization_command(args)
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1