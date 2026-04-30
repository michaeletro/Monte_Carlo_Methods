# Monte_Carlo_Methods

Monte Carlo experimentation in C++, now with a Python Interactive Brokers gateway for connectivity, a native C++ event processor for downstream market-data and historical-data handling, and a browser dashboard prototype for cached market views.

## What is in the repo

- `ib_gateway`: the primary Interactive Brokers CLI entrypoint. It connects to TWS or IB Gateway and emits JSONL events to stdout and an optional log file.
- `ib_event_processor`: the supported native C++ processing stage. It consumes the gateway JSONL stream and emits normalized quote and bar events for heavier downstream processing.
- `ib_historical_store`: a native C++ persistence stage that calls `./ib_gateway`, fetches contract metadata and historical bars, and stores them in SQLite.
- `dashboard/live_ticker.html`: a browser dashboard prototype that reads cached bars from a local `/api/bars` endpoint and can overlay live ticks from the gateway WebSocket bridge.
- `experimental/native_ib_cpp`: the deprecated native C++ IBKR wrapper kept for reference, but not the recommended path on modern Linux because of upstream protobuf compatibility issues in the deprecated C++ SDK.
- `experimental/one_factor_sde`: scaffold-level SDE code kept out of the supported build path.
- `experimental/random_num_generator`: older random-number generator code retained as reference material instead of a default build target.
- `examples/ib_historical_runner.cpp`: a standalone example that shells out to `./ib_gateway` and parses historical-bar JSONL.

For a concrete keep-versus-clean inventory, see `docs/repo_layout.md`.

## Interactive Brokers setup

1. Install the official Python IB API in the Python you will use to run the gateway.
2. Install `websockets` as well if you want the gateway to expose a local WebSocket server.
3. Start either Trader Workstation or IB Gateway.
4. In TWS or IB Gateway, enable API connections and allow the host you are connecting from.
5. Prefer a paper account and keep `--transmit false` until you are ready to send live orders.

On Ubuntu or Debian:

```bash
python3 -m pip install ibapi websockets
```

If the repo has a local virtual environment at `.venv`, `make ib_gateway` and `./ib_gateway` will prefer that interpreter automatically.

Typical API ports:

- Paper TWS: `7497`
- Live TWS: `7496`
- Paper IB Gateway: `4002`
- Live IB Gateway: `4001`

## Build

Build the supported C++ processing stage:

```bash
make
```

This now builds the supported C++ event processor `ib_event_processor`.

Prepare the Interactive Brokers gateway:

```bash
make ib_gateway
```

This target verifies that `ibapi` is importable and marks `./ib_gateway` executable.

Build only the C++ processing stage:

```bash
make ib_processor
```

Build the SQLite historical storage runner:

```bash
make historical_store
```

This target produces `./ib_historical_store` and needs SQLite plus the single-header `nlohmann/json.hpp` dependency available to the compiler.

## Experimental native C++ target

The deprecated IBKR C++ SDK is still available as an experimental target if you need it, but on modern Linux distributions it can fail because the SDK ships protobuf-generated code tied to older protobuf versions. The source for that target now lives under `experimental/native_ib_cpp`.

```bash
make ib_gateway_cpp IB_API_ROOT=/path/to/IBJts
```

If you are intentionally building the native C++ target and protobuf is installed outside your default compiler paths, you can override detection explicitly:

```bash
make ib_gateway_cpp \
	IB_API_ROOT=/path/to/IBJts \
	PROTOBUF_CFLAGS="-I/custom/protobuf/include" \
	PROTOBUF_LIBS="-L/custom/protobuf/lib -lprotobuf"
```

## Supported Architecture

The recommended path in this repo is:

1. Python `ib_gateway` handles TWS or IB Gateway connectivity and emits flat JSONL events.
2. C++ `ib_event_processor` consumes that JSONL stream and performs the heavier normalization and processing work.
3. Optional C++ `ib_historical_store` persists contract metadata and historical bars into SQLite when you need a storage runner.

This avoids the deprecated IBKR C++ SDK while keeping the performance-sensitive stage in C++.

## Repo Layout

- `ib_gateway.py`, `ib_gateway`, and `ib_gateway_app/`: supported Python gateway and WebSocket bridge.
- `src/InteractiveBrokers/IBEventProcessor.cpp` and `src/InteractiveBrokers/ib_event_processor_main.cpp`: supported C++ JSONL processor.
- `src/InteractiveBrokers/IBHistoricalStore.cpp` and `src/InteractiveBrokers/ib_historical_store_main.cpp`: supported C++ SQLite historical-store runner.
- `dashboard/live_ticker.html`: browser dashboard prototype for SQLite-backed bar views plus optional live tick overlay.
- `experimental/native_ib_cpp/`: deprecated C++ SDK path retained for reference.
- `experimental/one_factor_sde/` and `experimental/random_num_generator/`: non-default Monte Carlo scaffolding and older demos.
- `examples/`: standalone examples that are not part of the default build.

