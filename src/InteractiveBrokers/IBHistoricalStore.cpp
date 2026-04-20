#include "InteractiveBrokers/IBHistoricalStore.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#ifdef __unix__
#include <sys/wait.h>
#endif

#include <cmath>
#include <filesystem>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

#include <nlohmann/json.hpp>
#include <sqlite3.h>

namespace ibbridge {
namespace {

using json = nlohmann::json;

struct CommandCaptureResult {
    std::vector<json> events;
    int exitCode = 0;
};

std::string shellQuote(const std::string& value) {
    std::string quoted = "'";
    for (char character : value) {
        if (character == '\'') {
            quoted += "'\\''";
        } else {
            quoted.push_back(character);
        }
    }
    quoted.push_back('\'');
    return quoted;
}

void trimLineEnding(std::string& line) {
    while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) {
        line.pop_back();
    }
}

int decodeExitCode(int rawReturnCode) {
#ifdef __unix__
    if (WIFEXITED(rawReturnCode)) {
        return WEXITSTATUS(rawReturnCode);
    }
#endif
    return rawReturnCode;
}

std::optional<json> tryParseJsonLine(const std::string& line) {
    try {
        return json::parse(line);
    } catch (...) {
        return std::nullopt;
    }
}

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

HistoricalBarRecord toHistoricalBar(const json& event) {
    HistoricalBarRecord bar;
    bar.requestId = event.value("reqId", 0);
    bar.time = event.value("time", "");
    bar.open = event.value("open", 0.0);
    bar.high = event.value("high", 0.0);
    bar.low = event.value("low", 0.0);
    bar.close = event.value("close", 0.0);
    bar.volume = parseVolumeString(event.value("volume", std::string{}));
    bar.count = event.value("count", 0);
    return bar;
}

std::optional<double> optionalDouble(const json& event, const char* key) {
    if (!event.contains(key) || event.at(key).is_null()) {
        return std::nullopt;
    }
    return event.value(key, 0.0);
}

std::optional<int> optionalInt(const json& event, const char* key) {
    if (!event.contains(key) || event.at(key).is_null()) {
        return std::nullopt;
    }
    return event.value(key, 0);
}

void appendContractArguments(std::ostringstream& command, const HistoricalFetchRequest& request) {
    command << " --symbol " << shellQuote(request.symbol);
    command << " --sec-type " << shellQuote(request.secType);
    command << " --exchange " << shellQuote(request.exchange);
    command << " --currency " << shellQuote(request.currency);

    if (!request.primaryExchange.empty()) {
        command << " --primary-exchange " << shellQuote(request.primaryExchange);
    }
    if (!request.expiry.empty()) {
        command << " --expiry " << shellQuote(request.expiry);
    }
    if (request.strike.has_value()) {
        command << " --strike " << *request.strike;
    }
    if (!request.right.empty()) {
        command << " --right " << shellQuote(request.right);
    }
    if (!request.multiplier.empty()) {
        command << " --multiplier " << shellQuote(request.multiplier);
    }
    if (!request.localSymbol.empty()) {
        command << " --local-symbol " << shellQuote(request.localSymbol);
    }
    if (!request.tradingClass.empty()) {
        command << " --trading-class " << shellQuote(request.tradingClass);
    }
    if (request.conId.has_value()) {
        command << " --con-id " << *request.conId;
    }
}

std::string buildBaseCommand(const HistoricalFetchRequest& request, const std::string& subcommand) {
    if (request.symbol.empty()) {
        throw std::invalid_argument("symbol is required");
    }

    std::ostringstream command;
    command << shellQuote(request.gatewayPath);
    command << ' ' << subcommand;
    command << " --host " << shellQuote(request.host);
    command << " --port " << request.port;
    command << " --client-id " << request.clientId;
    command << " --ready-timeout " << request.readyTimeoutSeconds;
    appendContractArguments(command, request);
    return command.str();
}

CommandCaptureResult runJsonCommand(const std::string& command) {
    CommandCaptureResult result;

    FILE* pipe = popen(command.c_str(), "r");
    if (pipe == nullptr) {
        throw std::runtime_error("Failed to open pipe for gateway command");
    }

    char buffer[4096];
    while (fgets(buffer, sizeof(buffer), pipe) != nullptr) {
        std::string line(buffer);
        trimLineEnding(line);
        if (line.empty()) {
            continue;
        }

        std::optional<json> parsed = tryParseJsonLine(line);
        if (parsed.has_value()) {
            result.events.push_back(*parsed);
        }
    }

    result.exitCode = decodeExitCode(pclose(pipe));
    return result;
}

InstrumentMetadata fallbackInstrument(const HistoricalFetchRequest& request) {
    InstrumentMetadata instrument;
    instrument.symbol = request.symbol;
    instrument.secType = request.secType;
    instrument.exchange = request.exchange;
    instrument.primaryExchange = request.primaryExchange;
    instrument.currency = request.currency;
    instrument.expiry = request.expiry;
    instrument.strike = request.strike;
    instrument.right = request.right;
    instrument.multiplier = request.multiplier;
    instrument.localSymbol = request.localSymbol;
    instrument.tradingClass = request.tradingClass;
    instrument.conId = request.conId;
    return instrument;
}

InstrumentMetadata parseInstrumentMetadata(const HistoricalFetchRequest& request,
                                           const std::vector<json>& events) {
    InstrumentMetadata instrument = fallbackInstrument(request);

    for (const json& event : events) {
        if (event.value("type", std::string{}) != "contract.details") {
            continue;
        }

        instrument.symbol = event.value("symbol", instrument.symbol);
        instrument.secType = event.value("secType", instrument.secType);
        instrument.exchange = event.value("exchange", instrument.exchange);
        instrument.primaryExchange = event.value("primaryExchange", instrument.primaryExchange);
        instrument.currency = event.value("currency", instrument.currency);
        instrument.localSymbol = event.value("localSymbol", instrument.localSymbol);
        instrument.tradingClass = event.value("tradingClass", instrument.tradingClass);
        instrument.conId = optionalInt(event, "conId");
        instrument.marketName = event.value("marketName", instrument.marketName);
        instrument.longName = event.value("longName", instrument.longName);
        instrument.minTick = optionalDouble(event, "minTick");
        instrument.orderTypes = event.value("orderTypes", instrument.orderTypes);
        instrument.validExchanges = event.value("validExchanges", instrument.validExchanges);
        instrument.timeZoneId = event.value("timeZoneId", instrument.timeZoneId);
        instrument.liquidHours = event.value("liquidHours", instrument.liquidHours);
        instrument.tradingHours = event.value("tradingHours", instrument.tradingHours);
        break;
    }

    return instrument;
}

std::vector<HistoricalBarRecord> parseHistoricalBars(const std::vector<json>& events) {
    std::vector<HistoricalBarRecord> bars;
    for (const json& event : events) {
        if (event.value("type", std::string{}) == "historical.bar") {
            bars.push_back(toHistoricalBar(event));
        }
    }
    return bars;
}

void throwOnSqlError(int code, sqlite3* database, const std::string& action) {
    if (code == SQLITE_OK || code == SQLITE_DONE || code == SQLITE_ROW) {
        return;
    }

    throw std::runtime_error(action + ": " + sqlite3_errmsg(database));
}

class Statement final {
public:
    Statement(sqlite3* database, const char* sql) : statement_(nullptr), database_(database) {
        const int code = sqlite3_prepare_v2(database_, sql, -1, &statement_, nullptr);
        throwOnSqlError(code, database_, "Unable to prepare SQL statement");
    }

