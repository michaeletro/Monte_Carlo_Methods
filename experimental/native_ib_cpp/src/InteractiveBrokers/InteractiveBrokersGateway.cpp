#include "InteractiveBrokers/InteractiveBrokersGateway.hpp"

#include <cmath>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <utility>

#include "Contract.h"
#include "Decimal.h"
#include "EClientSocket.h"
#include "EReader.h"
#include "EReaderOSSignal.h"
#include "Order.h"
#include "OrderCancel.h"
#include "OrderState.h"
#include "TagValue.h"
#include "bar.h"

namespace {

bool isInformationalCode(int errorCode) {
    switch (errorCode) {
    case 2104:
    case 2106:
    case 2107:
    case 2108:
    case 2158:
        return true;
    default:
        return false;
    }
}

} // namespace

InteractiveBrokersGateway::InteractiveBrokersGateway()
    : signal_(std::make_unique<EReaderOSSignal>(2000)),
      client_(std::make_unique<EClientSocket>(this, signal_.get())),
      readerLoopRunning_(false),
      nextRequestId_(1000),
      nextOrderIdValue_(-1),
      connectionReady_(false),
      positionsComplete_(false),
      openOrdersComplete_(false),
      accountDownloadComplete_(false) {}

InteractiveBrokersGateway::~InteractiveBrokersGateway() {
    disconnect();
}

bool InteractiveBrokersGateway::connect(const IBConnectionSettings& settings) {
    disconnect();

    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        settings_ = settings;
        managedAccountsValue_.clear();
        nextOrderIdValue_ = -1;
        connectionReady_ = false;
        positionsComplete_ = false;
        openOrdersComplete_ = false;
        accountDownloadComplete_ = false;
        completedHistoricalRequests_.clear();
    }

    const bool connected = client_->eConnect(settings.host.c_str(), settings.port, settings.clientId);
    if (!connected) {
        emitEvent("connection.error",
                  {stringField("message", "Unable to open socket to TWS or IB Gateway")});
        return false;
    }

    reader_ = std::make_unique<EReader>(client_.get(), signal_.get());
    reader_->start();
    startReaderLoop();

    if (!waitUntilReady(std::chrono::seconds(settings.readyTimeoutSeconds))) {
        emitEvent("connection.timeout",
                  {intField("readyTimeoutSeconds", settings.readyTimeoutSeconds)});
        disconnect();
        return false;
    }

    return true;
}

void InteractiveBrokersGateway::disconnect() {
    stopReaderLoop();

    if (client_ && client_->isConnected()) {
        client_->eDisconnect();
    }

    markConnectionState(false);
}

bool InteractiveBrokersGateway::isConnected() const {
    return client_ && client_->isConnected();
}

void InteractiveBrokersGateway::setEventCallback(EventCallback callback) {
    std::lock_guard<std::mutex> lock(outputMutex_);
    eventCallback_ = std::move(callback);
}

void InteractiveBrokersGateway::setEventLogFile(const std::string& path) {
    if (path.empty()) {
        std::lock_guard<std::mutex> lock(outputMutex_);
        eventLog_.close();
        return;
    }

    std::ofstream stream(path, std::ios::out | std::ios::app);
    if (!stream) {
        throw std::runtime_error("Unable to open event log file: " + path);
    }

    std::lock_guard<std::mutex> lock(outputMutex_);
    eventLog_ = std::move(stream);
}

bool InteractiveBrokersGateway::waitUntilReady(std::chrono::seconds timeout) {
    std::unique_lock<std::mutex> lock(stateMutex_);
    stateCondition_.wait_for(lock, timeout, [this]() {
        return connectionReady_ || !isConnected();
    });
    return connectionReady_;
}

bool InteractiveBrokersGateway::waitForHistoricalDataEnd(int requestId,
                                                         std::chrono::seconds timeout) {
    std::unique_lock<std::mutex> lock(stateMutex_);
    historicalCondition_.wait_for(lock, timeout, [this, requestId]() {
        return completedHistoricalRequests_.count(requestId) > 0 || !connectionReady_;
    });
    return completedHistoricalRequests_.count(requestId) > 0;
}

