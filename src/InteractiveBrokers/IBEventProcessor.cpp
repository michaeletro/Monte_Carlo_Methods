#include "InteractiveBrokers/IBEventProcessor.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <istream>
#include <ostream>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace ibbridge {
namespace {

std::string toUpper(std::string value) {
    std::transform(value.begin(),
                   value.end(),
                   value.begin(),
                   [](unsigned char character) { return static_cast<char>(std::toupper(character)); });
    return value;
}

std::string jsonEscape(const std::string& value) {
    std::ostringstream escaped;

    for (char character : value) {
        switch (character) {
        case '\\':
            escaped << "\\\\";
            break;
        case '"':
            escaped << "\\\"";
            break;
        case '\b':
            escaped << "\\b";
            break;
        case '\f':
            escaped << "\\f";
            break;
        case '\n':
            escaped << "\\n";
            break;
        case '\r':
            escaped << "\\r";
            break;
        case '\t':
            escaped << "\\t";
            break;
        default:
            if (static_cast<unsigned char>(character) < 0x20U) {
                escaped << "\\u"
                        << std::hex
                        << std::setw(4)
                        << std::setfill('0')
                        << static_cast<int>(static_cast<unsigned char>(character))
                        << std::dec;
            } else {
                escaped << character;
            }
            break;
        }
    }

    return escaped.str();
}

std::string formatDouble(double value) {
    if (!std::isfinite(value)) {
        return "null";
    }

    std::ostringstream stream;
    stream << std::setprecision(10) << value;
    return stream.str();
}

void appendQuotedField(std::ostringstream& json,
                       const std::string& key,
                       const std::optional<std::string>& value) {
    json << ",\"" << jsonEscape(key) << "\":";
    if (!value.has_value()) {
        json << "null";
        return;
    }

    json << '"' << jsonEscape(*value) << '"';
}

void appendNumericField(std::ostringstream& json,
                        const std::string& key,
                        const std::optional<double>& value) {
    json << ",\"" << jsonEscape(key) << "\":";
    if (!value.has_value()) {
        json << "null";
        return;
    }

    json << formatDouble(*value);
}

void appendRawField(std::ostringstream& json, const std::string& key, const std::string& rawValue) {
    json << ",\"" << jsonEscape(key) << "\":" << rawValue;
}

class JsonLineCursor {
public:
    explicit JsonLineCursor(const std::string& input) : input_(input), offset_(0) {}

    bool parseObject(FlatJsonObject& object, std::string& error) {
        object.values.clear();
        skipWhitespace();

        if (!consume('{')) {
            error = "expected '{' at the start of the line";
            return false;
        }

        skipWhitespace();
        if (consume('}')) {
            skipWhitespace();
            return finish(error);
        }

        while (true) {
            std::string key;
            if (!parseString(key, error)) {
                return false;
            }

            skipWhitespace();
            if (!consume(':')) {
                error = "expected ':' after JSON key";
                return false;
            }

            skipWhitespace();
            std::string value;
            if (!parseValue(value, error)) {
                return false;
            }

            object.values[std::move(key)] = std::move(value);

            skipWhitespace();
            if (consume('}')) {
                skipWhitespace();
                return finish(error);
            }

            if (!consume(',')) {
                error = "expected ',' between JSON fields";
                return false;
            }

            skipWhitespace();
        }
    }

private:
    bool finish(std::string& error) const {
        if (offset_ == input_.size()) {
            return true;
        }

        error = "unexpected trailing characters after JSON object";
        return false;
    }

    void skipWhitespace() {
        while (offset_ < input_.size() && std::isspace(static_cast<unsigned char>(input_[offset_]))) {
            ++offset_;
        }
    }

    bool consume(char expected) {
        if (offset_ >= input_.size() || input_[offset_] != expected) {
            return false;
        }

        ++offset_;
        return true;
    }

