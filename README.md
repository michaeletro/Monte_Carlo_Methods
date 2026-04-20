# Monte_Carlo_Methods

Monte Carlo experimentation in C++, now with a native Interactive Brokers gateway target for pulling market data, account state, and order status into the repo before model work starts.

## What is in the repo

- `rng_test`: the existing random-number generator demo.
- `ib_gateway`: a new Interactive Brokers CLI that connects to TWS or IB Gateway and emits JSONL events to stdout and an optional log file.
- `header/InteractiveBrokers` and `src/InteractiveBrokers`: the IBKR connectivity layer used by the CLI and available for future model integration.

The `OneFactorSDE` code is still scaffold-level and is not part of the default build target yet.

## Interactive Brokers setup

The official TWS C++ API is not vendored into this repository. Interactive Brokers distributes it under its own non-commercial license, so you need to download and extract it yourself.

1. Download the current Mac/Unix TWS API zip from the Interactive Brokers API site.
2. Extract it so you have a directory named `IBJts` available somewhere on your machine.
3. Install the Protobuf development package for your platform. On Debian or Ubuntu that is typically `libprotobuf-dev` and `protobuf-compiler`.
4. Start either Trader Workstation or IB Gateway.
5. In TWS or IB Gateway, enable API connections and allow the host you are connecting from.
6. Prefer a paper account and keep `--transmit false` until you are ready to send live orders.

Typical API ports:

- Paper TWS: `7497`
- Live TWS: `7496`
- Paper IB Gateway: `4002`
- Live IB Gateway: `4001`

## Build

Build the RNG demo:

```bash
make
```

Build the Interactive Brokers gateway:

```bash
make ib_gateway IB_API_ROOT=/path/to/IBJts
```

The Makefile expects `IB_API_ROOT` to point at the extracted `IBJts` directory, not the zip file.

If Protobuf is installed outside your default compiler paths, you can override detection explicitly:

```bash
make ib_gateway \
	IB_API_ROOT=/path/to/IBJts \
	PROTOBUF_CFLAGS="-I/custom/protobuf/include" \
	PROTOBUF_LIBS="-L/custom/protobuf/lib -lprotobuf"
```

## Gateway usage

The gateway prints newline-delimited JSON events so you can pipe them into logs, scripts, or future model runners.

Request streaming market data:

```bash
./ib_gateway market-data \
	--host 127.0.0.1 \
	--port 7497 \
	--client-id 7 \
	--symbol AAPL \
	--exchange SMART \
	--currency USD \
	--runtime-seconds 20
```

Request historical bars:

```bash
./ib_gateway historical \
	--symbol SPY \
	--duration "2 D" \
	--bar-size "5 mins" \
	--what-to-show TRADES
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

## Notes

- The gateway waits for `nextValidId` before sending requests so it does not race the IBKR handshake.
- Account updates are single-account subscriptions in the underlying API; changing accounts cancels the previous subscription.
- `market-data`, `historical`, `positions`, `account-updates`, `open-orders`, `place-limit`, and `cancel-order` are implemented now.
- The order command defaults to `--transmit false` for safety.
- Error `502` generally means TWS or IB Gateway is not listening on the host or port you configured.
