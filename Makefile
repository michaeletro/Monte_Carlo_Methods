CXX := g++
CXXFLAGS := -std=c++17 -Wall -Wextra -Iheader
TARGET := rng_test
SOURCES := main.cpp src/RandomGenerators.cpp

.PHONY: all build run clean

all: build

build: $(TARGET)

$(TARGET): $(SOURCES)
	$(CXX) $(CXXFLAGS) $(SOURCES) -o $(TARGET)

run: $(TARGET)
	./$(TARGET)

clean:
	rm -f $(TARGET)