CXX := g++
CXXFLAGS := -std=c++17 -Wall -Wextra -pedantic -Iheader
IB_PYTHON ?= $(shell if [ -x "$(CURDIR)/.venv/bin/python" ]; then printf '%s' "$(CURDIR)/.venv/bin/python"; else printf '%s' python3; fi)

IB_PROCESSOR_TARGET := ib_event_processor
IB_PROCESSOR_SOURCES := src/InteractiveBrokers/IBEventProcessor.cpp \
	src/InteractiveBrokers/ib_event_processor_main.cpp

IB_HISTORICAL_STORE_TARGET := ib_historical_store
IB_HISTORICAL_STORE_SOURCES := src/InteractiveBrokers/IBHistoricalStore.cpp \
	src/InteractiveBrokers/ib_historical_store_main.cpp
SQLITE_LIBS ?= -lsqlite3

EXPERIMENTAL_IB_CPP_ROOT := experimental/native_ib_cpp
IB_CPP_TARGET := ib_gateway_cpp
IB_CPP_SOURCES := $(EXPERIMENTAL_IB_CPP_ROOT)/src/InteractiveBrokers/InteractiveBrokersGateway.cpp \
	$(EXPERIMENTAL_IB_CPP_ROOT)/src/InteractiveBrokers/ib_gateway_main.cpp
IB_API_ROOT ?=
IB_API_SOURCE_ROOT := $(IB_API_ROOT)/source/cppclient
IB_API_CLIENT_DIR := $(IB_API_SOURCE_ROOT)/client
IB_API_PROTO_DIR := $(IB_API_CLIENT_DIR)/protobufUnix
IB_API_SOURCES := $(wildcard $(IB_API_CLIENT_DIR)/*.cpp)
IB_API_PROTO_SOURCES := $(wildcard $(IB_API_PROTO_DIR)/*.cc)
PROTOBUF_CFLAGS ?= $(shell pkg-config --cflags protobuf 2>/dev/null)
PROTOBUF_LIBS ?= $(shell pkg-config --libs protobuf 2>/dev/null)
IB_CPP_CXXFLAGS := $(CXXFLAGS) \
	-I$(EXPERIMENTAL_IB_CPP_ROOT)/header \
	-I$(IB_API_SOURCE_ROOT) \
	-I$(IB_API_CLIENT_DIR) \
	-I$(IB_API_PROTO_DIR) \
	$(PROTOBUF_CFLAGS) \
	-Wno-switch \
	-Wno-unused-function \
	-Wno-unused-parameter \
	-pthread
IB_CPP_LDFLAGS := -L$(IB_API_SOURCE_ROOT) -Wl,-rpath,$(IB_API_SOURCE_ROOT)
IB_CPP_LIBS := -lbid $(PROTOBUF_LIBS) -pthread

.PHONY: all build clean check_ib_python check_plotly check_ib_api check_protobuf ib_gateway ib_visualize ib_build ib_gateway_cpp ib_processor historical_store

all: build

build: $(IB_PROCESSOR_TARGET)

ib_gateway: check_ib_python
	@chmod +x ib_gateway ib_gateway.py
	@echo "Interactive Brokers gateway is ready: ./ib_gateway"

ib_visualize: check_plotly
	@chmod +x ib_visualize ib_visualize.py
	@echo "Interactive Brokers visualization CLI is ready: ./ib_visualize"

ib_build: $(IB_CPP_TARGET)

ib_processor: $(IB_PROCESSOR_TARGET)

historical_store: $(IB_HISTORICAL_STORE_TARGET)

$(IB_PROCESSOR_TARGET): $(IB_PROCESSOR_SOURCES)
	$(CXX) $(CXXFLAGS) $(IB_PROCESSOR_SOURCES) -o $(IB_PROCESSOR_TARGET)

$(IB_HISTORICAL_STORE_TARGET): $(IB_HISTORICAL_STORE_SOURCES)
	$(CXX) $(CXXFLAGS) $(IB_HISTORICAL_STORE_SOURCES) $(SQLITE_LIBS) -o $(IB_HISTORICAL_STORE_TARGET)

check_ib_python:
	@$(IB_PYTHON) -c "import ibapi" >/dev/null 2>&1 || \
		(echo "Install Python package 'ibapi' for $(IB_PYTHON) before running make ib_gateway." >&2; exit 1)

check_plotly:
	@$(IB_PYTHON) -c "import plotly" >/dev/null 2>&1 || \
		(echo "Install Python package 'plotly' for $(IB_PYTHON) before running make ib_visualize." >&2; exit 1)

check_ib_api:
	@test -f "$(IB_API_CLIENT_DIR)/EClientSocket.h" || \
		(echo "Set IB_API_ROOT to the extracted IBJts directory, for example: make $(IB_CPP_TARGET) IB_API_ROOT=/opt/IBJts" >&2; exit 1)
	@test -f "$(IB_API_PROTO_DIR)/ExecutionDetails.pb.h" || \
		(echo "The IBJts extraction is incomplete: missing protobuf-generated headers under $(IB_API_PROTO_DIR)" >&2; exit 1)

check_protobuf:
	@if pkg-config --exists protobuf 2>/dev/null; then \
		:; \
	elif [ -n "$(PROTOBUF_CFLAGS)" ] && [ -n "$(PROTOBUF_LIBS)" ]; then \
		:; \
	else \
		echo "Install protobuf development headers/libs or set PROTOBUF_CFLAGS and PROTOBUF_LIBS before building $(IB_CPP_TARGET)." >&2; \
		exit 1; \
	fi

$(IB_CPP_TARGET): check_ib_api check_protobuf $(IB_CPP_SOURCES)
	$(CXX) $(IB_CPP_CXXFLAGS) $(IB_CPP_SOURCES) $(IB_API_SOURCES) $(IB_API_PROTO_SOURCES) $(IB_CPP_LDFLAGS) $(IB_CPP_LIBS) -o $(IB_CPP_TARGET)

clean:
	rm -f rng_test $(IB_PROCESSOR_TARGET) $(IB_HISTORICAL_STORE_TARGET) $(IB_CPP_TARGET)