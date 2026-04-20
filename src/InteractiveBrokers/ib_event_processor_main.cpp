#include "InteractiveBrokers/IBEventProcessor.hpp"

#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

struct ProcessorOptions {
    std::string inputPath;
    std::string symbol;
    bool emitQuotes = false;
    bool emitBars = false;
};

void printUsage() {
    std::cout
        << "IB event processor\n\n"
        << "Reads JSONL events emitted by ./ib_gateway and emits normalized quote/bar events.\n\n"
        << "Options\n"
        << "  --input PATH      Read JSONL from a file instead of stdin\n"
        << "  --symbol SYMBOL   Only emit normalized output for one symbol\n"
        << "  --emit-quotes     Emit normalized.quote events\n"
        << "  --emit-bars       Emit normalized.bar events\n"
        << "  --help            Show this message\n\n"
        << "If neither --emit-quotes nor --emit-bars is provided, both are enabled.\n";
}

ProcessorOptions parseArguments(int argc, char** argv) {
    ProcessorOptions options;

    for (int index = 1; index < argc; ++index) {
        const std::string token = argv[index];
        if (token == "--help" || token == "-h") {
            printUsage();
            std::exit(0);
        }

        if (token == "--input") {
            if (index + 1 >= argc) {
                throw std::invalid_argument("Missing value for --input");
            }
            options.inputPath = argv[++index];
            continue;
        }

        if (token == "--symbol") {
            if (index + 1 >= argc) {
                throw std::invalid_argument("Missing value for --symbol");
            }
            options.symbol = argv[++index];
            continue;
        }

        if (token == "--emit-quotes") {
            options.emitQuotes = true;
            continue;
        }

        if (token == "--emit-bars") {
            options.emitBars = true;
            continue;
        }

        throw std::invalid_argument("Unknown option: " + token);
    }

    if (!options.emitQuotes && !options.emitBars) {
        options.emitQuotes = true;
        options.emitBars = true;
    }

    return options;
}

} // namespace

int main(int argc, char** argv) {
    try {
        const ProcessorOptions options = parseArguments(argc, argv);

        ibbridge::IBEventProcessor processor(std::cout, std::cerr);
        processor.setEmitQuotes(options.emitQuotes);
        processor.setEmitBars(options.emitBars);

        if (!options.symbol.empty()) {
            processor.setSymbolFilter(options.symbol);
        }

        if (options.inputPath.empty()) {
            return processor.processStream(std::cin) ? 0 : 1;
        }

        std::ifstream input(options.inputPath);
        if (!input) {
            std::cerr << "Unable to open input file: " << options.inputPath << std::endl;
            return 1;
        }

        return processor.processStream(input) ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}