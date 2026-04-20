#include "InteractiveBrokers/InteractiveBrokersGateway.hpp"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>

namespace {

struct CliArguments {
    std::string command;
    std::unordered_map<std::string, std::string> options;
};

std::string toUpper(std::string value) {
    std::transform(value.begin(),
                   value.end(),
                   value.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    return value;
}

std::string toLower(std::string value) {
    std::transform(value.begin(),
                   value.end(),
                   value.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    return value;
}

bool parseBool(const std::string& value) {
    const std::string lowered = toLower(value);
    if (lowered == "1" || lowered == "true" || lowered == "yes" || lowered == "on") {
        return true;
    }
    if (lowered == "0" || lowered == "false" || lowered == "no" || lowered == "off") {
        return false;
    }
    throw std::invalid_argument("Invalid boolean value: " + value);
}

int parseInt(const std::string& value) {
    std::size_t parsedChars = 0;
    const int parsedValue = std::stoi(value, &parsedChars);
    if (parsedChars != value.size()) {
        throw std::invalid_argument("Invalid integer value: " + value);
    }
    return parsedValue;
}

double parseDouble(const std::string& value) {
    std::size_t parsedChars = 0;
    const double parsedValue = std::stod(value, &parsedChars);
    if (parsedChars != value.size()) {
        throw std::invalid_argument("Invalid floating-point value: " + value);
    }
    return parsedValue;
}

std::string requireOption(const CliArguments& arguments, const std::string& key) {
    const auto iterator = arguments.options.find(key);
    if (iterator == arguments.options.end() || iterator->second.empty()) {
        throw std::invalid_argument("Missing required option --" + key);
    }
    return iterator->second;
}

std::string optionOrDefault(const CliArguments& arguments,
                            const std::string& key,
                            const std::string& defaultValue) {
    const auto iterator = arguments.options.find(key);
    return iterator == arguments.options.end() ? defaultValue : iterator->second;
}

int intOptionOrDefault(const CliArguments& arguments, const std::string& key, int defaultValue) {
    const auto iterator = arguments.options.find(key);
    return iterator == arguments.options.end() ? defaultValue : parseInt(iterator->second);
}

double doubleOptionOrDefault(const CliArguments& arguments,
                             const std::string& key,
                             double defaultValue) {
    const auto iterator = arguments.options.find(key);
    return iterator == arguments.options.end() ? defaultValue : parseDouble(iterator->second);
}

bool boolOptionOrDefault(const CliArguments& arguments,
                         const std::string& key,
                         bool defaultValue) {
    const auto iterator = arguments.options.find(key);
    return iterator == arguments.options.end() ? defaultValue : parseBool(iterator->second);
}

CliArguments parseArguments(int argc, char** argv) {
    CliArguments arguments;
    if (argc < 2) {
        arguments.command = "help";
        return arguments;
    }

    arguments.command = argv[1];
    for (int index = 2; index < argc; ++index) {
        const std::string token = argv[index];
        if (token == "--help" || token == "-h") {
            arguments.command = "help";
            arguments.options.clear();
            return arguments;
        }

        if (token.rfind("--", 0) != 0) {
            throw std::invalid_argument("Unexpected positional argument: " + token);
        }

        const std::string key = token.substr(2);
        if (key.empty()) {
            throw std::invalid_argument("Empty option name");
        }

        if (index + 1 < argc && std::string(argv[index + 1]).rfind("--", 0) != 0) {
            arguments.options[key] = argv[++index];
        } else {
            arguments.options[key] = "true";
        }
    }

    return arguments;
}

IBConnectionSettings buildConnectionSettings(const CliArguments& arguments) {
    IBConnectionSettings settings;
    settings.host = optionOrDefault(arguments, "host", settings.host);
    settings.port = intOptionOrDefault(arguments, "port", settings.port);
    settings.clientId = intOptionOrDefault(arguments, "client-id", settings.clientId);
    settings.readyTimeoutSeconds =
        intOptionOrDefault(arguments, "ready-timeout", settings.readyTimeoutSeconds);
    return settings;
}

IBContractSpec buildContractSpec(const CliArguments& arguments) {
    IBContractSpec contract;
    contract.symbol = requireOption(arguments, "symbol");
    contract.secType = toUpper(optionOrDefault(arguments, "sec-type", contract.secType));
    contract.exchange = toUpper(optionOrDefault(arguments, "exchange", contract.exchange));
    contract.primaryExchange = toUpper(optionOrDefault(arguments, "primary-exchange", ""));
    contract.currency = toUpper(optionOrDefault(arguments, "currency", contract.currency));
    return contract;
}

IBHistoricalDataRequest buildHistoricalRequest(const CliArguments& arguments) {
    IBHistoricalDataRequest request;
    request.endDateTime = optionOrDefault(arguments, "end-date-time", request.endDateTime);
    request.durationStr = optionOrDefault(arguments, "duration", request.durationStr);
    request.barSizeSetting = optionOrDefault(arguments, "bar-size", request.barSizeSetting);
    request.whatToShow = toUpper(optionOrDefault(arguments, "what-to-show", request.whatToShow));
    request.useRTH = intOptionOrDefault(arguments, "use-rth", request.useRTH);
    request.formatDate = intOptionOrDefault(arguments, "format-date", request.formatDate);
    request.keepUpToDate =
        boolOptionOrDefault(arguments, "keep-up-to-date", request.keepUpToDate);

    if (request.keepUpToDate && !request.endDateTime.empty()) {
        throw std::invalid_argument(
            "IBKR does not allow --end-date-time together with --keep-up-to-date=true");
    }

    return request;
}

IBOrderRequest buildOrderRequest(const CliArguments& arguments) {
    IBOrderRequest request;
    request.action = toUpper(optionOrDefault(arguments, "action", request.action));
    request.quantity = requireOption(arguments, "quantity");
    request.orderType = toUpper(optionOrDefault(arguments, "order-type", request.orderType));
    request.limitPrice = doubleOptionOrDefault(arguments, "limit-price", request.limitPrice);
    request.tif = toUpper(optionOrDefault(arguments, "tif", request.tif));
    request.transmit = boolOptionOrDefault(arguments, "transmit", request.transmit);
    request.account = optionOrDefault(arguments, "account", request.account);
    return request;
}

int runtimeSeconds(const CliArguments& arguments, int defaultValue) {
    return intOptionOrDefault(arguments, "runtime-seconds", defaultValue);
}

void sleepForRuntime(int seconds) {
    if (seconds > 0) {
        std::this_thread::sleep_for(std::chrono::seconds(seconds));
    }
}

void printUsage() {
    std::cout
        << "Interactive Brokers gateway commands\n\n"
        << "Connection options\n"
        << "  --host 127.0.0.1\n"
        << "  --port 7497              Paper TWS defaults to 7497, live TWS usually 7496\n"
        << "  --client-id 7\n"
        << "  --ready-timeout 15\n"
        << "  --log-file ib_events.jsonl\n\n"
        << "Contract options\n"
        << "  --symbol AAPL\n"
        << "  --sec-type STK\n"
        << "  --exchange SMART\n"
        << "  --primary-exchange NASDAQ\n"
        << "  --currency USD\n\n"
        << "Commands\n"
        << "  market-data    Stream or snapshot top-of-book data\n"
        << "  historical     Request historical bars\n"
        << "  positions      Request positions for all accessible accounts\n"
        << "  account-updates Subscribe to account and portfolio updates for one account\n"
        << "  open-orders    Request open orders for this client or all clients\n"
        << "  place-limit    Submit a limit order; transmit defaults to false\n"
        << "  cancel-order   Cancel an existing order id\n\n"
        << "Examples\n"
        << "  ./ib_gateway market-data --symbol AAPL --runtime-seconds 20\n"
        << "  ./ib_gateway historical --symbol SPY --duration \"2 D\" --bar-size \"5 mins\"\n"
        << "  ./ib_gateway account-updates --account DU123456 --runtime-seconds 30\n"
        << "  ./ib_gateway place-limit --symbol AAPL --action BUY --quantity 10 --limit-price 175.25 --account DU123456 --transmit false\n"
        << "  ./ib_gateway cancel-order --order-id 12345\n";
}

} // namespace

int main(int argc, char** argv) {
    try {
        const CliArguments arguments = parseArguments(argc, argv);
        if (arguments.command == "help") {
            printUsage();
            return 0;
        }

        InteractiveBrokersGateway gateway;
        const std::string logFile = optionOrDefault(arguments, "log-file", "");
        if (!logFile.empty()) {
            gateway.setEventLogFile(logFile);
        }

        if (!gateway.connect(buildConnectionSettings(arguments))) {
            return 1;
        }

        if (arguments.command == "market-data") {
            const IBContractSpec contract = buildContractSpec(arguments);
            const bool snapshot = boolOptionOrDefault(arguments, "snapshot", false);
            const bool regulatorySnapshot =
                boolOptionOrDefault(arguments, "regulatory-snapshot", false);
            const std::string genericTicks = optionOrDefault(arguments, "generic-ticks", "233");
            const int requestId =
                gateway.requestMarketData(contract, genericTicks, snapshot, regulatorySnapshot);

            sleepForRuntime(runtimeSeconds(arguments, snapshot ? 12 : 20));
            if (!snapshot) {
                gateway.cancelMarketData(requestId);
            }
            gateway.disconnect();
            return 0;
        }

        if (arguments.command == "historical") {
            const IBContractSpec contract = buildContractSpec(arguments);
            const IBHistoricalDataRequest request = buildHistoricalRequest(arguments);
            const int requestId = gateway.requestHistoricalData(contract, request);
            const int timeoutSeconds = runtimeSeconds(arguments, request.keepUpToDate ? 30 : 20);

            if (request.keepUpToDate) {
                sleepForRuntime(timeoutSeconds);
                gateway.cancelHistoricalData(requestId);
                gateway.disconnect();
                return 0;
            }

            const bool completed =
                gateway.waitForHistoricalDataEnd(requestId, std::chrono::seconds(timeoutSeconds));
            gateway.disconnect();
            return completed ? 0 : 1;
        }

        if (arguments.command == "positions") {
            gateway.requestPositions();
            const bool completed = gateway.waitForPositionsEnd(
                std::chrono::seconds(runtimeSeconds(arguments, 15)));
            gateway.cancelPositions();
            gateway.disconnect();
            return completed ? 0 : 1;
        }

        if (arguments.command == "account-updates") {
            const std::string account = requireOption(arguments, "account");
            gateway.requestAccountUpdates(account, true);
            const int timeout = runtimeSeconds(arguments, 30);
            const bool completed =
                gateway.waitForAccountDownloadEnd(std::chrono::seconds(timeout));
            if (completed) {
                sleepForRuntime(timeout);
            }
            gateway.requestAccountUpdates(account, false);
            gateway.disconnect();
            return completed ? 0 : 1;
        }

        if (arguments.command == "open-orders") {
            const bool allClients = boolOptionOrDefault(arguments, "all-clients", false);
            gateway.requestOpenOrders(allClients);
            const bool completed = gateway.waitForOpenOrdersEnd(
                std::chrono::seconds(runtimeSeconds(arguments, 10)));
            gateway.disconnect();
            return completed ? 0 : 1;
        }

        if (arguments.command == "place-limit") {
            const IBContractSpec contract = buildContractSpec(arguments);
            const IBOrderRequest orderRequest = buildOrderRequest(arguments);
            gateway.placeLimitOrder(contract, orderRequest);
            sleepForRuntime(runtimeSeconds(arguments, 15));
            gateway.disconnect();
            return 0;
        }

        if (arguments.command == "cancel-order") {
            gateway.cancelOrder(intOptionOrDefault(arguments, "order-id", -1));
            sleepForRuntime(runtimeSeconds(arguments, 5));
            gateway.disconnect();
            return 0;
        }

        gateway.disconnect();
        throw std::invalid_argument("Unknown command: " + arguments.command);
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}