    ~Statement() {
        if (statement_ != nullptr) {
            sqlite3_finalize(statement_);
        }
    }

    sqlite3_stmt* get() const {
        return statement_;
    }

private:
    sqlite3_stmt* statement_;
    sqlite3* database_;
};

void bindText(sqlite3_stmt* statement, int index, const std::string& value) {
    sqlite3_bind_text(statement, index, value.c_str(), -1, SQLITE_TRANSIENT);
}

void bindOptionalDouble(sqlite3_stmt* statement, int index, const std::optional<double>& value) {
    if (!value.has_value()) {
        sqlite3_bind_null(statement, index);
        return;
    }
    sqlite3_bind_double(statement, index, *value);
}

void bindOptionalInt(sqlite3_stmt* statement, int index, const std::optional<int>& value) {
    if (!value.has_value()) {
        sqlite3_bind_null(statement, index);
        return;
    }
    sqlite3_bind_int(statement, index, *value);
}

} // namespace

std::string HistoricalGatewayClient::buildHistoricalCommand(const HistoricalFetchRequest& request) {
    std::ostringstream command;
    command << buildBaseCommand(request, "historical");
    if (!request.endDateTime.empty()) {
        command << " --end-date-time " << shellQuote(request.endDateTime);
    }
    command << " --duration " << shellQuote(request.duration);
    command << " --bar-size " << shellQuote(request.barSize);
    command << " --what-to-show " << shellQuote(request.whatToShow);
    command << " --use-rth " << request.useRTH;
    command << " --format-date " << request.formatDate;
    command << " --keep-up-to-date " << shellQuote(request.keepUpToDate ? "true" : "false");
    return command.str();
}