    bool parseString(std::string& output, std::string& error) {
        if (!consume('"')) {
            error = "expected string literal";
            return false;
        }

        output.clear();
        while (offset_ < input_.size()) {
            const char character = input_[offset_++];
            if (character == '"') {
                return true;
            }

            if (character != '\\') {
                output.push_back(character);
                continue;
            }

            if (offset_ >= input_.size()) {
                error = "unterminated escape sequence in string literal";
                return false;
            }

            const char escape = input_[offset_++];
            switch (escape) {
            case '"':
                output.push_back('"');
                break;
            case '\\':
                output.push_back('\\');
                break;
            case '/':
                output.push_back('/');
                break;
            case 'b':
                output.push_back('\b');
                break;
            case 'f':
                output.push_back('\f');
                break;
            case 'n':
                output.push_back('\n');
                break;
            case 'r':
                output.push_back('\r');
                break;
            case 't':
                output.push_back('\t');
                break;
            case 'u': {
                if (offset_ + 4 > input_.size()) {
                    error = "incomplete unicode escape sequence";
                    return false;
                }

                unsigned int codePoint = 0;
                for (int index = 0; index < 4; ++index) {
                    const char hexDigit = input_[offset_++];
                    codePoint <<= 4U;
                    if (hexDigit >= '0' && hexDigit <= '9') {
                        codePoint += static_cast<unsigned int>(hexDigit - '0');
                    } else if (hexDigit >= 'a' && hexDigit <= 'f') {
                        codePoint += static_cast<unsigned int>(hexDigit - 'a' + 10);
                    } else if (hexDigit >= 'A' && hexDigit <= 'F') {
                        codePoint += static_cast<unsigned int>(hexDigit - 'A' + 10);
                    } else {
                        error = "invalid unicode escape sequence";
                        return false;
                    }
                }

                if (codePoint <= 0x7FU) {
                    output.push_back(static_cast<char>(codePoint));
                } else {
                    output.push_back('?');
                }
                break;
            }
            default:
                error = std::string("unsupported escape sequence: \\") + std::string(1, escape);
                return false;
            }
        }

        error = "unterminated string literal";
        return false;
    }

    bool parseValue(std::string& output, std::string& error) {
        if (offset_ >= input_.size()) {
            error = "expected JSON value";
            return false;
        }

        if (input_[offset_] == '"') {
            return parseString(output, error);
        }

        const std::size_t start = offset_;
        while (offset_ < input_.size()) {
            const char character = input_[offset_];
            if (character == ',' || character == '}' || std::isspace(static_cast<unsigned char>(character))) {
                break;
            }
            ++offset_;
        }

        if (start == offset_) {
            error = "expected JSON value";
            return false;
        }

        output = input_.substr(start, offset_ - start);
        return true;
    }

    const std::string& input_;
    std::size_t offset_;
};

} // namespace

const std::string* FlatJsonObject::find(const std::string& key) const {
    const auto iterator = values.find(key);
    if (iterator == values.end()) {
        return nullptr;
    }
    return &iterator->second;
}

bool FlatJsonObject::contains(const std::string& key) const {
    return values.find(key) != values.end();
}

bool FlatJsonParser::parseLine(const std::string& line, FlatJsonObject& object, std::string& error) {
    JsonLineCursor cursor(line);
    return cursor.parseObject(object, error);
}

IBEventProcessor::IBEventProcessor(std::ostream& output, std::ostream& diagnostics)
    : output_(&output),
      diagnostics_(&diagnostics),
      emitQuotes_(true),
      emitBars_(true),
      processedLines_(0),
      processedEvents_(0),
      parseErrors_(0),
      emittedQuotesCount_(0),
      emittedBarsCount_(0) {}

void IBEventProcessor::setSymbolFilter(const std::string& symbol) {
    symbolFilter_ = toUpper(symbol);
}

void IBEventProcessor::setEmitQuotes(bool emitQuotes) {
    emitQuotes_ = emitQuotes;
}

void IBEventProcessor::setEmitBars(bool emitBars) {
    emitBars_ = emitBars;
}

bool IBEventProcessor::processStream(std::istream& input) {
    std::string line;
    bool success = true;

    while (std::getline(input, line)) {
        ++processedLines_;
        if (!processLine(line, processedLines_)) {
            success = false;
        }
    }

    printSummary();
    return success && parseErrors_ == 0;
}

