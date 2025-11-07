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
import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";
import {
  ActivityIndicator,
  Alert,
  Platform,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { WebView } from "react-native-webview";
import { createExtensionRuntimeScript } from "./extensionRuntime";
import {
  ClerkProvider,
  SignedIn,
  SignedOut,
  useAuth,
  useOAuth,
  useUser,
} from "@clerk/clerk-expo";

const localServerUrl = Platform.select({
  android: "http://10.0.2.2:8000/v1/thin",
  ios: "http://localhost:8000/v1/thin",
  default: "http://localhost:8000/v1/thin",
});

const defaultProdServerUrl =
  "https://yummi-server-greenbean.fly.dev/v1/thin";

const isReleaseBuild =
  process.env.NODE_ENV === "production" ||
  Constants.executionEnvironment === "standalone";

const RAW_SERVER_URL =
  process.env.EXPO_PUBLIC_THIN_SLICE_SERVER_URL ??
  Constants.expoConfig?.extra?.thinSliceServerUrl ??
  (isReleaseBuild ? defaultProdServerUrl : localServerUrl);
const trimTrailingSlash = (value) =>
  typeof value === "string" ? value.replace(/\/$/, "") : null;

const SERVER_BASE_URL = trimTrailingSlash(RAW_SERVER_URL) ?? "";
const RAW_API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ??
  Constants.expoConfig?.extra?.apiBaseUrl ??
  null;
const API_BASE_URL = trimTrailingSlash(RAW_API_BASE_URL);
const RAW_CLERK_JWT_TEMPLATE =
  process.env.EXPO_PUBLIC_CLERK_JWT_TEMPLATE ??
  Constants.expoConfig?.extra?.clerkJwtTemplate ??
  null;
const CLERK_JWT_TEMPLATE =
  typeof RAW_CLERK_JWT_TEMPLATE === "string"
    ? RAW_CLERK_JWT_TEMPLATE.trim() || null
    : null;

const resolvePublishableKey = () => {
  if (__DEV__) {
    console.log("Clerk key (env):", process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY);
    console.log("Clerk key (extra):", Constants.expoConfig?.extra?.clerkPublishableKey);
  }
  const candidate =
    process.env.EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY ??
    Constants.expoConfig?.extra?.clerkPublishableKey ??
    null;
  if (typeof candidate === "string") {
    return candidate;
  }
  if (candidate == null) {
    return "";
  }
  try {
    return String(candidate);
  } catch (error) {
    console.warn("Unable to coerce clerk publishable key", error);
    return "";
  }
};

const CLERK_PUBLISHABLE_KEY = resolvePublishableKey();
const PAYFAST_RETURN_URL =
  process.env.EXPO_PUBLIC_PAYFAST_RETURN_URL ??
  Constants.expoConfig?.extra?.payfastReturnUrl ??
  "yummi://payfast/return";
const PAYFAST_CANCEL_URL =
  process.env.EXPO_PUBLIC_PAYFAST_CANCEL_URL ??
  Constants.expoConfig?.extra?.payfastCancelUrl ??
  "yummi://payfast/cancel";

WebBrowser.maybeCompleteAuthSession();

const clerkTokenCache = {
  async getToken(key) {
    try {
      return await SecureStore.getItemAsync(key);
    } catch (error) {
      console.warn("Failed to read Clerk token from SecureStore", error);
      return null;
    }
  },
  async saveToken(key, value) {
    try {
      await SecureStore.setItemAsync(key, value);
    } catch (error) {
      console.warn("Failed to persist Clerk token", error);
    }
  },
  async removeToken(key) {
    try {
      await SecureStore.deleteItemAsync(key);
    } catch (error) {
      console.warn("Failed to remove Clerk token", error);
    }
  },
};
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

const buildAutoSubmitHtml = (url, params) => {
  const inputs = Object.entries(params || {})
    .map(
      ([key, value]) =>
        `<input type="hidden" name="${key}" value="${
          value != null ? String(value).replace(/"/g, "&quot;") : ""
        }" />`
    )
    .join("");
  return `<!DOCTYPE html><html><body>
    <form id="payfast" action="${url}" method="post">${inputs}</form>
    <script>document.getElementById("payfast").submit();</script>
  </body></html>`;
};

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

const formatCurrency = (minor, currency = "ZAR") => {
  if (typeof minor !== "number" || Number.isNaN(minor)) {
    return `${currency.toUpperCase() === "ZAR" ? "R" : currency.toUpperCase() + " "}0.00`;
  }
  const amount = (minor / 100).toFixed(2);
  if (currency.toUpperCase() === "ZAR") {
    return `R${amount}`;
  }
  return `${currency.toUpperCase()} ${amount}`;
};

const getTrimmed = (value) => {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.replace(/^\s+|\s+$/g, "");
  return trimmed.length ? trimmed : null;
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
      getTrimmed(item.url) ??
      getTrimmed(item.productUrl) ??
      getTrimmed(metadata.url) ??
      getTrimmed(metadata.productUrl) ??
      null;
    const detailUrl =
      getTrimmed(item.detailUrl) ??
      getTrimmed(metadata.detailUrl) ??
      preferredUrl ??
      (productId ? `https://www.woolworths.co.za/prod/_/A-${productId}` : null) ??
      (catalogRefId ? `https://www.woolworths.co.za/prod/_/A-${catalogRefId}` : null);

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

function AppContent() {
  const { getToken, signOut, isLoaded: isAuthLoaded, userId } = useAuth();
  const { user } = useUser();
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
  const [wallet, setWallet] = useState(null);
  const [walletLoading, setWalletLoading] = useState(false);
  const [walletError, setWalletError] = useState(null);
  const [walletLastUpdated, setWalletLastUpdated] = useState(null);
  const [topUpAmount, setTopUpAmount] = useState("100");
  const [isTopUpLoading, setIsTopUpLoading] = useState(false);
  const [payfastSession, setPayfastSession] = useState(null);

  const userDisplayName = useMemo(() => {
    const primaryEmail = user?.primaryEmailAddress?.emailAddress;
    if (primaryEmail) {
      return primaryEmail;
    }
    const fallbackEmail = user?.emailAddresses?.[0]?.emailAddress;
    if (fallbackEmail) {
      return fallbackEmail;
    }
    if (user?.username) {
      return user.username;
    }
    if (userId) {
      return userId;
    }
    return null;
  }, [user, userId]);

  const handleSignOut = useCallback(async () => {
    try {
      await signOut();
    } catch (error) {
      console.error("Failed to sign out", error);
      Alert.alert("Sign-out failed", "Please try again.");
    }
  }, [signOut]);

  const renderAccountBanner = useCallback(() => {
    if (!userDisplayName) {
      return null;
    }
    return (
      <View style={styles.accountRow}>
        <Text style={styles.accountText}>{userDisplayName}</Text>
        <TouchableOpacity style={styles.signOutButton} onPress={handleSignOut}>
          <Text style={styles.signOutButtonText}>Sign out</Text>
        </TouchableOpacity>
      </View>
    );
  }, [handleSignOut, userDisplayName]);

  if (!isAuthLoaded) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="dark" />
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#222" />
        </View>
      </SafeAreaView>
    );
  }

  const walletEndpoint = API_BASE_URL ? `${API_BASE_URL}/wallet/balance` : null;
  const payfastInitiateEndpoint = API_BASE_URL
    ? `${API_BASE_URL}/payments/payfast/initiate`
    : null;

  const buildAuthHeaders = useCallback(
    async (extra = {}) => {
      try {
        const token = CLERK_JWT_TEMPLATE
          ? await getToken({ template: CLERK_JWT_TEMPLATE })
          : await getToken();
        if (!token) {
          throw new Error("Missing Clerk session token");
        }
        return {
          ...extra,
          Authorization: `Bearer ${token}`,
        };
      } catch (error) {
        console.error("Failed to retrieve Clerk session token", error);
        throw new Error("Unable to authenticate request. Please sign in again.");
      }
    },
    [getToken]
  );

  const fetchWallet = useCallback(async () => {
    if (!walletEndpoint) {
      return;
    }
    setWalletLoading(true);
    setWalletError(null);
    try {
      const headers = await buildAuthHeaders();
      const response = await fetch(walletEndpoint, {
        headers,
      });
      if (response.status === 404) {
        setWallet({ balanceMinor: 0, currency: "ZAR", transactions: [], userId: null });
        setWalletLastUpdated(new Date());
        return;
      }
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error("Unauthorized. Please sign in again.");
        }
        throw new Error(`Wallet fetch failed (${response.status})`);
      }
      const payload = await response.json();
      setWallet(payload);
      setWalletLastUpdated(new Date());
    } catch (error) {
      console.error("Failed to fetch wallet", error);
      setWalletError(error.message ?? "Unable to load wallet");
    } finally {
      setWalletLoading(false);
    }
  }, [walletEndpoint, buildAuthHeaders]);

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

  useEffect(() => {
    if (!walletEndpoint || !isAuthLoaded) {
      return;
    }
    fetchWallet();
    // We intentionally exclude fetchWallet to avoid redundant refetch loops when its identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [walletEndpoint, isAuthLoaded]);

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

  const handleRefreshWallet = useCallback(() => {
    fetchWallet();
  }, [fetchWallet]);

  const handlePayfastNavigation = useCallback(
    (state) => {
      if (!state?.url) return;
      const currentUrl = state.url;
      if (PAYFAST_RETURN_URL && currentUrl.startsWith(PAYFAST_RETURN_URL)) {
        setPayfastSession(null);
        setScreen("home");
        Alert.alert("Payment Submitted", "We are verifying your payment.");
        fetchWallet();
      } else if (
        PAYFAST_CANCEL_URL &&
        currentUrl.startsWith(PAYFAST_CANCEL_URL)
      ) {
        setPayfastSession(null);
        setScreen("home");
        Alert.alert("Payment Cancelled", "Top-up was cancelled by the user.");
        fetchWallet();
      }
    },
    [fetchWallet]
  );

  const handleCancelPayfast = useCallback(() => {
    setPayfastSession(null);
    setScreen("home");
    fetchWallet();
  }, [fetchWallet]);

  const handleTopUp = useCallback(async () => {
    if (!payfastInitiateEndpoint) {
      Alert.alert(
        "Configuration Required",
        "Set EXPO_PUBLIC_API_BASE_URL to enable wallet top-ups."
      );
      return;
    }
    const parsed = parseFloat(topUpAmount.replace(/,/g, "."));
    if (Number.isNaN(parsed) || parsed <= 0) {
      Alert.alert("Invalid Amount", "Enter a positive amount (e.g. 100)");
      return;
    }
    const amountMinor = Math.round(parsed * 100);
    setIsTopUpLoading(true);
    setWalletError(null);
    try {
      const headers = await buildAuthHeaders({ "Content-Type": "application/json" });
      const response = await fetch(payfastInitiateEndpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({
          amountMinor,
          currency: "ZAR",
          itemName: "Wallet Top-up (mobile)",
        }),
      });
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error("Unauthorized. Please sign in again.");
        }
        throw new Error(`Top-up initiation failed (${response.status})`);
      }
      const payload = await response.json();
      if (!payload?.url || !payload?.params) {
        throw new Error("Unexpected response from server");
      }
      setPayfastSession({
        html: buildAutoSubmitHtml(payload.url, payload.params),
        reference: payload.reference,
      });
      setScreen("payfast");
    } catch (error) {
      console.error("Failed to initiate top-up", error);
      Alert.alert("Top-up Failed", error.message ?? "Unable to start payment");
    } finally {
      setIsTopUpLoading(false);
    }
  }, [payfastInitiateEndpoint, topUpAmount, buildAuthHeaders]);

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
          {renderAccountBanner()}
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

  if (screen === "payfast" && payfastSession) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="dark" />
        <View style={styles.header}>
          <Text style={styles.title}>PayFast Checkout</Text>
          <Text style={styles.subtitle}>
            Complete the secure PayFast form to finish your top-up.
          </Text>
          {renderAccountBanner()}
        </View>
        <View style={styles.webviewContainer}>
          <WebView
            originWhitelist={["*"]}
            source={{ html: payfastSession.html }}
            onNavigationStateChange={handlePayfastNavigation}
            startInLoadingState
            renderLoading={() => (
              <View style={styles.webviewLoader}>
                <ActivityIndicator size="large" color="#222" />
                <Text style={styles.webviewLoaderText}>
                  Loading PayFast checkout…
                </Text>
              </View>
            )}
          />
        </View>
        <View style={styles.footer}>
          <TouchableOpacity
            style={styles.secondaryButton}
            onPress={handleCancelPayfast}
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
          {renderAccountBanner()}
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
        <Text style={[styles.title, styles.authTitle]}>Thin Slice Shopper</Text>
        <Text style={styles.subtitle}>
          Prototype bridge between catalog resolver and Woolworths cart fill.
        </Text>
        <Text style={styles.versionBadge}>{APP_VERSION}</Text>
        {renderAccountBanner()}
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
        <View style={styles.walletCard}>
          <View style={styles.walletHeader}>
            <Text style={styles.walletTitle}>Wallet</Text>
            <TouchableOpacity
              style={styles.walletRefreshButton}
              onPress={handleRefreshWallet}
              disabled={walletLoading}
            >
              {walletLoading ? (
                <ActivityIndicator size="small" color="#222" />
              ) : (
                <Text style={styles.walletRefreshText}>Refresh</Text>
              )}
            </TouchableOpacity>
          </View>
          <Text style={styles.walletBalance}>
            {wallet ? formatCurrency(wallet.balanceMinor, wallet.currency) : "—"}
          </Text>
          {walletLastUpdated ? (
            <Text style={styles.walletMeta}>
              Updated {formatTime(walletLastUpdated)}
            </Text>
          ) : null}
          {walletError ? (
            <Text style={styles.walletErrorText}>{walletError}</Text>
          ) : null}
          <View style={styles.topUpRow}>
            <TextInput
              style={styles.topUpInput}
              keyboardType="numeric"
              value={topUpAmount}
              onChangeText={setTopUpAmount}
              placeholder="Amount (R)"
              placeholderTextColor="#888"
            />
            <TouchableOpacity
              style={[styles.topUpButton, isTopUpLoading ? styles.disabledButton : null]}
              onPress={handleTopUp}
              disabled={isTopUpLoading}
            >
              {isTopUpLoading ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.topUpButtonText}>Top Up</Text>
              )}
            </TouchableOpacity>
          </View>
          {wallet?.transactions?.length ? (
            <View style={styles.walletTransactions}>
              {wallet.transactions.slice(0, 3).map((txn) => (
                <View key={txn.id} style={styles.walletTransactionRow}>
                  <Text style={styles.walletTransactionText}>
                    {formatCurrency(txn.amountMinor, txn.currency)} · {formatTime(new Date(txn.createdAt))}
                  </Text>
                  {txn.note ? (
                    <Text style={styles.walletTransactionNote}>{txn.note}</Text>
                  ) : null}
                </View>
              ))}
              {wallet.transactions.length > 3 ? (
                <Text style={styles.walletTransactionHint}>
                  Showing recent {Math.min(wallet.transactions.length, 3)} entries
                </Text>
              ) : null}
            </View>
          ) : (
            <Text style={styles.walletMeta}>No transactions yet.</Text>
          )}
          {!API_BASE_URL ? (
            <Text style={styles.walletErrorText}>
              Set EXPO_PUBLIC_API_BASE_URL to enable wallet features.
            </Text>
          ) : null}
        </View>
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

