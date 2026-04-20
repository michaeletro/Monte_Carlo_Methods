/*
    File: ib_historical_runner.cpp

    Purpose

    This program runs your existing IB gateway command,
    captures the JSON line output,
    filters for "historical.bar" events,
    stores them in C++ structs,
    and performs sample operations in main().

    Build example

    g++ -std=c++17 -O2 ib_historical_runner.cpp -o ib_historical_runner

    Dependency

    This example uses nlohmann::json.
    If you do not already have it installed, install the single header
    or your package manager version and adjust the include path if needed.
*/

#include <cstdio>          // For popen, pclose, fgets
#include <cstdlib>         // For general C standard utilities
#include <cstring>         // For std::strlen
#include <iomanip>         // For std::setprecision, std::fixed
#include <iostream>        // For std::cout, std::cerr
#include <numeric>         // For std::accumulate
#include <optional>        // For std::optional
#include <sstream>         // For std::ostringstream
#include <stdexcept>       // For std::runtime_error
#include <string>          // For std::string
#include <vector>          // For std::vector

#ifdef __unix__
#include <sys/wait.h>      // For WIFEXITED, WEXITSTATUS on Unix
#endif

#include <nlohmann/json.hpp>

using json = nlohmann::json;

/*
    This struct stores only the fields we care about for bar analysis.
*/
struct HistoricalBar {
    int reqId = 0;
    std::string time;
    double open = 0.0;
    double high = 0.0;
    double low = 0.0;
    double close = 0.0;
    long long volume = 0;
    int count = 0;
};

/*
    This struct stores the full command result.
    bars holds only parsed historical bars.
    allEvents holds every JSON event we successfully parsed.
*/
struct CommandResult {
    std::vector<HistoricalBar> bars;
    std::vector<json> allEvents;
    int rawReturnCode = 0;
    int exitCode = 0;
};

/*
    Remove a trailing newline or carriage return from a line.
*/
void trimLineEnding(std::string& line) {
    while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) {
        line.pop_back();
    }
}

/*
    Convert a string to long long safely.
    If the field is empty or invalid, return 0.
*/
long long parseVolumeString(const std::string& value) {
    if (value.empty()) {
        return 0;
    }

    try {
        return std::stoll(value);
    } catch (...) {
        return 0;
    }
}

/*
    Try to parse one line as JSON.
    If parsing fails, return std::nullopt instead of throwing.
*/
std::optional<json> tryParseJsonLine(const std::string& line) {
    try {
        return json::parse(line);
    } catch (...) {
        return std::nullopt;
    }
}

/*
    Convert one JSON event into a HistoricalBar.
    This assumes the event type is already known to be historical.bar.
*/
HistoricalBar toHistoricalBar(const json& event) {
    HistoricalBar bar;

    bar.reqId = event.value("reqId", 0);
    bar.time = event.value("time", "");
    bar.open = event.value("open", 0.0);
    bar.high = event.value("high", 0.0);
    bar.low = event.value("low", 0.0);
    bar.close = event.value("close", 0.0);
    bar.volume = parseVolumeString(event.value("volume", std::string{}));
    bar.count = event.value("count", 0);

    return bar;
}

/*
    Decode the process return code into a cleaner exit code.
    On Unix, pclose returns encoded status information.
    On non Unix systems, we just return the raw code.
*/
int decodeExitCode(int rawReturnCode) {
#ifdef __unix__
    if (WIFEXITED(rawReturnCode)) {
        return WEXITSTATUS(rawReturnCode);
    }
#endif
    return rawReturnCode;
}

/*
    Run the shell command, capture stdout, and parse JSON line by line.

    Important

    1. This uses popen, so it is a shell invocation.
    2. Only use this with trusted command strings.
    3. The working directory matters because your command uses ./ib_gateway.
*/
CommandResult runHistoricalCommand(const std::string& command) {
    CommandResult result;

    FILE* pipe = popen(command.c_str(), "r");
    if (!pipe) {
        throw std::runtime_error("Failed to open pipe with popen()");
    }

    char buffer[4096];

    while (fgets(buffer, sizeof(buffer), pipe) != nullptr) {
        std::string line(buffer);
        trimLineEnding(line);

        if (line.empty()) {
            continue;
        }

        std::optional<json> parsed = tryParseJsonLine(line);
        if (!parsed.has_value()) {
            /*
                Ignore non JSON lines.
                If you want strict behavior, throw instead.
            */
            continue;
        }

        const json& event = parsed.value();
        result.allEvents.push_back(event);

        const std::string type = event.value("type", "");
        if (type == "historical.bar") {
            result.bars.push_back(toHistoricalBar(event));
        }
    }

    result.rawReturnCode = pclose(pipe);
    result.exitCode = decodeExitCode(result.rawReturnCode);

    return result;
}