Generated artifacts such as `.pyc`, SQLite databases, JSONL logs, and compiled binaries are intentionally ignored and should stay untracked.

## Dashboard prototype

The repo now includes `dashboard/live_ticker.html`, a chart-first browser UI for viewing cached price history.

- It reads bar history from a same-origin `/api/bars` endpoint backed by the SQLite cache.
- It can optionally overlay live bid/ask/last ticks from the local gateway WebSocket bridge, defaulting to `ws://127.0.0.1:8765`.
- The controls in the page adjust the browser polling loop and cache query parameters; they do not create extra IB sessions by themselves.

This file is a front-end prototype only. The repo does not currently ship a matching HTTP server for `/api/bars`, so serve it alongside your own local cache API if you want the chart to populate from SQLite.

## Gateway usage

The gateway prints newline-delimited JSON events so you can pipe them into logs, scripts, or future model runners.

Interactive Brokers itself does not speak WebSocket on port `7497`. Port `7497` is the TWS API TCP socket. If you need a WebSocket endpoint for another app, run the local bridge below; it connects to TWS on `7497` and exposes a separate WebSocket server, defaulting to `ws://127.0.0.1:8765`.

Start the local WebSocket bridge against paper TWS:

```bash
./ib_gateway websocket-server \
	--db data/ib_market_data.db \
	--host 127.0.0.1 \
	--port 7497 \
	--client-id 7 \
	--websocket-host 127.0.0.1 \
	--websocket-port 8765
```

Example client messages for the bridge:

```json
{"type":"market-data.subscribe","symbol":"AAPL","secType":"STK","exchange":"SMART","currency":"USD","snapshot":false}
{"type":"historical.request","symbol":"SPY","duration":"2 D","barSize":"5 mins","whatToShow":"TRADES"}
{"type":"contract-details.request","symbol":"AAPL","secType":"STK","exchange":"SMART","currency":"USD"}
{"type":"option-chain.request","underlyingSymbol":"AAPL","underlyingSecType":"STK","underlyingConId":265598}
{"type":"positions.request"}
{"type":"account-summary.request","groupName":"All","tags":"AccountType,NetLiquidation,BuyingPower,AvailableFunds"}
{"type":"open-orders.request","allClients":true}
```

The bridge sends back command acknowledgements such as `websocket.commandAccepted` and forwards the normal IBKR events like `connection.ready`, `marketData.tickPrice`, `historical.bar`, `option.greeks`, `option.chain`, `portfolio.position`, and `order.status` to every connected WebSocket client.

If you prefer a small reusable client wrapper instead of writing WebSocket payloads by hand, see `ib_gateway_client.py`. It exposes one async method per supported request.

Request streaming market data:

```bash
./ib_gateway market-data \
	--db data/ib_market_data.db \
	--host 127.0.0.1 \
	--port 7497 \
	--client-id 7 \
	--symbol AAPL \
	--exchange SMART \
	--currency USD \
	--runtime-seconds 20
```

Request delayed market data if your account has no live API entitlement:

```bash
./ib_gateway market-data \
	--host 127.0.0.1 \
	--port 7497 \
	--client-id 7 \
	--symbol AAPL \
	--exchange SMART \
	--currency USD \
	--market-data-type 3 \
	--runtime-seconds 20
```

Request historical bars:

```bash
./ib_gateway historical \
	--db data/ib_market_data.db \
	--symbol SPY \
	--duration "2 D" \
	--bar-size "5 mins" \
	--what-to-show TRADES
```

Request historical option prices for a specific contract:

```bash
./ib_gateway historical \
	--symbol AAPL \
	--sec-type OPT \
	--exchange SMART \
	--currency USD \
	--expiry 20260515 \
	--strike 200 \
	--right C \
	--multiplier 100 \
	--duration "2 D" \
	--bar-size "5 mins" \
	--what-to-show TRADES
```

Request contract details:

```bash
./ib_gateway contract-details \
	--symbol AAPL \
	--exchange SMART \
	--currency USD
```

Request option-chain metadata for an underlying:

```bash
./ib_gateway option-chain \
	--underlying-symbol AAPL \
	--underlying-sec-type STK \
	--underlying-con-id 265598
```

Stream live option greeks for a specific option contract:

```bash
./ib_gateway market-data \
	--symbol AAPL \
	--sec-type OPT \
	--exchange SMART \
	--currency USD \
	--expiry 20260515 \
	--strike 200 \
	--right C \
	--multiplier 100 \
	--runtime-seconds 20
```

That market-data request emits normal ticks and `option.greeks` events when IB provides option computation data.