bool InteractiveBrokersGateway::waitForPositionsEnd(std::chrono::seconds timeout) {
    std::unique_lock<std::mutex> lock(stateMutex_);
    positionsCondition_.wait_for(lock, timeout, [this]() {
        return positionsComplete_ || !connectionReady_;
    });
    return positionsComplete_;
}

bool InteractiveBrokersGateway::waitForOpenOrdersEnd(std::chrono::seconds timeout) {
    std::unique_lock<std::mutex> lock(stateMutex_);
    openOrdersCondition_.wait_for(lock, timeout, [this]() {
        return openOrdersComplete_ || !connectionReady_;
    });
    return openOrdersComplete_;
}

bool InteractiveBrokersGateway::waitForAccountDownloadEnd(std::chrono::seconds timeout) {
    std::unique_lock<std::mutex> lock(stateMutex_);
    accountCondition_.wait_for(lock, timeout, [this]() {
        return accountDownloadComplete_ || !connectionReady_;
    });
    return accountDownloadComplete_;
}

int InteractiveBrokersGateway::requestMarketData(const IBContractSpec& contractSpec,
                                                 const std::string& genericTicks,
                                                 bool snapshot,
                                                 bool regulatorySnapshot) {
    if (!waitUntilReady(std::chrono::seconds(settings_.readyTimeoutSeconds))) {
        throw std::runtime_error("IBKR connection is not ready");
    }

    const Contract contract = buildContract(contractSpec);
    const int requestId = acquireRequestId();

    client_->reqMktData(requestId,
                        contract,
                        genericTicks,
                        snapshot,
                        regulatorySnapshot,
                        TagValueListSPtr());

    emitEvent("request.marketData",
              {intField("reqId", requestId),
               stringField("symbol", contract.symbol),
               stringField("secType", contract.secType),
               stringField("exchange", contract.exchange),
               stringField("currency", contract.currency),
               stringField("genericTicks", genericTicks),
               boolField("snapshot", snapshot),
               boolField("regulatorySnapshot", regulatorySnapshot)});

    return requestId;
}

void InteractiveBrokersGateway::cancelMarketData(int requestId) {
    client_->cancelMktData(requestId);
    emitEvent("request.cancelMarketData", {intField("reqId", requestId)});
}

int InteractiveBrokersGateway::requestHistoricalData(const IBContractSpec& contractSpec,
                                                     const IBHistoricalDataRequest& request) {
    if (!waitUntilReady(std::chrono::seconds(settings_.readyTimeoutSeconds))) {
        throw std::runtime_error("IBKR connection is not ready");
    }

    const Contract contract = buildContract(contractSpec);
    const int requestId = acquireRequestId();

    client_->reqHistoricalData(requestId,
                               contract,
                               request.endDateTime,
                               request.durationStr,
                               request.barSizeSetting,
                               request.whatToShow,
                               request.useRTH,
                               request.formatDate,
                               request.keepUpToDate,
                               TagValueListSPtr());

    emitEvent("request.historicalData",
              {intField("reqId", requestId),
               stringField("symbol", contract.symbol),
               stringField("duration", request.durationStr),
               stringField("barSize", request.barSizeSetting),
               stringField("whatToShow", request.whatToShow),
               intField("useRTH", request.useRTH),
               intField("formatDate", request.formatDate),
               boolField("keepUpToDate", request.keepUpToDate),
               stringField("endDateTime", request.endDateTime)});

    return requestId;
}

void InteractiveBrokersGateway::cancelHistoricalData(int requestId) {
    client_->cancelHistoricalData(requestId);
    emitEvent("request.cancelHistoricalData", {intField("reqId", requestId)});
}