bool IBEventProcessor::processLine(const std::string& line, std::size_t lineNumber) {
    if (line.empty()) {
        return true;
    }

    FlatJsonObject event;
    std::string error;
    if (!FlatJsonParser::parseLine(line, event, error)) {
        ++parseErrors_;
        *diagnostics_ << "Parse error on line " << lineNumber << ": " << error << std::endl;
        return false;
    }

    ++processedEvents_;
    handleEvent(event);
    return true;
}

void IBEventProcessor::handleEvent(const FlatJsonObject& event) {
    const auto eventType = parseStringField(event, "type");
    if (!eventType.has_value()) {
        return;
    }

    if (*eventType == "request.marketData") {
        handleMarketDataRequest(event);
        return;
    }

    if (*eventType == "request.historicalData") {
        handleHistoricalRequest(event);
        return;
    }

    if (*eventType == "marketData.tickPrice") {
        handleTickPrice(event);
        return;
    }

    if (*eventType == "marketData.tickSize") {
        handleTickSize(event);
        return;
    }

    if (*eventType == "historical.bar") {
        handleHistoricalBar(event);
        return;
    }
}

void IBEventProcessor::handleMarketDataRequest(const FlatJsonObject& event) {
    const auto requestId = parseIntField(event, "reqId");
    const auto symbol = parseStringField(event, "symbol");
    if (!requestId.has_value() || !symbol.has_value()) {
        return;
    }

    marketDataRequests_[*requestId] = *symbol;

    auto& quote = quotesBySymbol_[*symbol];
    quote.symbol = *symbol;
}

void IBEventProcessor::handleHistoricalRequest(const FlatJsonObject& event) {
    const auto requestId = parseIntField(event, "reqId");
    const auto symbol = parseStringField(event, "symbol");
    if (!requestId.has_value() || !symbol.has_value()) {
        return;
    }

    historicalRequests_[*requestId] = *symbol;
}

void IBEventProcessor::handleTickPrice(const FlatJsonObject& event) {
    const auto requestId = parseIntField(event, "reqId");
    const auto field = parseStringField(event, "field");
    const auto price = parseDoubleField(event, "price");
    if (!requestId.has_value() || !field.has_value() || !price.has_value()) {
        return;
    }

    const auto requestIterator = marketDataRequests_.find(*requestId);
    if (requestIterator == marketDataRequests_.end()) {
        return;
    }

    QuoteState& quote = quotesBySymbol_[requestIterator->second];
    quote.symbol = requestIterator->second;

    if (*field == "BID") {
        quote.bid = *price;
    } else if (*field == "ASK") {
        quote.ask = *price;
    } else if (*field == "LAST") {
        quote.last = *price;
    } else if (*field == "CLOSE") {
        quote.close = *price;
    } else {
        return;
    }

    if (emitQuotes_ && shouldEmitSymbol(quote.symbol)) {
        emitNormalizedQuote(quote);
    }
}

void IBEventProcessor::handleTickSize(const FlatJsonObject& event) {
    const auto requestId = parseIntField(event, "reqId");
    const auto field = parseStringField(event, "field");
    const auto size = parseStringField(event, "size");
    if (!requestId.has_value() || !field.has_value() || !size.has_value()) {
        return;
    }

    const auto requestIterator = marketDataRequests_.find(*requestId);
    if (requestIterator == marketDataRequests_.end()) {
        return;
    }

    QuoteState& quote = quotesBySymbol_[requestIterator->second];
    quote.symbol = requestIterator->second;

    if (*field == "BID_SIZE") {
        quote.bidSize = *size;
    } else if (*field == "ASK_SIZE") {
        quote.askSize = *size;
    } else if (*field == "LAST_SIZE") {
        quote.lastSize = *size;
    } else {
        return;
    }

    if (emitQuotes_ && shouldEmitSymbol(quote.symbol)) {
        emitNormalizedQuote(quote);
    }
}

void IBEventProcessor::handleHistoricalBar(const FlatJsonObject& event) {
    const auto requestId = parseIntField(event, "reqId");
    if (!requestId.has_value()) {
        return;
    }

    const auto requestIterator = historicalRequests_.find(*requestId);
    if (requestIterator == historicalRequests_.end()) {
        return;
    }

    if (emitBars_ && shouldEmitSymbol(requestIterator->second)) {
        emitNormalizedBar(requestIterator->second, event);
    }
}