IB exposes historical option prices through `historical`, but it does not expose a native historical greek time series through this gateway. If you need historical delta, gamma, vega, theta, or implied-vol paths, you have to reconstruct them from historical option prices, underlying prices, rates, dividends, and a pricing model, or source them from a separate data vendor.

Request account summary values:

```bash
./ib_gateway account-summary \
	--group-name All \
	--tags "AccountType,NetLiquidation,BuyingPower,AvailableFunds"
```

Subscribe to account updates:

```bash
./ib_gateway account-updates \
	--account DU123456 \
	--runtime-seconds 30
```

Inspect open orders:

```bash
./ib_gateway open-orders --all-clients true
```

Stage a limit order without transmitting it:

```bash
./ib_gateway place-limit \
	--symbol AAPL \
	--action BUY \
	--quantity 10 \
	--limit-price 175.25 \
	--account DU123456 \
	--transmit false
```

Cancel an order:

```bash
./ib_gateway cancel-order --order-id 12345
```

Persist the JSONL event stream to a file:

```bash
./ib_gateway market-data --symbol AAPL --log-file ib_events.jsonl
```

Persist gateway requests and response events into SQLite directly from Python:

```bash
./ib_gateway market-data \
	--db data/ib_market_data.db \
	--host 172.23.80.1 \
	--port 7497 \
	--client-id 7 \
	--symbol AAPL \
	--runtime-seconds 20
```

With `--db`, the Python gateway writes all emitted events into `raw_gateway_events` and also persists normalized rows for contract metadata, historical bars, live market-data ticks, option greeks, option-chain snapshots, and account summary rows.

Persist historical bars and instrument metadata into SQLite from C++:

```bash
./ib_historical_store \
	--db data/ib_market_data.db \
	--host 172.23.80.1 \
	--port 7497 \
	--client-id 7 \
	--symbol AAPL \
	--duration "5 D" \
	--bar-size "5 mins" \
	--what-to-show TRADES
```

This creates two normalized tables instead of one table per ticker:

- `instruments` stores contract metadata such as symbol, exchange, currency, conId, long name, trading class, and hours metadata.
- `historical_prices` stores the bar history keyed by instrument id, bar time, source type, and bar size.

If you use `./ib_gateway --db ...` or `./ib_gateway websocket-server --db ...`, the same SQLite file also receives:

- `raw_gateway_events` for every emitted request and response event.
- `market_data_ticks` for live market data and market data mode changes.
- `option_greeks` for live option computation events.
- `option_chain_snapshots` for option-chain metadata requests.
- `account_summaries` for account-summary responses.

That schema is more stable than creating a separate SQL table per stock. If you later want stock-specific views, you can create SQL views on top of the normalized tables.

## API Reference

### CLI commands

| Command | Purpose | Key arguments |
| --- | --- | --- |
| `market-data` | Request streaming or snapshot market data | `--symbol`, `--generic-ticks`, `--snapshot`, `--regulatory-snapshot`, `--market-data-type` |
| `historical` | Request historical bars for stocks, options, and other supported contracts | `--symbol`, `--sec-type`, `--expiry`, `--strike`, `--right`, `--duration`, `--bar-size`, `--what-to-show`, `--use-rth`, `--format-date`, `--keep-up-to-date` |
| `contract-details` | Request instrument metadata for a specific contract | `--symbol`, `--sec-type`, `--exchange`, `--primary-exchange`, `--currency`, `--expiry`, `--strike`, `--right` |
| `option-chain` | Request option-chain metadata for an underlying | `--underlying-symbol`, `--underlying-sec-type`, `--underlying-con-id`, `--fut-fop-exchange` |
| `positions` | Request current positions | none beyond connection options |
| `account-updates` | Subscribe to account value and portfolio updates | `--account` |
| `account-summary` | Request account summary tags | `--group-name`, `--tags` |
| `open-orders` | Request open orders | `--all-clients` |
| `place-limit` | Submit a limit order | `--symbol`, `--action`, `--quantity`, `--limit-price`, `--account`, `--transmit` |
| `cancel-order` | Cancel an order | `--order-id` |
| `websocket-server` | Start the local WebSocket bridge | `--websocket-host`, `--websocket-port` |

### WebSocket requests

