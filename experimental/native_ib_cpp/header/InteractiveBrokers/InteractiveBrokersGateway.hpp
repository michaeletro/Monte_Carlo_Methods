#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <ctime>
#include <fstream>
#include <functional>
#include <initializer_list>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_set>

#include "DefaultEWrapper.h"

class EClientSocket;
class EReader;
class EReaderOSSignal;
struct Contract;
struct Order;
struct OrderCancel;
struct OrderState;
struct Bar;
struct TickAttrib;

struct IBConnectionSettings {
    std::string host = "127.0.0.1";
    int port = 7497;
    int clientId = 7;
    int readyTimeoutSeconds = 15;
};

struct IBContractSpec {
    std::string symbol;
    std::string secType = "STK";
    std::string exchange = "SMART";
    std::string primaryExchange;
    std::string currency = "USD";
};

struct IBHistoricalDataRequest {
    std::string endDateTime;
    std::string durationStr = "1 D";
    std::string barSizeSetting = "5 mins";
    std::string whatToShow = "TRADES";
    int useRTH = 1;
    int formatDate = 1;
    bool keepUpToDate = false;
};

struct IBOrderRequest {
    std::string action = "BUY";
    std::string quantity = "1";
    std::string orderType = "LMT";
    double limitPrice = 0.0;
    std::string tif = "DAY";
    bool transmit = false;
    std::string account;
};

class InteractiveBrokersGateway : public DefaultEWrapper {
public:
    using EventCallback = std::function<void(const std::string&)>;

    InteractiveBrokersGateway();
    ~InteractiveBrokersGateway() override;

    InteractiveBrokersGateway(const InteractiveBrokersGateway&) = delete;
    InteractiveBrokersGateway& operator=(const InteractiveBrokersGateway&) = delete;

    bool connect(const IBConnectionSettings& settings);
    void disconnect();
    bool isConnected() const;

    void setEventCallback(EventCallback callback);
    void setEventLogFile(const std::string& path);

    bool waitUntilReady(std::chrono::seconds timeout);
    bool waitForHistoricalDataEnd(int requestId, std::chrono::seconds timeout);
    bool waitForPositionsEnd(std::chrono::seconds timeout);
    bool waitForOpenOrdersEnd(std::chrono::seconds timeout);
    bool waitForAccountDownloadEnd(std::chrono::seconds timeout);

    int requestMarketData(const IBContractSpec& contractSpec,
                          const std::string& genericTicks = "233",
                          bool snapshot = false,
                          bool regulatorySnapshot = false);
    void cancelMarketData(int requestId);

    int requestHistoricalData(const IBContractSpec& contractSpec,
                              const IBHistoricalDataRequest& request);
    void cancelHistoricalData(int requestId);

    void requestPositions();
    void cancelPositions();

    void requestAccountUpdates(const std::string& account, bool subscribe = true);
    void requestOpenOrders(bool allClients = false);

    int placeLimitOrder(const IBContractSpec& contractSpec, const IBOrderRequest& orderRequest);
    void cancelOrder(int orderId);

    std::string managedAccountsList() const;
    int nextOrderId() const;

    void error(int id,
               time_t errorTime,
               int errorCode,
               const std::string& errorString,
               const std::string& advancedOrderRejectJson) override;
    void connectionClosed() override;
    void managedAccounts(const std::string& accountsList) override;
    void nextValidId(int orderId) override;
    void tickPrice(int reqId, TickType field, double price, const TickAttrib& attrib) override;
    void tickSize(int reqId, TickType field, Decimal size) override;
    void tickString(int reqId, TickType tickType, const std::string& value) override;
    void historicalData(int reqId, const Bar& bar) override;
    void historicalDataEnd(int reqId,
                           const std::string& startDateStr,
                           const std::string& endDateStr) override;
    void orderStatus(int orderId,
                     const std::string& status,
                     Decimal filled,
                     Decimal remaining,
                     double avgFillPrice,
                     long long permId,
                     int parentId,
                     double lastFillPrice,
                     int clientId,
                     const std::string& whyHeld,
                     double mktCapPrice) override;
    void openOrder(int orderId,
                   const Contract& contract,
                   const Order& order,
                   const OrderState& orderState) override;
    void openOrderEnd() override;
    void position(const std::string& account,
                  const Contract& contract,
                  Decimal position,
                  double avgCost) override;
    void positionEnd() override;
    void updateAccountValue(const std::string& key,
                            const std::string& val,
                            const std::string& currency,
                            const std::string& accountName) override;
    void updatePortfolio(const Contract& contract,
                         Decimal position,
                         double marketPrice,
                         double marketValue,
                         double averageCost,
                         double unrealizedPNL,
                         double realizedPNL,
                         const std::string& accountName) override;
    void updateAccountTime(const std::string& timeStamp) override;
    void accountDownloadEnd(const std::string& accountName) override;

private:
    struct JsonField {
        std::string key;
        std::string value;
        bool raw = false;
    };

    static JsonField stringField(const std::string& key, const std::string& value);
    static JsonField rawField(const std::string& key, const std::string& value);
    static JsonField intField(const std::string& key, int value);
    static JsonField longLongField(const std::string& key, long long value);
    static JsonField doubleField(const std::string& key, double value);
    static JsonField boolField(const std::string& key, bool value);

    static std::string jsonEscape(const std::string& value);
    static std::string formatDouble(double value);
    static std::string formatDecimal(Decimal value);
    static std::string tickTypeToString(TickType tickType);

    int acquireRequestId();
    Contract buildContract(const IBContractSpec& contractSpec) const;
    Order buildLimitOrder(const IBOrderRequest& orderRequest) const;
    void startReaderLoop();
    void stopReaderLoop();
    void emitEvent(const std::string& type, std::initializer_list<JsonField> fields);
    void markConnectionState(bool ready);

    mutable std::mutex stateMutex_;
    std::mutex outputMutex_;
    std::condition_variable stateCondition_;
    std::condition_variable historicalCondition_;
    std::condition_variable positionsCondition_;
    std::condition_variable openOrdersCondition_;
    std::condition_variable accountCondition_;

    std::unique_ptr<EReaderOSSignal> signal_;
    std::unique_ptr<EClientSocket> client_;
    std::unique_ptr<EReader> reader_;
    std::thread readerThread_;
    std::atomic<bool> readerLoopRunning_;
    std::atomic<int> nextRequestId_;

    EventCallback eventCallback_;
    std::ofstream eventLog_;

    IBConnectionSettings settings_;
    std::string managedAccountsValue_;
    int nextOrderIdValue_;
    bool connectionReady_;
    bool positionsComplete_;
    bool openOrdersComplete_;
    bool accountDownloadComplete_;
    std::unordered_set<int> completedHistoricalRequests_;
};