void InteractiveBrokersGateway::requestPositions() {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        positionsComplete_ = false;
    }
    client_->reqPositions();
    emitEvent("request.positions", {});
}

void InteractiveBrokersGateway::cancelPositions() {
    client_->cancelPositions();
    emitEvent("request.cancelPositions", {});
}

void InteractiveBrokersGateway::requestAccountUpdates(const std::string& account, bool subscribe) {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        accountDownloadComplete_ = false;
    }
    client_->reqAccountUpdates(subscribe, account);
    emitEvent("request.accountUpdates",
              {stringField("account", account), boolField("subscribe", subscribe)});
}

void InteractiveBrokersGateway::requestOpenOrders(bool allClients) {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        openOrdersComplete_ = false;
    }

    if (allClients) {
        client_->reqAllOpenOrders();
    } else {
        client_->reqOpenOrders();
    }

    emitEvent("request.openOrders", {boolField("allClients", allClients)});
}

int InteractiveBrokersGateway::placeLimitOrder(const IBContractSpec& contractSpec,
                                               const IBOrderRequest& orderRequest) {
    if (orderRequest.limitPrice <= 0.0) {
        throw std::invalid_argument("Limit price must be greater than zero");
    }

    if (!waitUntilReady(std::chrono::seconds(settings_.readyTimeoutSeconds))) {
        throw std::runtime_error("IBKR connection is not ready");
    }

    const Contract contract = buildContract(contractSpec);
    const Order order = buildLimitOrder(orderRequest);

    int orderId = -1;
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        if (nextOrderIdValue_ < 0) {
            throw std::runtime_error("IBKR did not provide nextValidId yet");
        }
        orderId = nextOrderIdValue_++;
    }

    client_->placeOrder(orderId, contract, order);
    emitEvent("request.placeOrder",
              {intField("orderId", orderId),
               stringField("symbol", contract.symbol),
               stringField("action", order.action),
               stringField("quantity", formatDecimal(order.totalQuantity)),
               doubleField("limitPrice", order.lmtPrice),
               stringField("tif", order.tif),
               boolField("transmit", order.transmit),
               stringField("account", order.account)});

    return orderId;
}

void InteractiveBrokersGateway::cancelOrder(int orderId) {
    OrderCancel orderCancel;
    client_->cancelOrder(orderId, orderCancel);
    emitEvent("request.cancelOrder", {intField("orderId", orderId)});
}

std::string InteractiveBrokersGateway::managedAccountsList() const {
    std::lock_guard<std::mutex> lock(stateMutex_);
    return managedAccountsValue_;
}

int InteractiveBrokersGateway::nextOrderId() const {
    std::lock_guard<std::mutex> lock(stateMutex_);
    return nextOrderIdValue_;
}

void InteractiveBrokersGateway::error(int id,
                                      time_t errorTime,
                                      int errorCode,
                                      const std::string& errorString,
                                      const std::string& advancedOrderRejectJson) {
    emitEvent(isInformationalCode(errorCode) ? "ib.notice" : "ib.error",
              {intField("id", id),
               longLongField("errorTime", static_cast<long long>(errorTime)),
               intField("code", errorCode),
               stringField("message", errorString),
               stringField("advancedOrderRejectJson", advancedOrderRejectJson)});

    if (errorCode == 502 || errorCode == 507) {
        markConnectionState(false);
    }
}

void InteractiveBrokersGateway::connectionClosed() {
    emitEvent("connection.closed", {});
    markConnectionState(false);
}

void InteractiveBrokersGateway::managedAccounts(const std::string& accountsList) {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        managedAccountsValue_ = accountsList;
    }
    emitEvent("connection.managedAccounts", {stringField("accounts", accountsList)});
}

void InteractiveBrokersGateway::nextValidId(int orderId) {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        nextOrderIdValue_ = orderId;
        connectionReady_ = true;
    }

    stateCondition_.notify_all();
    emitEvent("connection.ready",
              {intField("nextOrderId", orderId),
               stringField("accounts", managedAccountsList())});
}