std::string HistoricalGatewayClient::buildContractDetailsCommand(const HistoricalFetchRequest& request) {
    return buildBaseCommand(request, "contract-details");
}

HistoricalStoreResult HistoricalGatewayClient::fetch(const HistoricalFetchRequest& request) const {
    const CommandCaptureResult metadataCapture = runJsonCommand(buildContractDetailsCommand(request));
    const CommandCaptureResult historicalCapture = runJsonCommand(buildHistoricalCommand(request));

    HistoricalStoreResult result;
    result.instrument = parseInstrumentMetadata(request, metadataCapture.events);
    result.bars = parseHistoricalBars(historicalCapture.events);
    result.parsedEvents = historicalCapture.events.size() + metadataCapture.events.size();
    result.exitCode = historicalCapture.exitCode != 0 ? historicalCapture.exitCode : metadataCapture.exitCode;

    if (result.exitCode != 0) {
        throw std::runtime_error("Gateway command failed with exit code " + std::to_string(result.exitCode));
    }

    return result;
}

HistoricalDatabase::HistoricalDatabase(const std::string& databasePath) : database_(nullptr) {
    const std::filesystem::path path(databasePath);
    const std::filesystem::path parent = path.parent_path();
    if (!parent.empty()) {
        std::filesystem::create_directories(parent);
    }

    const int code = sqlite3_open(databasePath.c_str(), &database_);
    if (code != SQLITE_OK) {
        const std::string message = database_ != nullptr ? sqlite3_errmsg(database_) : "unable to open database";
        if (database_ != nullptr) {
            sqlite3_close(database_);
            database_ = nullptr;
        }
        throw std::runtime_error("Unable to open SQLite database: " + message);
    }
}

HistoricalDatabase::~HistoricalDatabase() {
    if (database_ != nullptr) {
        sqlite3_close(database_);
    }
}