function SignedOutScreen() {
  const [isSigningIn, setIsSigningIn] = useState(false);
  const { startOAuthFlow } = useOAuth({ strategy: "oauth_google" });

  const handleSignIn = useCallback(async () => {
    try {
      setIsSigningIn(true);
      const { createdSessionId, setActive } = await startOAuthFlow();
      if (createdSessionId) {
        await setActive?.({ session: createdSessionId });
      }
    } catch (error) {
      console.error("Clerk sign-in failed", error);
      Alert.alert("Sign-in failed", error?.message ?? "Unable to sign in. Please try again.");
    } finally {
      setIsSigningIn(false);
    }
  }, [startOAuthFlow]);

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <View style={styles.authContainer}>
        <Text style={[styles.title, styles.authTitle]}>Thin Slice Shopper</Text>
        <Text style={styles.authSubtitle}>
          Sign in with your Yummi account to manage wallet balances and cart fills.
        </Text>
        <TouchableOpacity
          style={[
            styles.primaryButton,
            styles.buttonSpacing,
            isSigningIn ? styles.disabledButton : null,
          ]}
          onPress={handleSignIn}
          disabled={isSigningIn}
        >
          {isSigningIn ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.primaryButtonText}>Continue with Google</Text>
          )}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

function MissingClerkConfigScreen() {
  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <View style={styles.authContainer}>
        <Text style={[styles.title, styles.authTitle]}>Configuration Required</Text>
        <Text style={styles.authSubtitle}>
          Set EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY in app.config.js to enable authentication.
        </Text>
      </View>
    </SafeAreaView>
  );
}