void InteractiveBrokersGateway::tickPrice(int reqId,
                                          TickType field,
                                          double price,
                                          const TickAttrib&) {
    emitEvent("marketData.tickPrice",
              {intField("reqId", reqId),
               stringField("field", tickTypeToString(field)),
               doubleField("price", price)});
}

void InteractiveBrokersGateway::tickSize(int reqId, TickType field, Decimal size) {
    emitEvent("marketData.tickSize",
              {intField("reqId", reqId),
               stringField("field", tickTypeToString(field)),
               stringField("size", formatDecimal(size))});
}

void InteractiveBrokersGateway::tickString(int reqId,
                                           TickType tickType,
                                           const std::string& value) {
    emitEvent("marketData.tickString",
              {intField("reqId", reqId),
               stringField("field", tickTypeToString(tickType)),
               stringField("value", value)});
}

void InteractiveBrokersGateway::historicalData(int reqId, const Bar& bar) {
    emitEvent("historical.bar",
              {intField("reqId", reqId),
               stringField("time", bar.time),
               doubleField("open", bar.open),
               doubleField("high", bar.high),
               doubleField("low", bar.low),
               doubleField("close", bar.close),
               stringField("wap", formatDecimal(bar.wap)),
               stringField("volume", formatDecimal(bar.volume)),
               intField("count", bar.count)});
}

void InteractiveBrokersGateway::historicalDataEnd(int reqId,
                                                  const std::string& startDateStr,
                                                  const std::string& endDateStr) {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        completedHistoricalRequests_.insert(reqId);
    }

    historicalCondition_.notify_all();
    emitEvent("historical.end",
              {intField("reqId", reqId),
               stringField("start", startDateStr),
               stringField("end", endDateStr)});
}

void InteractiveBrokersGateway::orderStatus(int orderId,
                                            const std::string& status,
                                            Decimal filled,
                                            Decimal remaining,
                                            double avgFillPrice,
                                            long long permId,
                                            int parentId,
                                            double lastFillPrice,
                                            int clientId,
                                            const std::string& whyHeld,
                                            double mktCapPrice) {
    emitEvent("order.status",
              {intField("orderId", orderId),
               stringField("status", status),
               stringField("filled", formatDecimal(filled)),
               stringField("remaining", formatDecimal(remaining)),
               doubleField("avgFillPrice", avgFillPrice),
               longLongField("permId", permId),
               intField("parentId", parentId),
               doubleField("lastFillPrice", lastFillPrice),
               intField("clientId", clientId),
               stringField("whyHeld", whyHeld),
               doubleField("mktCapPrice", mktCapPrice)});
}

void InteractiveBrokersGateway::openOrder(int orderId,
                                          const Contract& contract,
                                          const Order& order,
                                          const OrderState&) {
    emitEvent("order.open",
              {intField("orderId", orderId),
               stringField("symbol", contract.symbol),
               stringField("secType", contract.secType),
               stringField("exchange", contract.exchange),
               stringField("action", order.action),
               stringField("orderType", order.orderType),
               stringField("quantity", formatDecimal(order.totalQuantity)),
               doubleField("limitPrice", order.lmtPrice),
               stringField("account", order.account),
               stringField("tif", order.tif)});
}

void InteractiveBrokersGateway::openOrderEnd() {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        openOrdersComplete_ = true;
    }

    openOrdersCondition_.notify_all();
    emitEvent("order.openEnd", {});
}

void InteractiveBrokersGateway::position(const std::string& account,
                                         const Contract& contract,
                                         Decimal position,
                                         double avgCost) {
    emitEvent("portfolio.position",
              {stringField("account", account),
               stringField("symbol", contract.symbol),
               stringField("secType", contract.secType),
               stringField("exchange", contract.exchange),
               stringField("position", formatDecimal(position)),
               doubleField("avgCost", avgCost)});
}

