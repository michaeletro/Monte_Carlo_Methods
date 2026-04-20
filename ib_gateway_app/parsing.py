from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from typing import Any


def parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def parse_quantity(value: str) -> str:
    try:
        quantity = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"invalid quantity: {value}") from error

    if quantity <= 0:
        raise argparse.ArgumentTypeError("quantity must be greater than zero")

    return value


def ensure_limit_price(value: str) -> float:
    try:
        price = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid limit price: {value}") from error

    if price <= 0.0:
        raise argparse.ArgumentTypeError("limit price must be greater than zero")
    return price


def parse_market_data_type(value: str) -> int:
    try:
        market_data_type = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid market data type: {value}") from error

    if market_data_type not in {1, 2, 3, 4}:
        raise argparse.ArgumentTypeError("market data type must be one of: 1, 2, 3, 4")

    return market_data_type


def default_account_summary_tags() -> str:
    return "AccountType,NetLiquidation,TotalCashValue,BuyingPower,AvailableFunds,ExcessLiquidity"


def coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return parse_bool(value)
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"{field_name} must be a boolean value")


def coerce_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be an integer") from error


def coerce_quantity(value: Any) -> str:
    return parse_quantity(str(value))


def coerce_limit_price(value: Any) -> float:
    return ensure_limit_price(str(value))


def coerce_market_data_type(value: Any) -> int:
    return parse_market_data_type(str(value))