void HistoricalDatabase::initializeSchema() {
    const char* sql =
        "CREATE TABLE IF NOT EXISTS instruments ("
        " id INTEGER PRIMARY KEY,"
        " symbol TEXT NOT NULL,"
        " sec_type TEXT NOT NULL,"
        " exchange TEXT NOT NULL,"
        " primary_exchange TEXT NOT NULL DEFAULT '',"
        " currency TEXT NOT NULL,"
        " expiry TEXT NOT NULL DEFAULT '',"
        " strike REAL,"
        " right_code TEXT NOT NULL DEFAULT '',"
        " multiplier TEXT NOT NULL DEFAULT '',"
        " local_symbol TEXT NOT NULL DEFAULT '',"
        " trading_class TEXT NOT NULL DEFAULT '',"
        " con_id INTEGER,"
        " market_name TEXT NOT NULL DEFAULT '',"
        " long_name TEXT NOT NULL DEFAULT '',"
        " min_tick REAL,"
        " order_types TEXT NOT NULL DEFAULT '',"
        " valid_exchanges TEXT NOT NULL DEFAULT '',"
        " time_zone_id TEXT NOT NULL DEFAULT '',"
        " liquid_hours TEXT NOT NULL DEFAULT '',"
        " trading_hours TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " UNIQUE(symbol, sec_type, exchange, primary_exchange, currency, expiry, strike, right_code, multiplier, local_symbol, trading_class)"
        ");"
        "CREATE TABLE IF NOT EXISTS historical_prices ("
        " id INTEGER PRIMARY KEY,"
        " instrument_id INTEGER NOT NULL,"
        " bar_time TEXT NOT NULL,"
        " open REAL NOT NULL,"
        " high REAL NOT NULL,"
        " low REAL NOT NULL,"
        " close REAL NOT NULL,"
        " volume INTEGER NOT NULL DEFAULT 0,"
        " trade_count INTEGER NOT NULL DEFAULT 0,"
        " what_to_show TEXT NOT NULL,"
        " bar_size TEXT NOT NULL,"
        " duration TEXT NOT NULL,"
        " use_rth INTEGER NOT NULL,"
        " source TEXT NOT NULL DEFAULT 'ib_gateway',"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " UNIQUE(instrument_id, bar_time, what_to_show, bar_size, use_rth),"
        " FOREIGN KEY(instrument_id) REFERENCES instruments(id) ON DELETE CASCADE"
        ");"
        "CREATE INDEX IF NOT EXISTS idx_historical_prices_instrument_time ON historical_prices(instrument_id, bar_time);";

    char* errorMessage = nullptr;
    const int code = sqlite3_exec(database_, sql, nullptr, nullptr, &errorMessage);
    if (code != SQLITE_OK) {
        const std::string message = errorMessage != nullptr ? errorMessage : sqlite3_errmsg(database_);
        if (errorMessage != nullptr) {
            sqlite3_free(errorMessage);
        }
        throw std::runtime_error("Unable to initialize SQLite schema: " + message);
    }
}

long long HistoricalDatabase::upsertInstrument(const InstrumentMetadata& instrument) {
    const char* sql =
        "INSERT INTO instruments ("
        " symbol, sec_type, exchange, primary_exchange, currency, expiry, strike, right_code,"
        " multiplier, local_symbol, trading_class, con_id, market_name, long_name, min_tick,"
        " order_types, valid_exchanges, time_zone_id, liquid_hours, trading_hours, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)"
        " ON CONFLICT(symbol, sec_type, exchange, primary_exchange, currency, expiry, strike, right_code, multiplier, local_symbol, trading_class)"
        " DO UPDATE SET"
        " con_id=excluded.con_id,"
        " market_name=excluded.market_name,"
        " long_name=excluded.long_name,"
        " min_tick=excluded.min_tick,"
        " order_types=excluded.order_types,"
        " valid_exchanges=excluded.valid_exchanges,"
        " time_zone_id=excluded.time_zone_id,"
        " liquid_hours=excluded.liquid_hours,"
        " trading_hours=excluded.trading_hours,"
        " updated_at=CURRENT_TIMESTAMP;";

    Statement statement(database_, sql);
    sqlite3_stmt* prepared = statement.get();

    bindText(prepared, 1, instrument.symbol);
    bindText(prepared, 2, instrument.secType);
    bindText(prepared, 3, instrument.exchange);
    bindText(prepared, 4, instrument.primaryExchange);
    bindText(prepared, 5, instrument.currency);
    bindText(prepared, 6, instrument.expiry);
    bindOptionalDouble(prepared, 7, instrument.strike);
    bindText(prepared, 8, instrument.right);
    bindText(prepared, 9, instrument.multiplier);
    bindText(prepared, 10, instrument.localSymbol);
    bindText(prepared, 11, instrument.tradingClass);
    bindOptionalInt(prepared, 12, instrument.conId);
    bindText(prepared, 13, instrument.marketName);
    bindText(prepared, 14, instrument.longName);
    bindOptionalDouble(prepared, 15, instrument.minTick);
    bindText(prepared, 16, instrument.orderTypes);
    bindText(prepared, 17, instrument.validExchanges);
    bindText(prepared, 18, instrument.timeZoneId);
    bindText(prepared, 19, instrument.liquidHours);
    bindText(prepared, 20, instrument.tradingHours);

    throwOnSqlError(sqlite3_step(prepared), database_, "Unable to upsert instrument metadata");

    const char* selectSql =
        "SELECT id FROM instruments WHERE"
        " symbol = ? AND sec_type = ? AND exchange = ? AND primary_exchange = ? AND currency = ?"
        " AND expiry = ? AND ((strike IS NULL AND ? IS NULL) OR strike = ?)"
        " AND right_code = ? AND multiplier = ? AND local_symbol = ? AND trading_class = ?";

    Statement selectStatement(database_, selectSql);
    prepared = selectStatement.get();

    bindText(prepared, 1, instrument.symbol);
    bindText(prepared, 2, instrument.secType);
    bindText(prepared, 3, instrument.exchange);
    bindText(prepared, 4, instrument.primaryExchange);
    bindText(prepared, 5, instrument.currency);
    bindText(prepared, 6, instrument.expiry);
    bindOptionalDouble(prepared, 7, instrument.strike);
    bindOptionalDouble(prepared, 8, instrument.strike);
    bindText(prepared, 9, instrument.right);
    bindText(prepared, 10, instrument.multiplier);
    bindText(prepared, 11, instrument.localSymbol);
    bindText(prepared, 12, instrument.tradingClass);

    const int code = sqlite3_step(prepared);
    if (code != SQLITE_ROW) {
        throwOnSqlError(code, database_, "Unable to resolve instrument id");
    }
    return sqlite3_column_int64(prepared, 0);
}