void InteractiveBrokersGateway::positionEnd() {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        positionsComplete_ = true;
    }

    positionsCondition_.notify_all();
    emitEvent("portfolio.positionEnd", {});
}

void InteractiveBrokersGateway::updateAccountValue(const std::string& key,
                                                   const std::string& val,
                                                   const std::string& currency,
                                                   const std::string& accountName) {
    emitEvent("account.value",
              {stringField("key", key),
               stringField("value", val),
               stringField("currency", currency),
               stringField("account", accountName)});
}

void InteractiveBrokersGateway::updatePortfolio(const Contract& contract,
                                                Decimal position,
                                                double marketPrice,
                                                double marketValue,
                                                double averageCost,
                                                double unrealizedPNL,
                                                double realizedPNL,
                                                const std::string& accountName) {
    emitEvent("account.portfolio",
              {stringField("account", accountName),
               stringField("symbol", contract.symbol),
               stringField("secType", contract.secType),
               stringField("position", formatDecimal(position)),
               doubleField("marketPrice", marketPrice),
               doubleField("marketValue", marketValue),
               doubleField("averageCost", averageCost),
               doubleField("unrealizedPNL", unrealizedPNL),
               doubleField("realizedPNL", realizedPNL)});
}

void InteractiveBrokersGateway::updateAccountTime(const std::string& timeStamp) {
    emitEvent("account.time", {stringField("time", timeStamp)});
}

void InteractiveBrokersGateway::accountDownloadEnd(const std::string& accountName) {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        accountDownloadComplete_ = true;
    }

    accountCondition_.notify_all();
    emitEvent("account.downloadEnd", {stringField("account", accountName)});
}

InteractiveBrokersGateway::JsonField InteractiveBrokersGateway::stringField(
    const std::string& key,
    const std::string& value) {
    return JsonField{key, value, false};
}

InteractiveBrokersGateway::JsonField InteractiveBrokersGateway::rawField(
    const std::string& key,
    const std::string& value) {
    return JsonField{key, value, true};
}

InteractiveBrokersGateway::JsonField InteractiveBrokersGateway::intField(
    const std::string& key,
    int value) {
    return rawField(key, std::to_string(value));
}

InteractiveBrokersGateway::JsonField InteractiveBrokersGateway::longLongField(
    const std::string& key,
    long long value) {
    return rawField(key, std::to_string(value));
}

InteractiveBrokersGateway::JsonField InteractiveBrokersGateway::doubleField(
    const std::string& key,
    double value) {
    return rawField(key, formatDouble(value));
}

InteractiveBrokersGateway::JsonField InteractiveBrokersGateway::boolField(
    const std::string& key,
    bool value) {
    return rawField(key, value ? "true" : "false");
}

std::string InteractiveBrokersGateway::jsonEscape(const std::string& value) {
    std::ostringstream escaped;
    for (const char ch : value) {
        switch (ch) {
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
            if (static_cast<unsigned char>(ch) < 0x20U) {
                escaped << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(static_cast<unsigned char>(ch)) << std::dec;
            } else {
                escaped << ch;
            }
            break;
        }
    }
    return escaped.str();
}

std::string InteractiveBrokersGateway::formatDouble(double value) {
    if (!std::isfinite(value) || std::fabs(value) > 1.0e307) {
        return "null";
    }

    std::ostringstream stream;
    stream << std::setprecision(10) << value;
    return stream.str();
}

std::string InteractiveBrokersGateway::formatDecimal(Decimal value) {
    if (value == UNSET_DECIMAL) {
        return "";
    }
    return DecimalFunctions::decimalStringToDisplay(value);
}

std::string InteractiveBrokersGateway::tickTypeToString(TickType tickType) {
    switch (tickType) {
    case BID_SIZE:
        return "BID_SIZE";
    case BID:
        return "BID";
    case ASK:
        return "ASK";
    case ASK_SIZE:
        return "ASK_SIZE";
    case LAST:
        return "LAST";
    case LAST_SIZE:
        return "LAST_SIZE";
    case HIGH:
        return "HIGH";
    case LOW:
        return "LOW";
    case VOLUME:
        return "VOLUME";
    case CLOSE:
        return "CLOSE";
    default:
        return "OTHER";
    }
}