bool IBEventProcessor::shouldEmitSymbol(const std::string& symbol) const {
    return symbolFilter_.empty() || toUpper(symbol) == symbolFilter_;
}

void IBEventProcessor::emitNormalizedQuote(const QuoteState& state) {
    std::ostringstream json;
    json << "{\"type\":\"normalized.quote\",\"symbol\":\"" << jsonEscape(state.symbol)
         << "\"";
    appendNumericField(json, "bid", state.bid);
    appendNumericField(json, "ask", state.ask);
    appendNumericField(json, "last", state.last);
    appendNumericField(json, "close", state.close);
    appendQuotedField(json, "bidSize", state.bidSize);
    appendQuotedField(json, "askSize", state.askSize);
    appendQuotedField(json, "lastSize", state.lastSize);

    if (state.bid.has_value() && state.ask.has_value()) {
        appendNumericField(json, "mid", (*state.bid + *state.ask) / 2.0);
        appendNumericField(json, "spread", *state.ask - *state.bid);
    } else {
        appendNumericField(json, "mid", std::nullopt);
        appendNumericField(json, "spread", std::nullopt);
    }

    json << '}';
    *output_ << json.str() << std::endl;
    ++emittedQuotesCount_;
}

void IBEventProcessor::emitNormalizedBar(const std::string& symbol, const FlatJsonObject& event) {
    std::ostringstream json;
    json << "{\"type\":\"normalized.bar\",\"symbol\":\"" << jsonEscape(symbol) << "\"";
    appendQuotedField(json, "time", parseStringField(event, "time"));
    appendNumericField(json, "open", parseDoubleField(event, "open"));
    appendNumericField(json, "high", parseDoubleField(event, "high"));
    appendNumericField(json, "low", parseDoubleField(event, "low"));
    appendNumericField(json, "close", parseDoubleField(event, "close"));
    appendQuotedField(json, "volume", parseStringField(event, "volume"));
    appendQuotedField(json, "wap", parseStringField(event, "wap"));

    const auto count = parseIntField(event, "count");
    if (count.has_value()) {
        appendRawField(json, "count", std::to_string(*count));
    } else {
        appendRawField(json, "count", "null");
    }

    json << '}';
    *output_ << json.str() << std::endl;
    ++emittedBarsCount_;
}

void IBEventProcessor::printSummary() const {
    *diagnostics_ << "Processed " << processedLines_ << " line(s), " << processedEvents_
                  << " event(s), emitted " << emittedQuotesCount_ << " normalized quote event(s) and "
                  << emittedBarsCount_ << " normalized bar event(s)";

    if (parseErrors_ > 0) {
        *diagnostics_ << ", with " << parseErrors_ << " parse error(s)";
    }

    *diagnostics_ << '.' << std::endl;
}

std::optional<int> IBEventProcessor::parseIntField(const FlatJsonObject& event, const std::string& key) {
    const std::string* rawValue = event.find(key);
    if (rawValue == nullptr || rawValue->empty() || *rawValue == "null") {
        return std::nullopt;
    }

    try {
        std::size_t parsedCharacters = 0;
        const int parsedValue = std::stoi(*rawValue, &parsedCharacters);
        if (parsedCharacters != rawValue->size()) {
            return std::nullopt;
        }
        return parsedValue;
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

std::optional<double> IBEventProcessor::parseDoubleField(const FlatJsonObject& event,
                                                         const std::string& key) {
    const std::string* rawValue = event.find(key);
    if (rawValue == nullptr || rawValue->empty() || *rawValue == "null") {
        return std::nullopt;
    }

    try {
        std::size_t parsedCharacters = 0;
        const double parsedValue = std::stod(*rawValue, &parsedCharacters);
        if (parsedCharacters != rawValue->size()) {
            return std::nullopt;
        }
        return parsedValue;
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

std::optional<std::string> IBEventProcessor::parseStringField(const FlatJsonObject& event,
                                                              const std::string& key) {
    const std::string* rawValue = event.find(key);
    if (rawValue == nullptr || *rawValue == "null") {
        return std::nullopt;
    }
    return *rawValue;
}

} // namespace ibbridge