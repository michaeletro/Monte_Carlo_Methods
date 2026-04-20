#pragma once

#include <cstddef>
#include <iosfwd>
#include <optional>
#include <string>
#include <unordered_map>

namespace ibbridge {

struct FlatJsonObject {
    std::unordered_map<std::string, std::string> values;

    const std::string* find(const std::string& key) const;
    bool contains(const std::string& key) const;
};

class FlatJsonParser {
public:
    static bool parseLine(const std::string& line, FlatJsonObject& object, std::string& error);
};

struct QuoteState {
    std::string symbol;
    std::optional<double> bid;
    std::optional<double> ask;
    std::optional<double> last;
    std::optional<double> close;
    std::optional<std::string> bidSize;
    std::optional<std::string> askSize;
    std::optional<std::string> lastSize;
};

class IBEventProcessor {
public:
    IBEventProcessor(std::ostream& output, std::ostream& diagnostics);

    void setSymbolFilter(const std::string& symbol);
    void setEmitQuotes(bool emitQuotes);
    void setEmitBars(bool emitBars);

    bool processStream(std::istream& input);

private:
    bool processLine(const std::string& line, std::size_t lineNumber);
    void handleEvent(const FlatJsonObject& event);
    void handleMarketDataRequest(const FlatJsonObject& event);
    void handleHistoricalRequest(const FlatJsonObject& event);
    void handleTickPrice(const FlatJsonObject& event);
    void handleTickSize(const FlatJsonObject& event);
    void handleHistoricalBar(const FlatJsonObject& event);

    bool shouldEmitSymbol(const std::string& symbol) const;
    void emitNormalizedQuote(const QuoteState& state);
    void emitNormalizedBar(const std::string& symbol, const FlatJsonObject& event);
    void printSummary() const;

    static std::optional<int> parseIntField(const FlatJsonObject& event, const std::string& key);
    static std::optional<double> parseDoubleField(const FlatJsonObject& event, const std::string& key);
    static std::optional<std::string> parseStringField(const FlatJsonObject& event, const std::string& key);

    std::ostream* output_;
    std::ostream* diagnostics_;
    std::string symbolFilter_;
    bool emitQuotes_;
    bool emitBars_;

    std::size_t processedLines_;
    std::size_t processedEvents_;
    std::size_t parseErrors_;
    std::size_t emittedQuotesCount_;
    std::size_t emittedBarsCount_;

    std::unordered_map<int, std::string> marketDataRequests_;
    std::unordered_map<int, std::string> historicalRequests_;
    std::unordered_map<std::string, QuoteState> quotesBySymbol_;
};

} // namespace ibbridge