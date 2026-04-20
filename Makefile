CXX := g++
CXXFLAGS := -std=c++17 -Wall -Wextra -pedantic -Iheader

RNG_TARGET := rng_test
RNG_SOURCES := main.cpp src/RandomNumGenerator/RandomGenerators.cpp

IB_TARGET := ib_gateway
IB_SOURCES := src/InteractiveBrokers/InteractiveBrokersGateway.cpp \
	src/InteractiveBrokers/ib_gateway_main.cpp
IB_API_ROOT ?=
IB_API_SOURCE_ROOT := $(IB_API_ROOT)/source/cppclient
IB_API_CLIENT_DIR := $(IB_API_SOURCE_ROOT)/client
IB_API_PROTO_DIR := $(IB_API_CLIENT_DIR)/protobufUnix
IB_API_SOURCES := $(wildcard $(IB_API_CLIENT_DIR)/*.cpp)
IB_API_PROTO_SOURCES := $(wildcard $(IB_API_PROTO_DIR)/*.cc)
PROTOBUF_CFLAGS ?= $(shell pkg-config --cflags protobuf 2>/dev/null)
PROTOBUF_LIBS ?= $(shell pkg-config --libs protobuf 2>/dev/null)
IB_CXXFLAGS := $(CXXFLAGS) \
	-I$(IB_API_SOURCE_ROOT) \
	-I$(IB_API_CLIENT_DIR) \
	-I$(IB_API_PROTO_DIR) \
	$(PROTOBUF_CFLAGS) \
	-Wno-switch \
	-Wno-unused-function \
	-Wno-unused-parameter \
	-pthread
IB_LDFLAGS := -L$(IB_API_SOURCE_ROOT) -Wl,-rpath,$(IB_API_SOURCE_ROOT)
IB_LIBS := -lbid $(PROTOBUF_LIBS) -pthread

.PHONY: all build run clean check_ib_api check_protobuf ib_build

all: build

build: $(RNG_TARGET)

ib_build: $(IB_TARGET)

$(RNG_TARGET): $(RNG_SOURCES)
	$(CXX) $(CXXFLAGS) $(RNG_SOURCES) -o $(RNG_TARGET)

check_ib_api:
	@test -f "$(IB_API_CLIENT_DIR)/EClientSocket.h" || \
		(echo "Set IB_API_ROOT to the extracted IBJts directory, for example: make $(IB_TARGET) IB_API_ROOT=/opt/IBJts" >&2; exit 1)
	@test -f "$(IB_API_PROTO_DIR)/ExecutionDetails.pb.h" || \
		(echo "The IBJts extraction is incomplete: missing protobuf-generated headers under $(IB_API_PROTO_DIR)" >&2; exit 1)

check_protobuf:
	@if pkg-config --exists protobuf 2>/dev/null; then \
		:; \
	elif [ -n "$(PROTOBUF_CFLAGS)" ] && [ -n "$(PROTOBUF_LIBS)" ]; then \
		:; \
	else \
		echo "Install protobuf development headers/libs or set PROTOBUF_CFLAGS and PROTOBUF_LIBS before building $(IB_TARGET)." >&2; \
		exit 1; \
	fi

$(IB_TARGET): check_ib_api check_protobuf $(IB_SOURCES)
	$(CXX) $(IB_CXXFLAGS) $(IB_SOURCES) $(IB_API_SOURCES) $(IB_API_PROTO_SOURCES) $(IB_LDFLAGS) $(IB_LIBS) -o $(IB_TARGET)

run: $(RNG_TARGET)
	./$(RNG_TARGET)

clean:
	rm -f $(RNG_TARGET) $(IB_TARGET)