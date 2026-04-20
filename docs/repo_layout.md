# Repo Layout

This repository now separates the supported Interactive Brokers workflow from older demos, generated artifacts, and experimental code.

## Keep

- `ib_gateway`
- `ib_gateway.py`
- `ib_gateway_client.py`
- `ib_gateway_app/`
- `header/InteractiveBrokers/IBEventProcessor.hpp`
- `header/InteractiveBrokers/IBHistoricalStore.hpp`
- `src/InteractiveBrokers/IBEventProcessor.cpp`
- `src/InteractiveBrokers/ib_event_processor_main.cpp`
- `src/InteractiveBrokers/IBHistoricalStore.cpp`
- `src/InteractiveBrokers/ib_historical_store_main.cpp`
- `Makefile`
- `README.md`

## Experimental Or Demo

- `experimental/native_ib_cpp/header/InteractiveBrokers/InteractiveBrokersGateway.hpp`
- `experimental/native_ib_cpp/src/InteractiveBrokers/InteractiveBrokersGateway.cpp`
- `experimental/native_ib_cpp/src/InteractiveBrokers/ib_gateway_main.cpp`
- `experimental/one_factor_sde/`
- `experimental/random_num_generator/`
- `examples/ib_historical_runner.cpp`

## Removed Generated Artifacts

- `rng_test`
- `ib_historical_store`
- `ib_events.jsonl`
- `data/ib_market_data.db`
- `ib_gateway_app/__pycache__/`

## Ignore Going Forward

- Python cache files: `__pycache__/`, `*.pyc`
- Runtime data: `*.db`, `*.jsonl`
- Compiled binaries: `ib_event_processor`, `ib_historical_store`, `ib_gateway_cpp`, `rng_test`