int HistoricalDatabase::insertHistoricalBars(long long instrumentId,
                                             const HistoricalFetchRequest& request,
                                             const std::vector<HistoricalBarRecord>& bars) {
    char* errorMessage = nullptr;
    throwOnSqlError(sqlite3_exec(database_, "BEGIN IMMEDIATE TRANSACTION", nullptr, nullptr, &errorMessage),
                    database_,
                    "Unable to start transaction");

    try {
        const char* sql =
            "INSERT INTO historical_prices ("
            " instrument_id, bar_time, open, high, low, close, volume, trade_count, what_to_show, bar_size, duration, use_rth"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(instrument_id, bar_time, what_to_show, bar_size, use_rth)"
            " DO UPDATE SET"
            " open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,"
            " volume=excluded.volume, trade_count=excluded.trade_count, duration=excluded.duration;";

        Statement statement(database_, sql);
        sqlite3_stmt* prepared = statement.get();

        int insertedOrUpdated = 0;
        for (const HistoricalBarRecord& bar : bars) {
            sqlite3_reset(prepared);
            sqlite3_clear_bindings(prepared);

            sqlite3_bind_int64(prepared, 1, instrumentId);
            bindText(prepared, 2, bar.time);
            sqlite3_bind_double(prepared, 3, bar.open);
            sqlite3_bind_double(prepared, 4, bar.high);
            sqlite3_bind_double(prepared, 5, bar.low);
            sqlite3_bind_double(prepared, 6, bar.close);
            sqlite3_bind_int64(prepared, 7, bar.volume);
            sqlite3_bind_int(prepared, 8, bar.count);
            bindText(prepared, 9, request.whatToShow);
            bindText(prepared, 10, request.barSize);
            bindText(prepared, 11, request.duration);
            sqlite3_bind_int(prepared, 12, request.useRTH);

            throwOnSqlError(sqlite3_step(prepared), database_, "Unable to insert historical bar");
            ++insertedOrUpdated;
        }

        throwOnSqlError(sqlite3_exec(database_, "COMMIT", nullptr, nullptr, &errorMessage),
                        database_,
                        "Unable to commit transaction");
        return insertedOrUpdated;
    } catch (...) {
        sqlite3_exec(database_, "ROLLBACK", nullptr, nullptr, nullptr);
        throw;
    }
}

HistoricalStorageService::HistoricalStorageService(const std::string& databasePath,
                                                   HistoricalGatewayClient client)
    : databasePath_(databasePath), client_(std::move(client)) {}

HistoricalStoreResult HistoricalStorageService::fetchAndStore(const HistoricalFetchRequest& request) const {
    HistoricalStoreResult result = client_.fetch(request);

    HistoricalDatabase database(databasePath_);
    database.initializeSchema();
    const long long instrumentId = database.upsertInstrument(result.instrument);
    database.insertHistoricalBars(instrumentId, request, result.bars);
    return result;
}

} // namespace ibbridge