int InteractiveBrokersGateway::acquireRequestId() {
    return nextRequestId_.fetch_add(1);
}

Contract InteractiveBrokersGateway::buildContract(const IBContractSpec& contractSpec) const {
    if (contractSpec.symbol.empty()) {
        throw std::invalid_argument("Contract symbol is required");
    }

    Contract contract;
    contract.symbol = contractSpec.symbol;
    contract.secType = contractSpec.secType;
    contract.exchange = contractSpec.exchange;
    contract.primaryExchange = contractSpec.primaryExchange;
    contract.currency = contractSpec.currency;
    return contract;
}

Order InteractiveBrokersGateway::buildLimitOrder(const IBOrderRequest& orderRequest) const {
    if (orderRequest.quantity.empty()) {
        throw std::invalid_argument("Order quantity is required");
    }

    Order order;
    order.action = orderRequest.action;
    order.orderType = orderRequest.orderType;
    order.totalQuantity = DecimalFunctions::stringToDecimal(orderRequest.quantity);
    order.lmtPrice = orderRequest.limitPrice;
    order.tif = orderRequest.tif;
    order.transmit = orderRequest.transmit;
    order.account = orderRequest.account;
    return order;
}

void InteractiveBrokersGateway::startReaderLoop() {
    stopReaderLoop();
    readerLoopRunning_.store(true);

    readerThread_ = std::thread([this]() {
        while (readerLoopRunning_.load()) {
            if (!client_ || !client_->isConnected()) {
                break;
            }

            signal_->waitForSignal();
            if (!readerLoopRunning_.load()) {
                break;
            }

            if (!client_->isConnected()) {
                continue;
            }

            try {
                reader_->processMsgs();
            } catch (const std::exception& error) {
                emitEvent("ib.reader.exception", {stringField("message", error.what())});
            } catch (...) {
                emitEvent("ib.reader.exception",
                          {stringField("message", "Unknown exception in IB reader loop")});
            }
        }

        markConnectionState(false);
    });
}

void InteractiveBrokersGateway::stopReaderLoop() {
    readerLoopRunning_.store(false);
    if (signal_) {
        signal_->issueSignal();
    }

    if (readerThread_.joinable()) {
        readerThread_.join();
    }

    reader_.reset();
}

void InteractiveBrokersGateway::emitEvent(const std::string& type,
                                          std::initializer_list<JsonField> fields) {
    std::ostringstream json;
    json << "{\"type\":\"" << jsonEscape(type) << "\"";
    for (const JsonField& field : fields) {
        json << ",\"" << jsonEscape(field.key) << "\":";
        if (field.raw) {
            json << field.value;
        } else {
            json << '"' << jsonEscape(field.value) << '"';
        }
    }
    json << '}';

    const std::string payload = json.str();
    EventCallback callback;

    {
        std::lock_guard<std::mutex> lock(outputMutex_);
        std::cout << payload << std::endl;
        if (eventLog_.is_open()) {
            eventLog_ << payload << '\n';
            eventLog_.flush();
        }
        callback = eventCallback_;
    }

    if (callback) {
        callback(payload);
    }
}

void InteractiveBrokersGateway::markConnectionState(bool ready) {
    {
        std::lock_guard<std::mutex> lock(stateMutex_);
        connectionReady_ = ready;
        if (!ready) {
            positionsComplete_ = true;
            openOrdersComplete_ = true;
            accountDownloadComplete_ = true;
        }
    }

    stateCondition_.notify_all();
    historicalCondition_.notify_all();
    positionsCondition_.notify_all();
    openOrdersCondition_.notify_all();
    accountCondition_.notify_all();
}