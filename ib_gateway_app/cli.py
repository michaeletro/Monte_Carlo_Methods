from __future__ import annotations

import argparse
import asyncio
import sys

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
    historical.add_argument("--runtime-seconds", type=int, default=20)

    contract_details = subparsers.add_parser("contract-details", parents=[connection_parent, contract_parent])
    contract_details.add_argument("--runtime-seconds", type=int, default=10)

    option_chain = subparsers.add_parser("option-chain", parents=[connection_parent])
    option_chain.add_argument("--underlying-symbol", required=True)
    option_chain.add_argument("--underlying-sec-type", default="STK")
    option_chain.add_argument("--underlying-con-id", type=int, required=True)
    option_chain.add_argument("--fut-fop-exchange", default="")
    option_chain.add_argument("--runtime-seconds", type=int, default=10)

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
    try:
        if event_store is not None:
            gateway.add_event_listener(event_store.handle_event)
        gateway.connect_and_start(args.host, args.port, args.client_id, args.ready_timeout)

        if args.command == "market-data":
            request_id = gateway.request_market_data(
                build_contract(args),
                args.generic_ticks,
                args.snapshot,
                args.regulatory_snapshot,
                args.market_data_type,
            )
            gateway.sleep_while_connected(12 if args.snapshot else args.runtime_seconds)
            if not args.snapshot:
                gateway.cancel_market_data_request(request_id)
            return 0

        if args.command == "historical":
            request_id = gateway.request_historical_data(
                build_contract(args),
                args.end_date_time,
                args.duration,
                args.bar_size,
                args.what_to_show.upper(),
                args.use_rth,
                args.format_date,
                args.keep_up_to_date,
            )
            if args.keep_up_to_date:
                gateway.sleep_while_connected(args.runtime_seconds)
                gateway.cancel_historical_request(request_id)
                return 0
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