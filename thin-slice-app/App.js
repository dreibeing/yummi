import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { StatusBar } from "expo-status-bar";
import Constants from "expo-constants";
import * as FileSystem from "expo-file-system";
import {
  ActivityIndicator,
  Alert,
  Platform,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { WebView } from "react-native-webview";
import { createExtensionRuntimeScript } from "./extensionRuntime";

const defaultServerUrl = Platform.select({
  android: "http://10.0.2.2:4010",
  ios: "http://localhost:4010",
  default: "http://localhost:4010",
});

const RAW_SERVER_URL =
  process.env.EXPO_PUBLIC_THIN_SLICE_SERVER_URL ??
  Constants.expoConfig?.extra?.thinSliceServerUrl ??
  defaultServerUrl;
const SERVER_BASE_URL = RAW_SERVER_URL.replace(/\/$/, "");
const PRODUCTS_ENDPOINT = `${SERVER_BASE_URL}/products/random`;
const PLACE_ORDER_ENDPOINT = `${SERVER_BASE_URL}/orders/place`;
const ACK_ORDER_ENDPOINT = (orderId) =>
  `${SERVER_BASE_URL}/orders/${orderId}/ack`;
const BASKET_SIZE = 2;
const FETCH_COUNT = 100;
const DESKTOP_USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
const APP_VERSION = "thin-slice v0.5.7";
const LOG_FILE_NAME = "woolworths-cart-runner-log.txt";
const LOG_FILE_BASE =
  FileSystem.documentDirectory ?? FileSystem.cacheDirectory ?? null;
const DEFAULT_LOG_FILE_URI = LOG_FILE_BASE
  ? `${LOG_FILE_BASE}${LOG_FILE_NAME}`
  : null;

const formatTime = (date) => {
  if (!date) {
    return "";
  }
  try {
    return date.toLocaleTimeString();
  } catch (error) {
    return date.toISOString();
  }
};

const normalizeOrderItems = (items) =>
  items.map((item, index) => {
    const metadata = item.metadata ?? {};
    const rawProductId =
      item.productId ??
      item.catalogRefId ??
      metadata.productId ??
      metadata.catalogRefId ??
      null;
    const productId = rawProductId != null ? String(rawProductId) : null;
    const rawCatalogRefId =
      item.catalogRefId ?? metadata.catalogRefId ?? metadata.productId ?? null;
    const catalogRefId =
      rawCatalogRefId != null
        ? String(rawCatalogRefId)
        : productId != null
        ? productId
        : null;
    const preferredUrl =
      item.url && item.url.trim()
        ? item.url
        : item.productUrl && item.productUrl.trim()
        ? item.productUrl
        : metadata.url && metadata.url.trim()
        ? metadata.url
        : metadata.productUrl && metadata.productUrl.trim()
        ? metadata.productUrl
        : null;
    const detailUrl =
      item.detailUrl && item.detailUrl.trim()
        ? item.detailUrl
        : metadata.detailUrl && metadata.detailUrl.trim()
        ? metadata.detailUrl
        : preferredUrl
        ? preferredUrl
        : productId
        ? `https://www.woolworths.co.za/prod/_/A-${productId}`
        : catalogRefId
        ? `https://www.woolworths.co.za/prod/_/A-${catalogRefId}`
        : null;

    return {
      index,
      title: item.title ?? item.name ?? item.key ?? `Item ${index + 1}`,
      productId,
      catalogRefId,
      sku: item.sku ?? metadata.sku ?? null,
      qty: Math.max(1, item.qty ?? 1),
      detailUrl,
      url: preferredUrl ?? detailUrl,
      itemListName:
        item.itemListName ?? metadata.itemListName ?? "Extension",
      raw: item,
    };
  });

const stageLabels = {
  idle: "Waiting to start",
  waiting_login: "Waiting for Woolworths login",
  waiting_address: "Waiting for delivery address",
  filling: "Adding items to your basket",
  completed: "Basket ready in Woolworths",
  failed: "Cart fill failed",
};

export default function App() {
  const [screen, setScreen] = useState("home");
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [products, setProducts] = useState([]);
  const [basket, setBasket] = useState([]);
  const [errorMessage, setErrorMessage] = useState(null);
  const [lastFetchedAt, setLastFetchedAt] = useState(null);
  const [logFileUri, setLogFileUri] = useState(DEFAULT_LOG_FILE_URI);
  const [orderStatus, setOrderStatus] = useState(null);
  const [activeOrder, setActiveOrder] = useState(null);
  const [runnerState, setRunnerState] = useState({
    stage: "idle",
    total: 0,
    processed: 0,
    ok: 0,
    failed: 0,
  });
  const [runnerLogs, setRunnerLogs] = useState([]);

  const webViewRef = useRef(null);
  const completionRef = useRef(false);
  const logFileUriRef = useRef(DEFAULT_LOG_FILE_URI);
  const logBufferRef = useRef([]);
  const serverLogErrorRef = useRef(false);

  useEffect(() => {
    if (!activeOrder) {
      setRunnerState({
        stage: "idle",
        total: 0,
        processed: 0,
        ok: 0,
        failed: 0,
      });
      setRunnerLogs([]);
    }
  }, [activeOrder]);

  const fetchSummary = useMemo(() => {
    if (!products.length) {
      return "No products fetched yet.";
    }
    const time = formatTime(lastFetchedAt);
    return `Fetched ${products.length} products${time ? ` at ${time}` : ""}.`;
  }, [products.length, lastFetchedAt]);

  const handleFetchProducts = useCallback(async () => {
    setIsLoading(true);
    setErrorMessage(null);
    setOrderStatus(null);
    try {
      const response = await fetch(`${PRODUCTS_ENDPOINT}?count=${FETCH_COUNT}`);
      if (!response.ok) {
        throw new Error(`Product fetch failed (${response.status})`);
      }
      const payload = await response.json();
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        throw new Error("Server returned 0 products");
      }
      setProducts(items);
      setLastFetchedAt(new Date());
    } catch (error) {
      console.error("Failed to fetch products", error);
      setErrorMessage(error.message ?? "Unknown error fetching products");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleBuildBasket = useCallback(() => {
    if (!products.length) {
      setErrorMessage("Fetch products before building the basket.");
      return;
    }
    const selection = products.slice(0, BASKET_SIZE).map((item) => ({
      ...item,
      qty: item.qty ?? 1,
    }));
    setBasket(selection);
    setOrderStatus(null);
    setErrorMessage(null);
    setScreen("basket");
  }, [products]);

  const acknowledgeOrder = useCallback(
    async (orderId, status, details) => {
      if (!orderId) return;
      try {
        await fetch(ACK_ORDER_ENDPOINT(orderId), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status,
            processedItems: details?.processedItems ?? null,
            error: details?.error ?? null,
          }),
        });
      } catch (error) {
        console.error("Failed to acknowledge order", error);
      }
    },
    [SERVER_BASE_URL]
  );

  const resetRunnerLogFile = useCallback(async () => {
    const uri = logFileUriRef.current;
    logBufferRef.current = [];
    if (uri) {
      try {
        await FileSystem.writeAsStringAsync(uri, "", {
          encoding: FileSystem.EncodingType.UTF8,
        });
      } catch (error) {
        console.warn("Failed to reset runner log file", error);
      }
    }
    if (SERVER_BASE_URL) {
      try {
        await fetch(`${SERVER_BASE_URL}/logs/runner/reset`, {
          method: "POST",
        });
        serverLogErrorRef.current = false;
      } catch (error) {
        if (!serverLogErrorRef.current) {
          console.warn("Failed to reset server runner log", error);
          serverLogErrorRef.current = true;
        }
      }
    }
  }, []);

  const appendRunnerLogFile = useCallback(
    async (line) => {
      if (!line) return;
      const uri = logFileUriRef.current;
      let buffer = Array.isArray(logBufferRef.current)
        ? logBufferRef.current
        : [];
      buffer.push(line);
      if (buffer.length > 200) {
        buffer.splice(0, buffer.length - 200);
      }
      logBufferRef.current = buffer;
      const fileContents = buffer.join("\n") + "\n";
      if (uri) {
        try {
          await FileSystem.writeAsStringAsync(uri, fileContents, {
            encoding: FileSystem.EncodingType.UTF8,
          });
        } catch (error) {
          console.warn("Failed to write runner log file", error);
        }
      }
      if (SERVER_BASE_URL) {
        try {
          await fetch(`${SERVER_BASE_URL}/logs/runner/append`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ line }),
          });
          serverLogErrorRef.current = false;
        } catch (error) {
          if (!serverLogErrorRef.current) {
            console.warn("Failed to append server runner log", error);
            serverLogErrorRef.current = true;
          }
        }
      }
    },
    []
  );

  useEffect(() => {
    if (!LOG_FILE_BASE) {
      setLogFileUri(null);
      logFileUriRef.current = null;
      return;
    }
    const uri = `${LOG_FILE_BASE}${LOG_FILE_NAME}`;
    logFileUriRef.current = uri;
    setLogFileUri(uri);
    resetRunnerLogFile();
  }, [resetRunnerLogFile]);

  const appendLog = useCallback((message) => {
    if (!message) return;
    const timestamp = new Date().toISOString();
    const line = `${timestamp} ${message}`;
    setRunnerLogs((prev) => [...prev.slice(-19), line]);
    appendRunnerLogFile(line);
  }, [appendRunnerLogFile]);

  const handleSubmitOrder = useCallback(async () => {
    if (!basket.length) {
      setErrorMessage("Basket empty. Build the basket before placing an order.");
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);
    const normalizedItems = normalizeOrderItems(basket);
    try {
      const response = await fetch(PLACE_ORDER_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: normalizedItems,
          metadata: {
            source: "thin-slice-app",
            requestedAt: new Date().toISOString(),
          },
        }),
      });
      if (!response.ok) {
        throw new Error(`Order queue failed (${response.status})`);
      }
      const payload = await response.json();
      const message =
        payload?.message ??
        "Order queued. Log into Woolworths to continue.";
      const redirectUrl =
        payload?.redirectUrl ?? "https://www.woolworths.co.za/login";
      const orderId = payload?.orderId ?? null;
      const status = {
        message,
        receivedItems: payload?.receivedItems ?? basket.length,
        timestamp: new Date(),
        orderId,
      };
      setOrderStatus(status);
      await resetRunnerLogFile();
      setRunnerLogs([]);
      setRunnerState({
        stage: "waiting_login",
        total: normalizedItems.length,
        processed: 0,
        ok: 0,
        failed: 0,
      });
      completionRef.current = false;
      setActiveOrder({
        orderId,
        redirectUrl,
        initialUrl: redirectUrl,
        mode: "runner",
        items: normalizedItems,
      });
      setScreen("webview");
    } catch (error) {
      console.error("Failed to submit order", error);
      const msg = error.message ?? "Unknown order submission error";
      setErrorMessage(msg);
      Alert.alert("Order Placement Failed", msg);
    } finally {
      setIsSubmitting(false);
    }
  }, [basket, resetRunnerLogFile]);

  const handleGoToBasket = useCallback(() => {
    if (!basket.length) {
      Alert.alert(
        "Basket Empty",
        "Build the basket first to review items before placing an order."
      );
      return;
    }
    setScreen("basket");
  }, [basket.length]);

  const handleBackToHome = useCallback(() => {
    setScreen("home");
  }, []);

  const handleWebViewMessage = useCallback(
    (event) => {
      let data;
      try {
        data = JSON.parse(event.nativeEvent.data);
      } catch (parseError) {
        console.warn("Failed to parse runner message", parseError);
        return;
      }
      const { type, payload } = data || {};
      switch (type) {
        case "runner:init": {
          setRunnerState((prev) => ({
            ...prev,
            stage: "waiting_login",
            total: payload?.total ?? prev.total,
            processed: 0,
            ok: 0,
            failed: 0,
          }));
          appendLog(
            `Runner ready for ${payload?.total ?? 0} item(s). Sign in to continue.`
          );
          break;
        }
        case "runner:login-check": {
          setRunnerState((prev) => ({
            ...prev,
            stage: "waiting_login",
          }));
          break;
        }
        case "runner:login": {
          setRunnerState((prev) => ({
            ...prev,
            stage: "waiting_address",
          }));
          appendLog("Login detected. Checking delivery address...");
          break;
        }
        case "runner:start": {
          appendLog("Cart fill engine running.");
          setRunnerState((prev) => ({
            ...prev,
            stage: "filling",
          }));
          break;
        }
        case "runner:address_required": {
          setRunnerState((prev) => ({
            ...prev,
            stage: "waiting_address",
          }));
          appendLog(
            "Please select your delivery address or store, then stay on this page."
          );
          break;
        }
        case "runner:address_ready": {
          appendLog("Delivery address detected. Starting cart fill...");
          setRunnerState((prev) => ({
            ...prev,
            stage: "filling",
          }));
          break;
        }
        case "runner:item:start": {
          appendLog(
            `Adding ${payload?.title ?? `Item ${(payload?.index ?? 0) + 1}`}`
          );
          break;
        }
        case "runner:item:done": {
          const success = payload?.status === "ok";
          setRunnerState((prev) => ({
            ...prev,
            processed: Math.min(prev.total, prev.processed + 1),
            ok: prev.ok + (success ? 1 : 0),
            failed: prev.failed + (success ? 0 : 1),
          }));
          appendLog(
            `${success ? "✓" : "⚠"} ${
              payload?.title ?? `Item ${(payload?.index ?? 0) + 1}`
            }${payload?.reason ? ` (${payload.reason})` : ""}`
          );
          break;
        }
        case "runner:log": {
          appendLog(payload?.message ?? "");
          break;
        }
        case "runner:error": {
          if (!completionRef.current) {
            completionRef.current = true;
            const orderId = activeOrder?.orderId ?? null;
            acknowledgeOrder(orderId, "failed", {
              processedItems: null,
              error: payload?.reason ?? "runner_error",
            });
            setOrderStatus({
              message:
                "Cart fill failed. Please ensure you are logged in and try again.",
              receivedItems: basket.length,
              timestamp: new Date(),
              orderId,
            });
          }
          setRunnerState((prev) => ({
            ...prev,
            stage: "failed",
          }));
          appendLog(`Runner error: ${payload?.reason ?? "unknown"}`);
          Alert.alert(
            "Cart Fill Failed",
            "We couldn't complete the cart fill. Please try again after logging in."
          );
          setActiveOrder(null);
          setScreen("basket");
          break;
        }
        case "runner:complete": {
          if (!completionRef.current) {
            completionRef.current = true;
            const okCount = payload?.ok ?? 0;
            const failedCount = payload?.failed ?? 0;
            const success = failedCount === 0;
            const orderId = activeOrder?.orderId ?? null;
            acknowledgeOrder(orderId, success ? "completed" : "failed", {
              processedItems: {
                ok: okCount,
                failed: failedCount,
              },
              error: success ? null : "runner_partial_failure",
            });
            setRunnerState((prev) => ({
              ...prev,
              stage: success ? "completed" : "failed",
              processed: Math.min(prev.total, okCount + failedCount),
              ok: okCount,
              failed: failedCount,
            }));
            const msg = success
              ? "Cart filled successfully. Review your order in Woolworths."
              : `Cart fill finished with ${failedCount} issue(s). Review in Woolworths.`;
            setOrderStatus({
              message: msg,
              receivedItems: basket.length,
              timestamp: new Date(),
              orderId,
              ok: okCount,
              failed: failedCount,
            });
            appendLog("Runner complete. Redirecting to Woolworths cart.");
            setRunnerState((prev) => ({
              ...prev,
              stage: "completed",
            }));
            setActiveOrder((prev) =>
              prev
                ? {
                    orderId: prev.orderId,
                    redirectUrl: prev.redirectUrl,
                    initialUrl: "https://www.woolworths.co.za/check-out/cart",
                    mode: "cart",
                    items: [],
                    completedAt: new Date().toISOString(),
                  }
                : prev
            );
          }
          break;
        }
        default:
          break;
      }
    },
    [activeOrder, acknowledgeOrder, appendLog, basket.length]
  );

  if (screen === "webview" && activeOrder) {
    const injectedRunner =
      activeOrder.mode === "runner"
        ? createExtensionRuntimeScript({
            orderId: activeOrder.orderId,
            items: activeOrder.items,
            finalUrl: "https://www.woolworths.co.za/check-out/cart",
          })
        : null;
    const stageLabel = stageLabels[runnerState.stage] ?? "Working…";
    const recentLogs = runnerLogs.slice(-4);

    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="dark" />
        <View style={styles.header}>
          <Text style={styles.title}>Woolworths Checkout</Text>
          <Text style={styles.subtitle}>
            Sign in and keep this window open while we load your basket.
          </Text>
        </View>
        <View style={styles.webviewContainer}>
          <WebView
            key={`${activeOrder.orderId ?? "order"}-${activeOrder.mode ?? "runner"}`}
            ref={webViewRef}
            source={{ uri: activeOrder.initialUrl }}
            originWhitelist={["*"]}
            javaScriptEnabled
            sharedCookiesEnabled
            thirdPartyCookiesEnabled
            domStorageEnabled
            startInLoadingState
            userAgent={DESKTOP_USER_AGENT}
            injectedJavaScriptBeforeContentLoaded={
              activeOrder.mode === "runner" ? injectedRunner : undefined
            }
            onMessage={handleWebViewMessage}
            renderLoading={() => (
              <View style={styles.webviewLoader}>
                <ActivityIndicator size="large" color="#222" />
                <Text style={styles.webviewLoaderText}>
                  Loading Woolworths…
                </Text>
              </View>
            )}
          />
          {runnerState.stage !== "completed" ? (
            <View style={styles.webviewOverlay}>
              <ActivityIndicator size="large" color="#fff" />
              <Text style={styles.overlayTitle}>Filling your basket…</Text>
              <Text style={styles.overlaySubtitle}>
                Stay signed in. We'll show the Woolworths cart once everything is ready.
              </Text>
            </View>
          ) : null}
        </View>
        <View style={styles.runnerStatusCard}>
          <Text style={styles.runnerStatusHeading}>{stageLabel}</Text>
          <Text style={styles.runnerStatusText}>
            {runnerState.processed}/{runnerState.total} items • OK{" "}
            {runnerState.ok} • Failed {runnerState.failed}
          </Text>
          {runnerState.stage === "completed" && activeOrder?.completedAt ? (
            <Text style={styles.runnerLogText}>
              Keep this page open to confirm cart contents in Woolworths.
            </Text>
          ) : null}
          {recentLogs.map((line, idx) => (
            <Text key={`${idx}-${line}`} style={styles.runnerLogText}>
              {line}
            </Text>
          ))}
        </View>
        <View style={styles.footer}>
          <TouchableOpacity
            style={styles.secondaryButton}
            onPress={() => {
              setActiveOrder(null);
              setScreen("basket");
            }}
          >
            <Text style={styles.secondaryButtonText}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (screen === "basket") {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="dark" />
        <View style={styles.header}>
          <Text style={styles.title}>Basket Preview</Text>
          <Text style={styles.subtitle}>
            Showing {basket.length} items ready for Woolworths cart
          </Text>
        </View>
        <ScrollView contentContainerStyle={styles.scrollContent}>
          {basket.map((item, index) => (
            <View
              key={`${item.catalogRefId ?? item.productId ?? index}-${index}`}
              style={styles.basketRow}
            >
              <View style={styles.basketText}>
                <Text style={styles.itemTitle}>
                  {item.title ?? item.name ?? "Unknown Item"}
                </Text>
                <Text style={styles.itemMeta}>
                  Qty: {item.qty ?? 1} · Product ID:{" "}
                  {item.productId ?? item.catalogRefId ?? "?"}
                </Text>
                {item.catalogRefId ? (
                  <Text style={styles.itemMeta}>
                    Catalog Ref: {item.catalogRefId}
                  </Text>
                ) : null}
              </View>
            </View>
          ))}
          {orderStatus ? (
            <View style={styles.orderStatusCard}>
              <Text style={styles.summaryText}>{orderStatus.message}</Text>
              <Text style={styles.orderMeta}>
                Last Attempt: {formatTime(orderStatus.timestamp)}
              </Text>
              {orderStatus.orderId ? (
                <Text style={styles.orderMeta}>
                  Order ID: {orderStatus.orderId}
                </Text>
              ) : null}
              {typeof orderStatus.ok === "number" ||
              typeof orderStatus.failed === "number" ? (
                <Text style={styles.orderMeta}>
                  Results: OK {orderStatus.ok ?? 0} · Failed{" "}
                  {orderStatus.failed ?? 0}
                </Text>
              ) : null}
            </View>
          ) : null}
          {errorMessage ? (
            <Text style={styles.errorText}>{errorMessage}</Text>
          ) : null}
        </ScrollView>
        <View style={styles.footer}>
          <TouchableOpacity style={styles.secondaryButton} onPress={handleBackToHome}>
            <Text style={styles.secondaryButtonText}>Back</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[
              styles.primaryButton,
              isSubmitting ? styles.disabledButton : null,
            ]}
            onPress={handleSubmitOrder}
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.primaryButtonText}>Place Order</Text>
            )}
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <View style={styles.header}>
        <Text style={styles.title}>Thin Slice Shopper</Text>
        <Text style={styles.subtitle}>
          Prototype bridge between catalog resolver and Woolworths cart fill.
        </Text>
        <Text style={styles.versionBadge}>{APP_VERSION}</Text>
      </View>
      <View style={styles.body}>
        <TouchableOpacity
          style={[styles.primaryButton, styles.buttonSpacing]}
          onPress={handleFetchProducts}
          disabled={isLoading}
        >
          {isLoading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.primaryButtonText}>Fetch Products</Text>
          )}
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.primaryButton, styles.buttonSpacing]}
          onPress={handleBuildBasket}
          disabled={isLoading}
        >
          <Text style={styles.primaryButtonText}>Build Basket</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[
            styles.primaryButton,
            !basket.length ? styles.disabledButton : null,
          ]}
          onPress={handleGoToBasket}
          disabled={!basket.length}
        >
          <Text style={styles.primaryButtonText}>Place Order</Text>
        </TouchableOpacity>
        <View style={styles.summaryCard}>
          <Text style={styles.summaryText}>{fetchSummary}</Text>
          <Text style={styles.summaryText}>
            Basket is currently holding {basket.length} items.
          </Text>
        </View>
        {orderStatus ? (
          <View style={styles.orderStatusCard}>
            <Text style={styles.summaryText}>{orderStatus.message}</Text>
            <Text style={styles.orderMeta}>
              Last Attempt: {formatTime(orderStatus.timestamp)}
            </Text>
            {orderStatus.orderId ? (
              <Text style={styles.orderMeta}>
                Order ID: {orderStatus.orderId}
              </Text>
            ) : null}
          </View>
        ) : null}
        <View style={styles.serverInfo}>
          <Text style={styles.serverInfoText}>Server: {RAW_SERVER_URL}</Text>
          {logFileUri ? (
            <Text style={styles.serverInfoText}>
              Runner log: {logFileUri}
            </Text>
          ) : null}
        </View>
        {errorMessage ? (
          <Text style={styles.errorText}>{errorMessage}</Text>
        ) : null}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f5f5f5",
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 24,
    paddingBottom: 12,
  },
  title: {
    fontSize: 24,
    fontWeight: "600",
    color: "#222",
  },
  subtitle: {
    marginTop: 4,
    fontSize: 14,
    color: "#555",
  },
  versionBadge: {
    marginTop: 6,
    fontSize: 12,
    color: "#888",
  },
  body: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 24,
  },
  primaryButton: {
    backgroundColor: "#222",
    paddingVertical: 16,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  primaryButtonText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "600",
  },
  disabledButton: {
    opacity: 0.6,
  },
  secondaryButton: {
    flex: 1,
    backgroundColor: "#e0e0e0",
    paddingVertical: 16,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 12,
  },
  secondaryButtonText: {
    color: "#222",
    fontSize: 16,
    fontWeight: "600",
  },
  buttonSpacing: {
    marginBottom: 12,
  },
  summaryCard: {
    marginTop: 24,
    padding: 16,
    borderRadius: 12,
    backgroundColor: "#fff",
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
    gap: 6,
  },
  summaryText: {
    fontSize: 14,
    color: "#333",
  },
  orderStatusCard: {
    marginTop: 12,
    padding: 16,
    borderRadius: 12,
    backgroundColor: "#e8f5e9",
    borderWidth: 1,
    borderColor: "#66bb6a",
    gap: 6,
  },
  orderMeta: {
    fontSize: 12,
    color: "#2e7d32",
  },
  serverInfo: {
    marginTop: 12,
  },
  serverInfoText: {
    fontSize: 12,
    color: "#777",
  },
  errorText: {
    marginTop: 16,
    color: "#c62828",
    fontSize: 14,
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 24,
  },
  basketRow: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  basketText: {
    flexDirection: "column",
    gap: 4,
  },
  itemTitle: {
    fontSize: 16,
    fontWeight: "600",
    color: "#222",
  },
  itemMeta: {
    fontSize: 13,
    color: "#555",
  },
  footer: {
    flexDirection: "row",
    paddingHorizontal: 20,
    paddingBottom: 24,
    paddingTop: 12,
    gap: 12,
  },
  webviewContainer: {
    flex: 1,
    marginHorizontal: 12,
    borderRadius: 12,
    overflow: "hidden",
    backgroundColor: "#fff",
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
    position: "relative",
  },
  webviewLoader: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
  },
  webviewOverlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0, 0, 0, 0.6)",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 24,
    gap: 12,
  },
  overlayTitle: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "600",
    textAlign: "center",
  },
  overlaySubtitle: {
    color: "#f0f0f0",
    fontSize: 13,
    textAlign: "center",
  },
  webviewLoaderText: {
    fontSize: 14,
    color: "#555",
  },
  runnerStatusCard: {
    marginHorizontal: 20,
    marginTop: 12,
    marginBottom: 4,
    padding: 16,
    borderRadius: 12,
    backgroundColor: "#ffffff",
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
    gap: 4,
  },
  runnerStatusHeading: {
    fontSize: 15,
    fontWeight: "600",
    color: "#222",
  },
  runnerStatusText: {
    fontSize: 13,
    color: "#444",
  },
  runnerLogText: {
    fontSize: 12,
    color: "#666",
  },
});


