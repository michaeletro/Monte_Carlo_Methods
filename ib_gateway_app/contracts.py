from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

from ibapi.contract import Contract


def _apply_contract_fields(
    contract: Contract,
    *,
    primary_exchange: str = "",
    expiry: str = "",
    strike: float | None = None,
    right: str = "",
    multiplier: str = "",
    local_symbol: str = "",
    trading_class: str = "",
    con_id: int | None = None,
) -> None:
    contract.primaryExchange = primary_exchange.upper() if primary_exchange else ""
    contract.lastTradeDateOrContractMonth = expiry
    contract.right = right.upper() if right else ""
    contract.multiplier = multiplier
    contract.localSymbol = local_symbol
    contract.tradingClass = trading_class
    if strike is not None:
        contract.strike = strike
    if con_id is not None:
        contract.conId = con_id


def build_contract(args: argparse.Namespace) -> Contract:
    contract = Contract()
    contract.symbol = args.symbol
    contract.secType = args.sec_type.upper()
    contract.exchange = args.exchange.upper()
    contract.currency = args.currency.upper()
    _apply_contract_fields(
        contract,
        primary_exchange=args.primary_exchange,
        expiry=getattr(args, "expiry", ""),
        strike=getattr(args, "strike", None),
        right=getattr(args, "right", ""),
        multiplier=getattr(args, "multiplier", ""),
        local_symbol=getattr(args, "local_symbol", ""),
        trading_class=getattr(args, "trading_class", ""),
        con_id=getattr(args, "con_id", None),
    )
    return contract


def build_contract_from_payload(payload: Mapping[str, Any]) -> Contract:
    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")

    contract = Contract()
    contract.symbol = symbol
    contract.secType = str(payload.get("secType", "STK")).strip().upper()
    contract.exchange = str(payload.get("exchange", "SMART")).strip().upper()
    contract.currency = str(payload.get("currency", "USD")).strip().upper()
    strike_value = payload.get("strike")
    strike = float(strike_value) if strike_value not in {None, ""} else None
    con_id_value = payload.get("conId")
    con_id = int(con_id_value) if con_id_value not in {None, ""} else None
    _apply_contract_fields(
        contract,
        primary_exchange=str(payload.get("primaryExchange", "")).strip(),
        expiry=str(payload.get("expiry", payload.get("lastTradeDateOrContractMonth", ""))).strip(),
        strike=strike,
        right=str(payload.get("right", "")).strip(),
        multiplier=str(payload.get("multiplier", "")).strip(),
        local_symbol=str(payload.get("localSymbol", "")).strip(),
        trading_class=str(payload.get("tradingClass", "")).strip(),
        con_id=con_id,
    )
    return contract