export default function App() {
  const publishableKey =
    typeof CLERK_PUBLISHABLE_KEY === "string"
      ? CLERK_PUBLISHABLE_KEY.trim()
      : "";

  if (__DEV__) {
    console.log("Clerk publishable key (sanitized):", publishableKey, typeof publishableKey);
  }

  const isLikelyValidKey = /^pk_(test|live)_/i.test(publishableKey);

  if (!isLikelyValidKey) {
    return <MissingClerkConfigScreen />;
  }
  return (
    <ClerkProvider publishableKey={publishableKey} tokenCache={clerkTokenCache}>
      <SignedIn>
        <AppContent />
      </SignedIn>
      <SignedOut>
        <SignedOutScreen />
      </SignedOut>
    </ClerkProvider>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f5f5f5",
  },
  loadingContainer: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
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
  accountRow: {
    marginTop: 12,
    flexDirection: "row",
    alignItems: "center",
  },
  accountText: {
    flex: 1,
    fontSize: 13,
    color: "#333",
  },
  signOutButton: {
    marginLeft: 12,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: "#ededed",
  },
  signOutButtonText: {
    fontSize: 13,
    fontWeight: "600",
    color: "#c62828",
  },
  body: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 24,
  },
  authContainer: {
    flex: 1,
    paddingHorizontal: 32,
    paddingVertical: 24,
    alignItems: "center",
    justifyContent: "center",
  },
  authTitle: {
    textAlign: "center",
  },
  authSubtitle: {
    marginTop: 12,
    fontSize: 14,
    lineHeight: 20,
    color: "#555",
    textAlign: "center",
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
  walletCard: {
    marginTop: 24,
    padding: 16,
    borderRadius: 12,
    backgroundColor: "#fff",
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
    gap: 12,
  },
  walletHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  walletTitle: {
    fontSize: 16,
    fontWeight: "600",
    color: "#222",
  },
  walletRefreshButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: "#f0f0f0",
  },
  walletRefreshText: {
    fontSize: 12,
    fontWeight: "600",
    color: "#222",
  },
  walletBalance: {
    fontSize: 28,
    fontWeight: "700",
    color: "#1b5e20",
  },
  walletMeta: {
    fontSize: 12,
    color: "#555",
  },
  walletErrorText: {
    fontSize: 12,
    color: "#c62828",
  },
  topUpRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  topUpInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#ddd",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: "#222",
    backgroundColor: "#fafafa",
  },
  topUpButton: {
    backgroundColor: "#1b5e20",
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
  },
  topUpButtonText: {
    color: "#fff",
    fontSize: 14,
    fontWeight: "600",
  },
  walletTransactions: {
    gap: 6,
  },
  walletTransactionRow: {
    paddingVertical: 4,
    borderBottomWidth: 1,
    borderBottomColor: "#f0f0f0",
  },
  walletTransactionText: {
    fontSize: 13,
    color: "#333",
  },
  walletTransactionNote: {
    fontSize: 12,
    color: "#666",
  },
  walletTransactionHint: {
    fontSize: 12,
    color: "#777",
    marginTop: 4,
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


