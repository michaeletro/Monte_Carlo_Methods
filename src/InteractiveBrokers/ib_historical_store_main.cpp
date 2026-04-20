#include "InteractiveBrokers/IBHistoricalStore.hpp"

#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

namespace {

struct Options {
    std::string databasePath = "data/ib_market_data.db";
    ibbridge::HistoricalFetchRequest request;
};

void printUsage() {
    std::cout
        << "IB historical storage runner\n\n"
        << "Fetches contract metadata and historical bars from ./ib_gateway and stores them in SQLite.\n\n"
        << "Options\n"
        << "  --db PATH                 SQLite database path\n"
        << "  --gateway-path PATH       Path to the ib_gateway launcher\n"
        << "  --host HOST               IBKR host\n"
        << "  --port PORT               IBKR port\n"
        << "  --client-id ID            IBKR client id\n"
        << "  --ready-timeout SECONDS   Connection timeout\n"
        << "  --symbol SYMBOL           Contract symbol\n"
        << "  --sec-type TYPE           Contract type, default STK\n"
        << "  --exchange EXCHANGE       Contract exchange, default SMART\n"
        << "  --primary-exchange EXCH   Primary exchange\n"
        << "  --currency CCY            Contract currency, default USD\n"
        << "  --expiry YYYYMMDD         Option or future expiry\n"
        << "  --strike VALUE            Option strike\n"
        << "  --right C|P               Option right\n"
        << "  --multiplier VALUE        Contract multiplier\n"
        << "  --local-symbol VALUE      IB local symbol\n"
        << "  --trading-class VALUE     IB trading class\n"
        << "  --con-id ID               Explicit IB contract id\n"
        << "  --end-date-time VALUE     Historical end timestamp\n"
        << "  --duration VALUE          Historical duration, default 1 D\n"
        << "  --bar-size VALUE          Historical bar size, default 5 mins\n"
        << "  --what-to-show VALUE      Historical source, default TRADES\n"
        << "  --use-rth 0|1             Restrict to regular trading hours\n"
        << "  --format-date 1|2         IB historical date format\n"
        << "  --keep-up-to-date true|false  Streaming historical updates\n"
        << "  --help                    Show this message\n";
}

bool parseBool(const std::string& value) {
    if (value == "1" || value == "true" || value == "TRUE" || value == "yes") {
        return true;
    }
    if (value == "0" || value == "false" || value == "FALSE" || value == "no") {
        return false;
    }
    throw std::invalid_argument("Invalid boolean value: " + value);
}

template <typename T>
T requireValue(const std::string& option, int argc, char** argv, int& index, T (*converter)(const std::string&)) {
    if (index + 1 >= argc) {
        throw std::invalid_argument("Missing value for " + option);
    }
    return converter(argv[++index]);
}

std::string asString(const std::string& value) {
    return value;
}

int asInt(const std::string& value) {
    return std::stoi(value);
}

double asDouble(const std::string& value) {
    return std::stod(value);
}

Options parseArguments(int argc, char** argv) {
    Options options;

    for (int index = 1; index < argc; ++index) {
        const std::string token = argv[index];
        if (token == "--help" || token == "-h") {
            printUsage();
            std::exit(0);
        }

        if (token == "--db") {
            options.databasePath = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--gateway-path") {
            options.request.gatewayPath = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--host") {
            options.request.host = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--port") {
            options.request.port = requireValue(token, argc, argv, index, asInt);
            continue;
        }
        if (token == "--client-id") {
            options.request.clientId = requireValue(token, argc, argv, index, asInt);
            continue;
        }
        if (token == "--ready-timeout") {
            options.request.readyTimeoutSeconds = requireValue(token, argc, argv, index, asInt);
            continue;
        }
        if (token == "--symbol") {
            options.request.symbol = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--sec-type") {
            options.request.secType = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--exchange") {
            options.request.exchange = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--primary-exchange") {
            options.request.primaryExchange = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--currency") {
            options.request.currency = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--expiry") {
            options.request.expiry = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--strike") {
            options.request.strike = requireValue(token, argc, argv, index, asDouble);
            continue;
        }
        if (token == "--right") {
            options.request.right = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--multiplier") {
            options.request.multiplier = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--local-symbol") {
            options.request.localSymbol = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--trading-class") {
            options.request.tradingClass = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--con-id") {
            options.request.conId = requireValue(token, argc, argv, index, asInt);
            continue;
        }
        if (token == "--end-date-time") {
            options.request.endDateTime = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--duration") {
            options.request.duration = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--bar-size") {
            options.request.barSize = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--what-to-show") {
            options.request.whatToShow = requireValue(token, argc, argv, index, asString);
            continue;
        }
        if (token == "--use-rth") {
            options.request.useRTH = requireValue(token, argc, argv, index, asInt);
            continue;
        }
        if (token == "--format-date") {
            options.request.formatDate = requireValue(token, argc, argv, index, asInt);
            continue;
        }
        if (token == "--keep-up-to-date") {
            options.request.keepUpToDate = requireValue(token, argc, argv, index, parseBool);
            continue;
        }

        throw std::invalid_argument("Unknown option: " + token);
    }

    if (options.request.symbol.empty()) {
        throw std::invalid_argument("--symbol is required");
    }

    return options;
}

} // namespace

int main(int argc, char** argv) {
    try {
        const Options options = parseArguments(argc, argv);
        const ibbridge::HistoricalStorageService service(options.databasePath);
        const ibbridge::HistoricalStoreResult result = service.fetchAndStore(options.request);

        std::cout << "Database path: " << options.databasePath << "\n";
        std::cout << "Symbol: " << result.instrument.symbol << "\n";
        std::cout << "Security type: " << result.instrument.secType << "\n";
        std::cout << "Parsed events: " << result.parsedEvents << "\n";
        std::cout << "Stored bars: " << result.bars.size() << "\n";

        if (!result.instrument.longName.empty()) {
            std::cout << "Long name: " << result.instrument.longName << "\n";
        }
        if (result.instrument.conId.has_value()) {
            std::cout << "ConId: " << *result.instrument.conId << "\n";
        }

        if (!result.bars.empty()) {
            const ibbridge::HistoricalBarRecord& first = result.bars.front();
            const ibbridge::HistoricalBarRecord& last = result.bars.back();
            std::cout << "First bar: " << first.time << " close=" << std::fixed << std::setprecision(4) << first.close << "\n";
            std::cout << "Last bar: " << last.time << " close=" << std::fixed << std::setprecision(4) << last.close << "\n";
        }

        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}