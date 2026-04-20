#pragma once

#include <optional>
#include <sqlite3.h>
#include <string>
#include <vector>

namespace ibbridge {

struct HistoricalBarRecord {
    int requestId = 0;
    std::string time;
    double open = 0.0;
    double high = 0.0;
    double low = 0.0;
    double close = 0.0;
    long long volume = 0;
    int count = 0;
};

struct InstrumentMetadata {
    std::string symbol;
    std::string secType = "STK";
    std::string exchange = "SMART";
    std::string primaryExchange;
    std::string currency = "USD";
    std::string expiry;
    std::optional<double> strike;
    std::string right;
    std::string multiplier;
    std::string localSymbol;
    std::string tradingClass;
    std::optional<int> conId;
    std::string marketName;
    std::string longName;
    std::optional<double> minTick;
    std::string orderTypes;
    std::string validExchanges;
    std::string timeZoneId;
    std::string liquidHours;
    std::string tradingHours;
};

struct HistoricalFetchRequest {
    std::string gatewayPath = "./ib_gateway";
    std::string host = "127.0.0.1";
    int port = 7497;
    int clientId = 7;
    int readyTimeoutSeconds = 15;
    std::string symbol;
    std::string secType = "STK";
    std::string exchange = "SMART";
    std::string primaryExchange;
    std::string currency = "USD";
    std::string expiry;
    std::optional<double> strike;
    std::string right;
    std::string multiplier;
    std::string localSymbol;
    std::string tradingClass;
    std::optional<int> conId;
    std::string endDateTime;
    std::string duration = "1 D";
    std::string barSize = "5 mins";
    std::string whatToShow = "TRADES";
    int useRTH = 1;
    int formatDate = 1;
    bool keepUpToDate = false;
};

struct HistoricalStoreResult {
    InstrumentMetadata instrument;
    std::vector<HistoricalBarRecord> bars;
    std::size_t parsedEvents = 0;
    int exitCode = 0;
};

class HistoricalGatewayClient {
public:
    HistoricalStoreResult fetch(const HistoricalFetchRequest& request) const;

private:
    static std::string buildHistoricalCommand(const HistoricalFetchRequest& request);
    static std::string buildContractDetailsCommand(const HistoricalFetchRequest& request);
};

class HistoricalDatabase {
public:
    explicit HistoricalDatabase(const std::string& databasePath);
    ~HistoricalDatabase();

    HistoricalDatabase(const HistoricalDatabase&) = delete;
    HistoricalDatabase& operator=(const HistoricalDatabase&) = delete;

    void initializeSchema();
    long long upsertInstrument(const InstrumentMetadata& instrument);
    int insertHistoricalBars(long long instrumentId,
                             const HistoricalFetchRequest& request,
                             const std::vector<HistoricalBarRecord>& bars);

private:
    ::sqlite3* database_;
};

class HistoricalStorageService {
public:
    HistoricalStorageService(const std::string& databasePath, HistoricalGatewayClient client = HistoricalGatewayClient());

    HistoricalStoreResult fetchAndStore(const HistoricalFetchRequest& request) const;

private:
    std::string databasePath_;
    HistoricalGatewayClient client_;
};

} // namespace ibbridge