| Request type | Purpose | Important fields |
| --- | --- | --- |
| `ping` | Health check | none |
| `market-data.subscribe` | Start market data, including option greeks for option contracts | `symbol`, `secType`, `exchange`, `currency`, `expiry`, `strike`, `right`, `multiplier`, `genericTicks`, `snapshot`, `regulatorySnapshot`, `marketDataType` |
| `market-data.unsubscribe` | Cancel market data | `reqId` |
| `historical.request` | Start historical bars | `symbol`, `secType`, `expiry`, `strike`, `right`, `multiplier`, `duration`, `barSize`, `whatToShow`, `useRTH`, `formatDate`, `keepUpToDate` |
| `historical.cancel` | Cancel historical bars | `reqId` |
| `contract-details.request` | Request instrument metadata | `symbol`, `secType`, `exchange`, `currency`, `primaryExchange`, `expiry`, `strike`, `right`, `multiplier`, `conId` |
| `option-chain.request` | Request option-chain metadata for an underlying | `underlyingSymbol`, `underlyingSecType`, `underlyingConId`, `futFopExchange` |
| `positions.request` | Request positions | none |
| `account-updates.subscribe` | Subscribe account updates | `account` |
| `account-updates.unsubscribe` | Cancel account updates | `account` |
| `account-summary.request` | Request account summary | `groupName`, `tags` |
| `account-summary.cancel` | Cancel account summary stream | `reqId` |
| `open-orders.request` | Request open orders | `allClients` |
| `order.place-limit` | Submit a limit order | `symbol`, `action`, `quantity`, `limitPrice`, `account`, `transmit` |
| `order.cancel` | Cancel an order | `orderId` |

### Main event types emitted by the gateway

| Event type | Meaning |
| --- | --- |
| `connection.managedAccounts` | Accounts reported by IBKR during login |
| `connection.ready` | Connection handshake completed and `nextValidId` arrived |
| `connection.closed` | TWS or gateway socket closed |
| `request.*` | Local request issued by the gateway |
| `marketData.type` | Server-confirmed market data mode for a request |
| `marketData.tickPrice` | Price tick update |
| `marketData.tickSize` | Size tick update |
| `marketData.tickString` | String-valued tick update |
| `option.greeks` | Live option computation payload for an option market-data subscription |
| `historical.bar` | Historical bar payload |
| `historical.end` | Historical request complete |
| `option.chain` | Option-chain metadata batch containing expirations and strikes |
| `option.chainEnd` | Option-chain request complete |
| `contract.details` | Instrument metadata row |
| `contract.detailsEnd` | Contract details request complete |
| `portfolio.position` | Position update |
| `portfolio.positionEnd` | Positions request complete |
| `account.value` | Account value update |
| `account.portfolio` | Account portfolio update |
| `account.time` | Account timestamp update |
| `account.downloadEnd` | Account updates initial download complete |
| `account.summary` | Account summary tag/value row |
| `account.summaryEnd` | Account summary request complete |
| `order.open` | Open order row |
| `order.openEnd` | Open orders request complete |
| `order.status` | Order status update |
| `ib.notice` | Informational IBKR message |
| `ib.error` | IBKR error |
| `websocket.ready` | Local WebSocket server is listening |
| `websocket.connected` | A WebSocket client connected |
| `websocket.commandAccepted` | A WebSocket request was accepted |
| `websocket.commandRejected` | A WebSocket request was rejected |
| `pong` | Reply to `ping` |

## C++ Processing Usage

Normalize a saved gateway log in C++:

```bash
./ib_event_processor --input ib_events.jsonl
```

Normalize only quote updates for one symbol:

```bash
./ib_event_processor --input ib_events.jsonl --symbol AAPL --emit-quotes
```

Normalize only historical bars:

```bash
./ib_event_processor --input ib_events.jsonl --emit-bars
```

Run the live Python-to-C++ pipeline for streaming quote normalization:

```bash
./ib_gateway market-data \
	--host 127.0.0.1 \
	--port 7497 \
	--client-id 7 \
	--symbol AAPL \
	--exchange SMART \
	--currency USD \
	--runtime-seconds 20 | ./ib_event_processor --emit-quotes --symbol AAPL
```

Run the live pipeline for historical bars:

```bash
./ib_gateway historical \
	--host 127.0.0.1 \
	--port 7497 \
	--client-id 7 \
	--symbol SPY \
	--duration "2 D" \
	--bar-size "5 mins" \
	--what-to-show TRADES | ./ib_event_processor --emit-bars --symbol SPY
```

## Notes

- The gateway waits for `nextValidId` before sending requests so it does not race the IBKR handshake.
- Account updates are single-account subscriptions in the underlying API; changing accounts cancels the previous subscription.
- `market-data`, `historical`, `contract-details`, `positions`, `account-updates`, `account-summary`, `open-orders`, `place-limit`, and `cancel-order` are implemented now.
- The order command defaults to `--transmit false` for safety.
- Error `502` generally means TWS or IB Gateway is not listening on the host or port you configured.
- Error `10089` generally means the account lacks the required API market-data entitlement for that product or venue.
- `make ib_gateway` plus `make ib_processor` is the supported architecture on Ubuntu 24 and similar systems.
- `ib_event_processor` understands the flat JSONL emitted by `ib_gateway` and joins `reqId` values back to symbols for normalized quote and historical-bar output.
- `make ib_gateway_cpp` remains experimental and is upstream-SDK-dependent; it may require an older protobuf toolchain.