/*
    Compute average close across all bars.
*/
double computeAverageClose(const std::vector<HistoricalBar>& bars) {
    if (bars.empty()) {
        return 0.0;
    }

    const double sumClose = std::accumulate(
        bars.begin(),
        bars.end(),
        0.0,
        [](double acc, const HistoricalBar& bar) {
            return acc + bar.close;
        }
    );

    return sumClose / static_cast<double>(bars.size());
}

/*
    Compute total traded volume across all bars.
*/
long long computeTotalVolume(const std::vector<HistoricalBar>& bars) {
    return std::accumulate(
        bars.begin(),
        bars.end(),
        0LL,
        [](long long acc, const HistoricalBar& bar) {
            return acc + bar.volume;
        }
    );
}

/*
    Compute simple close to close return from first bar to last bar.
*/
double computeSessionReturn(const std::vector<HistoricalBar>& bars) {
    if (bars.size() < 2 || bars.front().close == 0.0) {
        return 0.0;
    }

    return (bars.back().close / bars.front().close) - 1.0;
}

/*
    Print a few close to close returns for inspection.
*/
void printSampleReturns(const std::vector<HistoricalBar>& bars, std::size_t maxRows = 10) {
    if (bars.size() < 2) {
        std::cout << "Not enough bars to compute close to close returns.\n";
        return;
    }

    std::cout << "\nSample close to close returns\n";

    const std::size_t upper = std::min(maxRows + 1, bars.size());
    for (std::size_t i = 1; i < upper; ++i) {
        const double prevClose = bars[i - 1].close;
        const double currClose = bars[i].close;

        if (prevClose == 0.0) {
            continue;
        }

        const double simpleReturn = (currClose / prevClose) - 1.0;

        std::cout
            << bars[i].time
            << "  return = "
            << std::fixed
            << std::setprecision(6)
            << simpleReturn
            << "\n";
    }
}

int main() {
    try {
        /*
            This is your exact command rewritten as one shell string.

            Important

            Run this C++ program from the same directory where ./ib_gateway exists,
            or replace ./ib_gateway with the full absolute path.
        */
        const std::string command =
            R"(./ib_gateway historical --host 172.23.80.1 --port 7497 --client-id 7 --symbol AAPL --duration "1 D" --bar-size "5 mins" --what-to-show TRADES)";

        /*
            Run the command and capture the structured output.
        */
        CommandResult result = runHistoricalCommand(command);

        /*
            Basic process diagnostics.
        */
        std::cout << "Process exit code: " << result.exitCode << "\n";
        std::cout << "Parsed JSON events: " << result.allEvents.size() << "\n";
        std::cout << "Parsed historical bars: " << result.bars.size() << "\n";

        /*
            If no bars came back, stop early.
        */
        if (result.bars.empty()) {
            std::cerr << "No historical bars were parsed.\n";
            return 1;
        }

        /*
            Sample operation 1

            Print the first and last bar.
        */
        const HistoricalBar& first = result.bars.front();
        const HistoricalBar& last = result.bars.back();

        std::cout << "\nFirst bar\n";
        std::cout
            << "time   = " << first.time << "\n"
            << "open   = " << first.open << "\n"
            << "high   = " << first.high << "\n"
            << "low    = " << first.low << "\n"
            << "close  = " << first.close << "\n"
            << "volume = " << first.volume << "\n";

        std::cout << "\nLast bar\n";
        std::cout
            << "time   = " << last.time << "\n"
            << "open   = " << last.open << "\n"
            << "high   = " << last.high << "\n"
            << "low    = " << last.low << "\n"
            << "close  = " << last.close << "\n"
            << "volume = " << last.volume << "\n";

        /*
            Sample operation 2

            Compute average close across the session.
        */
        const double averageClose = computeAverageClose(result.bars);
        std::cout
            << "\nAverage close = "
            << std::fixed
            << std::setprecision(6)
            << averageClose
            << "\n";

        /*
            Sample operation 3

            Compute total volume.
        */
        const long long totalVolume = computeTotalVolume(result.bars);
        std::cout << "Total volume  = " << totalVolume << "\n";

        /*
            Sample operation 4

            Compute session return from the first bar close to the last bar close.
        */
        const double sessionReturn = computeSessionReturn(result.bars);
        std::cout
            << "Session return from first close to last close = "
            << std::fixed
            << std::setprecision(6)
            << sessionReturn
            << "\n";

        /*
            Sample operation 5

            Print a few close to close returns.
        */
        printSampleReturns(result.bars, 10);

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Fatal error: " << ex.what() << "\n";
        return 1;
    }
}