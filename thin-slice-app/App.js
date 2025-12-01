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
  ScrollView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  TouchableWithoutFeedback,
  Image,
  View,
  Dimensions,
  useWindowDimensions,
} from "react-native";
import { SafeAreaProvider, SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { WebView } from "react-native-webview";
import { Feather } from "@expo/vector-icons";
import { createExtensionRuntimeScript } from "./extensionRuntime";
import {
  ClerkProvider,
  SignedIn,
  SignedOut,
  useAuth,
  useOAuth,
  useUser,
} from "@clerk/clerk-expo";
import definedTagsDocument from "./generated/defined_tags.json";

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

// Lightweight responsive helper for cross-device sizing
function useResponsive() {
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const shortSide = Math.min(width, height);
  const isTablet = shortSide >= 600;
  // Base on 390pt width. Clamp to keep design stable.
  const base = 390;
  const scale = Math.min(1.15, Math.max(0.9, shortSide / base));
  const headlineFontSize = Math.round(34 * scale);
  const cardMaxWidth = Math.min(width - 56, isTablet ? 560 : 420);
  const menuOverlayTop = insets.top + 64 + 12; // header height + spacing
  return { width, height, insets, scale, headlineFontSize, cardMaxWidth, menuOverlayTop };
}

const SERVER_BASE_URL = trimTrailingSlash(RAW_SERVER_URL) ?? "";
const RAW_API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ??
  Constants.expoConfig?.extra?.apiBaseUrl ??
  null;
const deriveApiBaseUrl = () => {
  const trimmed = trimTrailingSlash(RAW_API_BASE_URL);
  if (trimmed) {
    return trimmed;
  }
  if (SERVER_BASE_URL && SERVER_BASE_URL.endsWith("/v1/thin")) {
    const fallback = SERVER_BASE_URL.replace(/\/thin$/, "");
    if (__DEV__) {
      console.log("API_BASE_URL fallback ->", fallback);
    }
    return fallback;
  }
  return null;
};
const API_BASE_URL = deriveApiBaseUrl();
const RECOMMENDATIONS_LATEST_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/recommendations/latest`
  : null;
const PAST_ORDERS_STORAGE_KEY = "yummi_past_orders_v1";
const SHOPPING_LIST_STORAGE_KEY = "yummi_shopping_list_v1";
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
if (__DEV__) {
  console.log("API_BASE_URL (resolved):", API_BASE_URL);
}
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
const MAX_PAYFAST_POLLS = 45;
const YUMMI_LOGO_SOURCE = require("./assets/yummi-logo.png");
const DEFAULT_AUDIENCE_SERVINGS = 4;
const AUDIENCE_SERVINGS_LOOKUP = buildAudienceServingsLookup(definedTagsDocument);
const HOME_MEAL_DISPLAY_LIMIT = 50;
const urlStartsWith = (value, prefix) => {
  if (!value || !prefix) {
    return false;
  }
  try {
    return value.toLowerCase().startsWith(prefix.toLowerCase());
  } catch (error) {
    console.warn("Failed to compare PayFast URLs", error);
    return value.startsWith(prefix);
  }
};

function buildAudienceServingsLookup(document) {
  const lookup = {};
  const tags = document?.defined_tags;
  if (!Array.isArray(tags)) {
    return lookup;
  }
  tags.forEach((tag) => {
    if (tag?.category !== "Audience") {
      return;
    }
    const servings = parseServingsCount(tag?.description);
    if (typeof servings !== "number") {
      return;
    }
    const valueKey =
      typeof tag?.value === "string" ? tag.value.trim() : null;
    const idKey =
      typeof tag?.tag_id === "string" ? tag.tag_id.trim() : null;
    if (valueKey) {
      lookup[valueKey] = servings;
      lookup[valueKey.toLowerCase()] = servings;
    }
    if (idKey) {
      lookup[idKey] = servings;
      lookup[idKey.toLowerCase()] = servings;
    }
  });
  return lookup;
}

function parseServingsCount(text) {
  if (!text || typeof text !== "string") {
    return null;
  }
  const match = text.match(/(\d+)/);
  if (!match) {
    return null;
  }
  const parsed = parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function getMealAudienceTagValues(meal) {
  const tagSource =
    meal?.tags?.Audience ??
    meal?.tags?.audience ??
    meal?.meal_tags?.Audience ??
    meal?.meal_tags?.audience;
  if (!tagSource) {
    return [];
  }
  if (Array.isArray(tagSource)) {
    return tagSource.filter(Boolean);
  }
  return [tagSource];
}

function resolveAudienceServingsFromTags(meal) {
  const candidates = getMealAudienceTagValues(meal);
  for (const candidate of candidates) {
    const key = typeof candidate === "string" ? candidate.trim() : candidate;
    if (!key) {
      continue;
    }
    const normalizedKey = String(key);
    const lookupValue =
      AUDIENCE_SERVINGS_LOOKUP[normalizedKey] ??
      AUDIENCE_SERVINGS_LOOKUP[normalizedKey.toLowerCase()];
    if (typeof lookupValue === "number") {
      return lookupValue;
    }
  }
  return null;
}

function deriveMealServingsCount(meal) {
  const fromTags = resolveAudienceServingsFromTags(meal);
  if (typeof fromTags === "number") {
    return fromTags;
  }
  const fromText = parseServingsCount(meal?.servings);
  if (typeof fromText === "number") {
    return fromText;
  }
  return DEFAULT_AUDIENCE_SERVINGS;
}

function formatServingsPeopleLabel(count) {
  const safeCount =
    typeof count === "number" && Number.isFinite(count)
      ? count
      : DEFAULT_AUDIENCE_SERVINGS;
  const noun = safeCount === 1 ? "person" : "people";
  return `${safeCount} ${noun}`;
}

function deriveServingsTextForPayload(meal) {
  const count = deriveMealServingsCount(meal);
  if (typeof count === "number" && Number.isFinite(count)) {
    return `Serves ${count}`;
  }
  if (typeof meal?.servings === "string" && meal.servings.trim()) {
    return meal.servings;
  }
  return null;
}

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

const parseServerDate = (value) => {
  if (!value) {
    return null;
  }
  try {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.valueOf())) {
      return null;
    }
    return parsed;
  } catch (error) {
    return null;
  }
};

const normalizeGeneratedAtValue = (value) => {
  if (!value) {
    return null;
  }
  if (value instanceof Date) {
    return Number.isNaN(value.valueOf()) ? null : value.toISOString();
  }
  if (typeof value === "number") {
    const fromNumber = new Date(value);
    return Number.isNaN(fromNumber.valueOf()) ? null : fromNumber.toISOString();
  }
  if (typeof value === "string") {
    const parsed = parseServerDate(value);
    return parsed ? parsed.toISOString() : null;
  }
  return null;
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

const normalizePriceToMinorUnits = (value) => {
  if (typeof value === "number" && Number.isFinite(value)) {
    // Treat values >= 10000 as already minor units to avoid overshooting for cents inputs.
    if (Math.abs(value) >= 10000 && Number.isInteger(value)) {
      return Math.round(value);
    }
    return Math.round(value * 100);
  }
  if (typeof value === "string") {
    const cleaned = value.replace(/[^0-9,.\-]/g, "").replace(/,/g, ".");
    if (!cleaned) {
      return null;
    }
    const parsed = Number(cleaned);
    if (!Number.isNaN(parsed)) {
      return Math.round(parsed * 100);
    }
    return null;
  }
  if (value && typeof value === "object") {
    if (typeof value.amountMinor === "number" && Number.isFinite(value.amountMinor)) {
      return Math.round(value.amountMinor);
    }
    if (typeof value.amount === "number" && Number.isFinite(value.amount)) {
      return Math.round(value.amount * 100);
    }
    if (typeof value.value === "number" && Number.isFinite(value.value)) {
      return Math.round(value.value * 100);
    }
    if (typeof value.value === "string") {
      return normalizePriceToMinorUnits(value.value);
    }
  }
  return null;
};

const PREFERENCES_STATE_STORAGE_KEY = "yummi.preferences.state.v1";
const PREFERENCES_COMPLETED_STORAGE_KEY = "yummi.preferences.completed.v1";
const PREFERENCES_TAGS_VERSION = "2025.02.1"; // Keep in sync with data/tags/defined_tags.json
const PREFERENCES_API_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/preferences`
  : null;
const EXPLORATION_MEAL_TARGET = 20;
const EXPLORATION_API_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/recommendations/exploration`
  : null;
const USE_MEAL_CARD_EXPLORATION_UI = true;
const PREFERENCE_CONTROL_STATES = [
  { id: "like", label: "Like", icon: "👍" },
  { id: "neutral", label: "Skip", icon: "○" },
  { id: "dislike", label: "Dislike", icon: "👎" },
];
const PAST_ORDER_REACTION_CONTROLS = [
  { id: "dislike", label: "Mark meal as disliked", icon: "👎" },
  { id: "like", label: "Mark meal as liked", icon: "❤️" },
];
const RECOMMENDATION_MEAL_TARGET = 20;
const RECOMMENDATION_API_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/recommendations/feed`
  : null;
const SHOPPING_LIST_API_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/shopping-list/build`
  : null;
const DIETARY_RESTRICTIONS_CATEGORY_ID = "DietaryRestrictions";
const DIETARY_NO_RESTRICTIONS_TAG_ID = "dietres_none";
const ALLERGENS_CATEGORY_ID = "Allergens";
const ALLERGENS_NO_AVOIDANCE_TAG_ID = "allergen_none";
const NUTRITION_FOCUS_CATEGORY_ID = "NutritionFocus";
const NUTRITION_NO_FOCUS_TAG_ID = "nutrition_none";
const BASE_PREFERENCE_CATEGORIES = [
  {
    id: DIETARY_RESTRICTIONS_CATEGORY_ID,
    title: "Diet & Ethics",
    description: "Tell us which dietary guardrails apply to your household.",
    tags: [
      { id: DIETARY_NO_RESTRICTIONS_TAG_ID, label: "No restrictions" },
      { id: "dietres_vegan", label: "Vegan" },
      { id: "dietres_vegetarian", label: "Vegetarian" },
      { id: "dietres_pescatarian", label: "Pescatarian" },
      { id: "dietres_halal", label: "Halal" },
      { id: "dietres_kosher", label: "Kosher" },
    ],
  },
  {
    id: "Cuisine",
    title: "Cuisine Types",
    description: "Highlight the cuisines you’re most excited about.",
    tags: [
      { id: "cuisine_southaf", label: "South African" },
      { id: "cuisine_american", label: "American" },
      { id: "cuisine_mexican", label: "Mexican" },
      { id: "cuisine_caribbean", label: "Caribbean" },
      { id: "cuisine_latin", label: "Latin American" },
      { id: "cuisine_italian", label: "Italian" },
      { id: "cuisine_french", label: "French" },
      { id: "cuisine_greek", label: "Greek" },
      { id: "cuisine_turkish", label: "Turkish" },
      { id: "cuisine_mideast", label: "Middle Eastern" },
      { id: "cuisine_northaf", label: "North African" },
      { id: "cuisine_indian", label: "Indian" },
      { id: "cuisine_chinese", label: "Chinese" },
      { id: "cuisine_japanese", label: "Japanese" },
      { id: "cuisine_korean", label: "Korean" },
      { id: "cuisine_thai", label: "Thai" },
      { id: "cuisine_vietnamese", label: "Vietnamese" },
      { id: "cuisine_portuguese", label: "Portuguese" },
      { id: "cuisine_spanish", label: "Spanish" },
    ],
  },
  {
    id: "PrepTime",
    title: "Hands-On Time",
    description: "How much hands-on time works for your routine?",
    tags: [
      { id: "preptime_less15", label: "< 15 minutes" },
      { id: "preptime_15_30", label: "15–30 minutes" },
      { id: "preptime_30_60", label: "30–60 minutes" },
      { id: "preptime_60_plus", label: "60+ minutes" },
    ],
  },
  {
    id: "Complexity",
    title: "Skill & Complexity",
    description: "Match recipe difficulty to your comfort level.",
    tags: [
      { id: "complex_easy", label: "Simple" },
      { id: "complex_mid", label: "Intermediate" },
      { id: "complex_adv", label: "Advanced" },
      { id: "complex_show", label: "Showstopper" },
    ],
  },
  {
    id: "HeatSpice",
    title: "Heat & Spice",
    description: "Set your tolerance for spice and heat.",
    tags: [
      { id: "heat_none", label: "No heat" },
      { id: "heat_mild", label: "Mild" },
      { id: "heat_medium", label: "Medium" },
      { id: "heat_hot", label: "Hot" },
      { id: "heat_extra", label: "Extra hot" },
    ],
  },
  {
    id: "Audience",
    title: "Who Are We Feeding?",
    description: "Help us scale portions and vibes to your audience.",
    tags: [
      { id: "audience_solo", label: "Solo", helper: "1 serving" },
      { id: "audience_couple", label: "Couple", helper: "2 servings" },
      { id: "audience_family", label: "Family", helper: "4 servings" },
      { id: "audience_largefamily", label: "Large family", helper: "6 servings" },
      { id: "audience_extendedfamily", label: "Extended family", helper: "8 servings" },
    ],
  },
  {
    id: ALLERGENS_CATEGORY_ID,
    title: "Avoidances & Allergens",
    description: "Flag anything that must stay out of your kitchen.",
    tags: [
      { id: ALLERGENS_NO_AVOIDANCE_TAG_ID, label: "No allergen avoidance" },
      { id: "allergen_dairy", label: "Dairy" },
      { id: "allergen_egg", label: "Eggs" },
      { id: "allergen_gluten", label: "Gluten" },
      { id: "allergen_soy", label: "Soy" },
      { id: "allergen_nuts", label: "Nuts" },
      { id: "allergen_seafood", label: "Seafood" },
      { id: "allergen_sesame", label: "Sesame" },
    ],
  },
  {
    id: NUTRITION_FOCUS_CATEGORY_ID,
    title: "Nutrition Focus",
    description: "Call out wellness goals we should optimize for.",
    tags: [
      { id: NUTRITION_NO_FOCUS_TAG_ID, label: "No nutrition focus" },
      { id: "nutrition_highprotein", label: "High protein" },
      { id: "nutrition_lowcalorie", label: "Low calorie" },
      { id: "nutrition_lowcarb", label: "Low carb" },
      { id: "nutrition_keto", label: "Keto" },
      { id: "nutrition_lowfat", label: "Low fat" },
      { id: "nutrition_lowsodium", label: "Low sodium" },
      { id: "nutrition_highfiber", label: "High fiber" },
    ],
  },
  {
    id: "Equipment",
    title: "Equipment",
    description: "Let us know what gear is fair game.",
    tags: [
      { id: "equip_oven", label: "Oven" },
      {
        id: "equip_countertop_cooker",
        label: "Slow/pressure cooker",
      },
      { id: "equip_airfryer", label: "Air fryer" },
      { id: "equip_microwave", label: "Microwave" },
      { id: "equip_stove", label: "Stovetop" },
      { id: "equip_grill", label: "Outdoor grill" },
    ],
  },
  {
    id: "MealComponentPreference",
    title: "Meal Components",
    description: "Tell us how scratch-made or ready we should go.",
    tags: [
      { id: "mealcomp_fromscratch", label: "From scratch" },
      { id: "mealcomp_semiprepared", label: "Semi-prepared" },
      {
        id: "mealcomp_readymeal",
        label: "Ready-meal preferred",
      },
    ],
  },
];

// Controls the order of preference categories in the UI while keeping the full metadata above.
const PREFERENCE_CATEGORY_ORDER = [
  "Audience",
  "PrepTime",
  "Complexity",
  "MealComponentPreference",
  DIETARY_RESTRICTIONS_CATEGORY_ID,
  ALLERGENS_CATEGORY_ID,
  NUTRITION_FOCUS_CATEGORY_ID,
  "Cuisine",
  "HeatSpice",
];
const ORDERED_PREFERENCE_CATEGORIES = [
  ...PREFERENCE_CATEGORY_ORDER.map((categoryId) =>
    BASE_PREFERENCE_CATEGORIES.find((category) => category.id === categoryId)
  ),
  ...BASE_PREFERENCE_CATEGORIES.filter(
    (category) => !PREFERENCE_CATEGORY_ORDER.includes(category.id)
  ),
].filter(Boolean);
const SINGLE_SELECT_PREFERENCE_CATEGORY_IDS = new Set(["Audience"]);
const isSingleSelectPreferenceCategory = (categoryId) =>
  SINGLE_SELECT_PREFERENCE_CATEGORY_IDS.has(categoryId);
const TOGGLE_CATEGORY_BEHAVIOR = {
  [DIETARY_RESTRICTIONS_CATEGORY_ID]: {
    defaultTagId: DIETARY_NO_RESTRICTIONS_TAG_ID,
    defaultState: "like",
    selectionState: "like",
  },
  [ALLERGENS_CATEGORY_ID]: {
    defaultTagId: ALLERGENS_NO_AVOIDANCE_TAG_ID,
    defaultState: "like",
    selectionState: "dislike",
  },
  [NUTRITION_FOCUS_CATEGORY_ID]: {
    defaultTagId: NUTRITION_NO_FOCUS_TAG_ID,
    defaultState: "like",
    selectionState: "like",
  },
};
const MULTI_SELECT_TOGGLE_PREFERENCE_CATEGORY_IDS = new Set(
  Object.keys(TOGGLE_CATEGORY_BEHAVIOR)
);
const isMultiSelectTogglePreferenceCategory = (categoryId) =>
  MULTI_SELECT_TOGGLE_PREFERENCE_CATEGORY_IDS.has(categoryId);
const getToggleCategoryConfig = (categoryId) =>
  TOGGLE_CATEGORY_BEHAVIOR[categoryId] ?? null;

const clonePreferenceResponses = (responses = {}) => {
  return Object.entries(responses).reduce((acc, [categoryId, values]) => {
    acc[categoryId] = { ...values };
    return acc;
  }, {});
};

const applyPreferenceSmartLogic = (responses = {}) => {
  const next = clonePreferenceResponses(responses);
  Object.entries(TOGGLE_CATEGORY_BEHAVIOR).forEach(
    ([categoryId, config]) => {
      const categoryValues = {
        ...(next[categoryId] ?? {}),
      };
      const hasNonDefaultSelections = Object.entries(categoryValues).some(
        ([tagId, state]) =>
          tagId !== config.defaultTagId && state === config.selectionState
      );
      if (hasNonDefaultSelections) {
        delete categoryValues[config.defaultTagId];
      } else {
        categoryValues[config.defaultTagId] = config.defaultState;
      }
      if (Object.keys(categoryValues).length > 0) {
        next[categoryId] = categoryValues;
      } else {
        delete next[categoryId];
      }
    }
  );
  return next;
};

const hasMeaningfulPreferenceSelections = (responses = {}) => {
  const entries = Object.entries(responses ?? {});
  if (entries.length === 0) {
    return false;
  }
  for (const [categoryId, selections] of entries) {
    if (!selections || Object.keys(selections).length === 0) {
      continue;
    }
    const toggleConfig = getToggleCategoryConfig(categoryId);
    if (!toggleConfig) {
      return true;
    }
    const hasRealSelection = Object.entries(selections).some(
      ([tagId, state]) =>
        tagId !== toggleConfig.defaultTagId &&
        state === toggleConfig.selectionState
    );
    if (hasRealSelection) {
      return true;
    }
  }
  return false;
};

const shouldSkipPreferenceCategory = () => false;

const filterPreferenceCategoryTags = (category) => category.tags;

const buildPreferenceCategories = (responses = {}) => {
  const categories = [];
  ORDERED_PREFERENCE_CATEGORIES.forEach((category) => {
    if (shouldSkipPreferenceCategory(category.id, responses)) {
      return;
    }
    const filteredTags = filterPreferenceCategoryTags(category, responses);
    if (filteredTags.length === 0) {
      return;
    }
    categories.push({
      ...category,
      tags: filteredTags,
    });
  });
  return categories;
};

const getPreferenceValue = (responses, categoryId, tagId) => {
  return responses?.[categoryId]?.[tagId] ?? "neutral";
};

const resolvePreferenceSelectionValue = (currentValue, requestedValue) => {
  if (currentValue === requestedValue && requestedValue !== "neutral") {
    return "neutral";
  }
  return requestedValue;
};

const shortReference = (value) => {
  if (!value) return "";
  if (value.length <= 10) return value;
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
};

const formatPayfastStatus = (status) => {
  if (!status) return "Pending";
  const lower = status.toLowerCase();
  if (lower === "complete") return "Complete";
  return lower.charAt(0).toUpperCase() + lower.slice(1);
};

const isPayfastTrackerDone = (status, walletCredited) => {
  if (!status) {
    return false;
  }
  const normalized = status.toLowerCase();
  if (normalized === "complete") {
    return Boolean(walletCredited);
  }
  return normalized === "cancelled" || normalized === "failed";
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

const normalizeShoppingListProductSelection = (product) => {
  if (!product || typeof product !== "object") {
    return null;
  }
  const productId =
    product.productId ?? product.product_id ?? product.sku ?? null;
  const catalogRefId =
    product.catalogRefId ?? product.catalog_ref_id ?? null;
  const detailUrl =
    product.detailUrl ?? product.detail_url ?? product.url ?? null;
  const packages =
    typeof product.packages === "number" && Number.isFinite(product.packages)
      ? product.packages
      : null;
  return {
    productId: productId != null ? String(productId) : null,
    catalogRefId: catalogRefId != null ? String(catalogRefId) : null,
    name: product.name ?? product.title ?? null,
    detailUrl,
    packages,
    imageUrl: product.imageUrl ?? product.image_url ?? null,
  };
};

const extractLinkedProducts = (ingredient) => {
  if (!ingredient || typeof ingredient !== "object") {
    return [];
  }
  if (Array.isArray(ingredient.linkedProducts)) {
    return ingredient.linkedProducts;
  }
  if (Array.isArray(ingredient.linked_products)) {
    return ingredient.linked_products;
  }
  return [];
};

const pickPreferredShoppingListProduct = (ingredient) => {
  const candidates = extractLinkedProducts(ingredient)
    .map((product) => normalizeShoppingListProductSelection(product))
    .filter(Boolean);
  if (!candidates.length) {
    return null;
  }
  const withProductId = candidates.find((candidate) => candidate.productId);
  if (withProductId) {
    return withProductId;
  }
  const withCatalog = candidates.find((candidate) => candidate.catalogRefId);
  if (withCatalog) {
    return withCatalog;
  }
  return candidates[0];
};

const buildProductDetailUrl = (productId, catalogRefId) => {
  const resolved = productId ?? catalogRefId;
  if (!resolved) {
    return null;
  }
  return `https://www.woolworths.co.za/prod/_/A-${resolved}`;
};

const formatSkippedIngredientSummary = (entries, maxDisplay = 3) => {
  if (!Array.isArray(entries) || entries.length === 0) {
    return null;
  }
  const labels = entries
    .map((entry) => entry?.label)
    .filter((label) => typeof label === "string" && label.trim().length);
  if (!labels.length) {
    return `${entries.length} item${entries.length === 1 ? "" : "s"}`;
  }
  const preview = labels.slice(0, maxDisplay).join(", ");
  if (labels.length > maxDisplay) {
    return `${preview} +${labels.length - maxDisplay} more`;
  }
  return preview;
};

const shuffleMeals = (meals = []) => meals;

const parseIngredientQuantity = (value) => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const numeric = parseFloat(value);
    if (!Number.isNaN(numeric)) {
      return numeric;
    }
  }
  return 1;
};

const normalizeIngredientLabel = (value) => {
  if (typeof value !== "string") {
    if (value == null) {
      return null;
    }
    value = String(value);
  }
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\b(\d+|ml|l|g|kg|cups?|cup|teaspoons?|tablespoons?|tsp|tbsp|pack|packs|pk|x)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
};

const WATER_LABELS = new Set([
  "water",
  "warm water",
  "cold water",
  "ice water",
  "hot water",
  "boiling water",
  "tap water",
  "filtered water",
  "room temperature water",
]);

const STAPLE_SPICE_KEYWORDS = [
  "oregano",
  "paprika",
  "smoked paprika",
  "ground cumin",
  "cumin",
  "turmeric",
  "masala",
  "garam masala",
  "curry powder",
  "mixed herbs",
  "herb mix",
  "seasoning",
  "spice mix",
  "spice blend",
  "five spice",
  "all spice",
  "peri peri",
  "ground coriander",
];

const OIL_KEYWORDS = [
  "olive oil",
  "extra virgin olive oil",
  "cooking olive oil",
  "vegetable oil",
  "canola oil",
  "sunflower oil",
  "neutral oil",
  "rapeseed oil",
  "coconut oil",
];

const containsWord = (label, word) => {
  if (!label || !word) {
    return false;
  }
  const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`\\b${escaped}\\b`, "i");
  return regex.test(label);
};

const isMilkLabel = (label) => {
  if (!label || !label.includes("milk")) {
    return false;
  }
  const disqualifiers = ["chocolate", "powder", "condensed", "evaporated", "coconut", "almond", "soy", "oat"];
  if (disqualifiers.some((word) => label.includes(word))) {
    return false;
  }
  if (label === "milk") {
    return true;
  }
  const qualifiers = ["full cream", "fullcream", "low fat", "long life", "fresh", "whole", "skim", "fat free"];
  if (qualifiers.some((word) => label.includes(word))) {
    return true;
  }
  if (label.startsWith("milk ") || label.endsWith(" milk")) {
    return true;
  }
  return false;
};

const collectIngredientLabels = (ingredient, fallbackText = null) => {
  const labels = [];
  const push = (candidate) => {
    if (typeof candidate === "string" && candidate.trim().length) {
      labels.push(candidate);
    }
  };
  if (ingredient && typeof ingredient === "object") {
    push(ingredient.core_item_name);
    push(ingredient.name);
    push(ingredient.ingredient_line);
    push(ingredient.productName);
    const selectedProduct = ingredient.selectedProduct ?? ingredient.selected_product ?? null;
    if (selectedProduct && typeof selectedProduct === "object") {
      push(selectedProduct.name);
    }
  }
  push(fallbackText);
  return labels;
};

const pickFirstNonEmptyString = (...candidates) => {
  for (const candidate of candidates) {
    if (typeof candidate === "string") {
      const trimmed = candidate.trim();
      if (trimmed.length > 0) {
        return trimmed;
      }
    }
  }
  return null;
};

const formatNumericQuantityValue = (value) => {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  if (Math.abs(value - Math.round(value)) < 0.01) {
    return String(Math.round(value));
  }
  return value.toFixed(2).replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
};

const deriveMeasurementQuantityText = (ingredient) => {
  if (!ingredient || typeof ingredient !== "object") {
    return null;
  }
  const measurement =
    ingredient.requirementMeasurement ??
    ingredient.measurement ??
    ingredient.quantityMeasurement ??
    null;
  if (!measurement || typeof measurement !== "object") {
    return null;
  }
  const amountValue =
    typeof measurement.amount === "number" && Number.isFinite(measurement.amount)
      ? measurement.amount
      : typeof measurement.baseAmount === "number" && Number.isFinite(measurement.baseAmount)
      ? measurement.baseAmount
      : null;
  const unitLabel = pickFirstNonEmptyString(
    measurement.unitType,
    measurement.unit,
    measurement.unit_label,
    measurement.unitLabel
  );
  if (amountValue == null || !unitLabel) {
    return null;
  }
  const amountText = formatNumericQuantityValue(amountValue) ?? String(amountValue);
  return `${amountText} ${unitLabel}`.trim();
};

const formatMealIngredientQuantityText = (ingredient) => {
  if (!ingredient || typeof ingredient !== "object") {
    return null;
  }
  const stringQuantity = pickFirstNonEmptyString(
    ingredient.quantityText,
    ingredient.quantity_text,
    ingredient.quantityLabel,
    ingredient.quantity_label,
    ingredient.quantityDisplay,
    ingredient.quantity_display
  );
  if (stringQuantity) {
    return stringQuantity;
  }
  if (typeof ingredient.quantity === "string") {
    const trimmed = ingredient.quantity.trim();
    if (trimmed.length) {
      return trimmed;
    }
  } else if (typeof ingredient.quantity === "number") {
    const numericValue = formatNumericQuantityValue(ingredient.quantity);
    if (numericValue) {
      return numericValue;
    }
  }
  const numericQuantity =
    formatNumericQuantityValue(ingredient.quantityValue) ??
    formatNumericQuantityValue(ingredient.quantity_value) ??
    formatNumericQuantityValue(ingredient.requiredQuantity) ??
    formatNumericQuantityValue(ingredient.defaultQuantity);
  if (numericQuantity) {
    return numericQuantity;
  }
  return deriveMeasurementQuantityText(ingredient);
};

const resolveMealIngredientName = (ingredient, fallbackLabel) => {
  if (!ingredient || typeof ingredient !== "object") {
    return fallbackLabel;
  }
  return (
    pickFirstNonEmptyString(
      ingredient.core_item_name,
      ingredient.coreItemName,
      ingredient.coreName,
      ingredient.name,
      ingredient.displayName,
      ingredient.baseName,
      ingredient.ingredient_line,
      ingredient.text,
      ingredient.productName
    ) ?? fallbackLabel
  );
};

const formatMealIngredientDetailText = (ingredient, fallbackIndex = 0) => {
  const fallbackLabel = `Ingredient ${fallbackIndex + 1}`;
  const quantityText = formatMealIngredientQuantityText(ingredient);
  const ingredientName = resolveMealIngredientName(ingredient, fallbackLabel);
  const baseLabel = [quantityText, ingredientName].filter(Boolean).join(" ").trim() || fallbackLabel;
  const preparationText = pickFirstNonEmptyString(
    ingredient?.preparation,
    ingredient?.prepNote,
    ingredient?.preparationNote,
    ingredient?.preparation_note
  );
  if (preparationText) {
    return `${baseLabel} (${preparationText})`;
  }
  return baseLabel;
};

const coerceMealStepList = (value) => {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => {
      if (typeof entry === "string") {
        return entry.trim();
      }
      if (entry && typeof entry === "object") {
        const textValue =
          pickFirstNonEmptyString(entry.text, entry.step, entry.description) ??
          (typeof entry === "number" ? String(entry) : null);
        return textValue ? textValue.trim() : "";
      }
      if (entry == null) {
        return "";
      }
      return String(entry).trim();
    })
    .filter(Boolean);
};

const getMealPrepSteps = (meal) => {
  if (!meal) {
    return [];
  }
  const candidates = [
    meal.prepSteps,
    meal.prep_steps,
    meal.steps?.prep,
    meal.steps?.prepSteps,
    meal.prepInstructions,
    meal.prep_instructions,
  ];
  for (const candidate of candidates) {
    const normalized = coerceMealStepList(candidate);
    if (normalized.length) {
      return normalized;
    }
  }
  return [];
};

const getMealCookSteps = (meal) => {
  if (!meal) {
    return [];
  }
  const candidates = [
    meal.cookSteps,
    meal.cook_steps,
    meal.steps?.cook,
    meal.steps?.cookSteps,
    meal.cookInstructions,
    meal.cook_instructions,
  ];
  for (const candidate of candidates) {
    const normalized = coerceMealStepList(candidate);
    if (normalized.length) {
      return normalized;
    }
  }
  const instructions = coerceMealStepList(meal.instructions);
  return instructions;
};

const normalizeIngredientEntry = (entry) => {
  if (!entry) {
    return null;
  }
  if (typeof entry === "string") {
    return { name: entry };
  }
  if (typeof entry !== "object") {
    return null;
  }
  const normalizedName =
    pickFirstNonEmptyString(
      entry.name,
      entry.core_item_name,
      entry.coreItemName,
      entry.coreName,
      entry.displayName,
      entry.ingredient,
      entry.ingredient_line,
      entry.text,
      entry.productName
    ) ?? null;
  return {
    ...entry,
    name: normalizedName ?? entry.name ?? null,
  };
};

const getMealDetailIngredients = (meal) => {
  if (!meal) {
    return [];
  }
  const sources = [
    meal.ingredients,
    meal.finalIngredients,
    meal.final_ingredients,
  ];
  for (const source of sources) {
    if (Array.isArray(source) && source.length > 0) {
      return source.map(normalizeIngredientEntry).filter(Boolean);
    }
  }
  return [];
};

const isWaterIngredient = (labels) => {
  if (!Array.isArray(labels) || !labels.length) {
    return false;
  }
  return labels
    .map((label) => normalizeIngredientLabel(label))
    .filter(Boolean)
    .some((normalized) => WATER_LABELS.has(normalized));
};

const isStapleIngredient = (labels) => {
  if (!Array.isArray(labels) || !labels.length) {
    return false;
  }
  return labels
    .map((label) => normalizeIngredientLabel(label))
    .filter(Boolean)
    .some((normalized) => {
      if (!normalized || WATER_LABELS.has(normalized)) {
        return false;
      }
      if (containsWord(normalized, "salt") || containsWord(normalized, "pepper") || containsWord(normalized, "butter")) {
        return true;
      }
      if (isMilkLabel(normalized)) {
        return true;
      }
      if (OIL_KEYWORDS.some((keyword) => normalized.includes(keyword))) {
        return true;
      }
      if (STAPLE_SPICE_KEYWORDS.some((keyword) => normalized.includes(keyword))) {
        return true;
      }
      return false;
    });
};

const UNIT_DEFINITIONS = {
  g: { unitType: "weight", baseLabel: "g", multiplier: 1 },
  gram: { unitType: "weight", baseLabel: "g", multiplier: 1 },
  grams: { unitType: "weight", baseLabel: "g", multiplier: 1 },
  kilogram: { unitType: "weight", baseLabel: "g", multiplier: 1000 },
  kilograms: { unitType: "weight", baseLabel: "g", multiplier: 1000 },
  kg: { unitType: "weight", baseLabel: "g", multiplier: 1000 },
  mg: { unitType: "weight", baseLabel: "g", multiplier: 0.001 },
  milligram: { unitType: "weight", baseLabel: "g", multiplier: 0.001 },
  milligrams: { unitType: "weight", baseLabel: "g", multiplier: 0.001 },
  l: { unitType: "volume", baseLabel: "ml", multiplier: 1000 },
  litre: { unitType: "volume", baseLabel: "ml", multiplier: 1000 },
  litres: { unitType: "volume", baseLabel: "ml", multiplier: 1000 },
  liter: { unitType: "volume", baseLabel: "ml", multiplier: 1000 },
  liters: { unitType: "volume", baseLabel: "ml", multiplier: 1000 },
  ml: { unitType: "volume", baseLabel: "ml", multiplier: 1 },
  millilitre: { unitType: "volume", baseLabel: "ml", multiplier: 1 },
  millilitres: { unitType: "volume", baseLabel: "ml", multiplier: 1 },
  milliliter: { unitType: "volume", baseLabel: "ml", multiplier: 1 },
  milliliters: { unitType: "volume", baseLabel: "ml", multiplier: 1 },
};

const parseFractionalNumber = (value) => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const normalized = trimmed.replace(/,/g, ".").toLowerCase();
  // Handle compound fractions like "1 1/2"
  const compoundMatch = normalized.match(/^(\d+)\s+(\d+)\/(\d+)$/);
  if (compoundMatch) {
    const whole = parseFloat(compoundMatch[1]);
    const numerator = parseFloat(compoundMatch[2]);
    const denominator = parseFloat(compoundMatch[3]);
    if (denominator !== 0) {
      return whole + numerator / denominator;
    }
  }
  const fractionMatch = normalized.match(/^(\d+)\/(\d+)$/);
  if (fractionMatch) {
    const numerator = parseFloat(fractionMatch[1]);
    const denominator = parseFloat(fractionMatch[2]);
    if (denominator !== 0) {
      return numerator / denominator;
    }
  }
  const basicMatch = normalized.match(/-?\d+(?:\.\d+)?/);
  if (basicMatch) {
    const parsed = parseFloat(basicMatch[0]);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const parseMeasurementValue = (value) => {
  if (value == null) {
    return null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return { baseAmount: value, unitType: "count", unitLabel: "count" };
  }
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.toLowerCase();
  const multiplierMatch = normalized.match(
    /(\d+(?:[\.,]\d+)?(?:\s+\d+\/\d+)?)\s*[x×]\s*(\d+(?:[\.,]\d+)?(?:\s+\d+\/\d+)?)(?:\s*(kg|g|grams?|kilograms?|l|ml|litres?|liters?|millilitres?|milliliters?))?/
  );
  if (multiplierMatch) {
    const countValue = parseFractionalNumber(multiplierMatch[1]);
    const perValue = parseFractionalNumber(multiplierMatch[2]);
    const unitKey = multiplierMatch[3]?.trim();
    if (countValue != null && perValue != null) {
      if (unitKey && UNIT_DEFINITIONS[unitKey]) {
        const def = UNIT_DEFINITIONS[unitKey];
        const baseAmount = countValue * perValue * def.multiplier;
        return {
          baseAmount,
          unitType: def.unitType,
          unitLabel: def.baseLabel,
        };
      }
      return {
        baseAmount: countValue * perValue,
        unitType: "count",
        unitLabel: "count",
      };
    }
  }
  const unitMatch = normalized.match(
    /(\d+(?:[\.,]\d+)?(?:\s+\d+\/\d+)?|\d+\/\d+)\s*(kg|g|grams?|kilograms?|mg|milligrams?|l|ml|litres?|liters?|millilitres?|milliliters?)/
  );
  if (unitMatch) {
    const amount = parseFractionalNumber(unitMatch[1]);
    const unitKey = unitMatch[2];
    if (amount != null && UNIT_DEFINITIONS[unitKey]) {
      const def = UNIT_DEFINITIONS[unitKey];
      return {
        baseAmount: amount * def.multiplier,
        unitType: def.unitType,
        unitLabel: def.baseLabel,
      };
    }
  }
  const attachedUnitMatch = normalized.match(
    /(\d+(?:[\.,]\d+)?)(kg|g|mg|l|ml)/
  );
  if (attachedUnitMatch) {
    const amount = parseFractionalNumber(attachedUnitMatch[1]);
    const unitKey = attachedUnitMatch[2];
    if (amount != null && UNIT_DEFINITIONS[unitKey]) {
      const def = UNIT_DEFINITIONS[unitKey];
      return {
        baseAmount: amount * def.multiplier,
        unitType: def.unitType,
        unitLabel: def.baseLabel,
      };
    }
  }
  const countMatch = normalized.match(
    /(\d+(?:[\.,]\d+)?(?:\s+\d+\/\d+)?|\d+\/\d+)\s*(packets?|packs?|pk|pieces?|pcs|bunch(?:es)?|loaves?|bottles?|jars?|tins?|cans?|sticks?|wraps?|buns?)/
  );
  if (countMatch) {
    const amount = parseFractionalNumber(countMatch[1]);
    if (amount != null) {
      return {
        baseAmount: amount,
        unitType: "count",
        unitLabel: "count",
      };
    }
  }
  const defaultMatch = normalized.match(/(\d+(?:[\.,]\d+)?(?:\s+\d+\/\d+)?|\d+\/\d+)/);
  if (defaultMatch) {
    const amount = parseFractionalNumber(defaultMatch[1]);
    if (amount != null) {
      return {
        baseAmount: amount,
        unitType: "count",
        unitLabel: "count",
      };
    }
  }
  return null;
};

const deriveIngredientCoreKey = (ingredient, fallbackText = null) => {
  const candidates = [
    ingredient?.core_item_name,
    ingredient?.coreItemName,
    ingredient?.coreName,
    ingredient?.name,
    fallbackText,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeIngredientLabel(candidate);
    if (normalized) {
      return normalized;
    }
  }
  return normalizeIngredientLabel(fallbackText) || null;
};

const normalizeProductMeta = (ingredient) => {
  const nested =
    (ingredient?.selectedProduct || ingredient?.selected_product) ?? null;
  if (nested && typeof nested === "object") {
    return {
      id:
        nested.productId ??
        nested.product_id ??
        nested.catalogRefId ??
        nested.catalog_ref_id ??
        null,
      name: nested.name ?? ingredient?.productName ?? null,
      detailUrl: nested.detailUrl ?? nested.detail_url ?? ingredient?.detailUrl ?? null,
      salePrice: nested.salePrice ?? nested.sale_price ?? ingredient?.salePrice ?? null,
      packageQuantity:
        nested.packageQuantity ??
        nested.package_quantity ??
        ingredient?.packageQuantity ??
        ingredient?.package_quantity ??
        null,
      ingredientLine:
        nested.ingredient_line ??
        ingredient?.ingredient_line ??
        nested.name ??
        ingredient?.productName ??
        null,
    };
  }
  const productId =
    ingredient?.productId ??
    ingredient?.product_id ??
    ingredient?.catalogRefId ??
    ingredient?.catalog_ref_id ??
    null;
  if (
    productId != null ||
    ingredient?.productName ||
    ingredient?.packageQuantity != null ||
    ingredient?.package_quantity != null ||
    ingredient?.ingredient_line ||
    ingredient?.detailUrl ||
    ingredient?.salePrice != null
  ) {
    return {
      id: productId != null ? String(productId) : null,
      name: ingredient?.productName ?? ingredient?.ingredient_line ?? null,
      detailUrl: ingredient?.detailUrl ?? ingredient?.detail_url ?? null,
      salePrice: ingredient?.salePrice ?? ingredient?.sale_price ?? null,
      packageQuantity:
        ingredient?.packageQuantity ?? ingredient?.package_quantity ?? null,
      ingredientLine: ingredient?.ingredient_line ?? ingredient?.productName ?? null,
    };
  }
  return null;
};

const extractPackageQuantityValue = (ingredient, productMeta) => {
  const candidates = [
    ingredient?.packageQuantity,
    ingredient?.package_quantity,
    productMeta?.packageQuantity,
  ];
  for (const value of candidates) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
};

const summarizeGroupRequirement = (entries) => {
  let unitType = null;
  let totalAmount = 0;
  let fallbackPackages = 0;
  entries.forEach((entry) => {
    const qty =
      typeof entry.requiredQuantity === "number" && Number.isFinite(entry.requiredQuantity)
        ? entry.requiredQuantity
        : 1;
    fallbackPackages += qty;
    const measurement = entry.requirementMeasurement;
    if (measurement && measurement.baseAmount > 0) {
      if (!unitType) {
        unitType = measurement.unitType;
      }
      if (unitType === measurement.unitType) {
        totalAmount += measurement.baseAmount;
      } else {
        unitType = null;
        totalAmount = 0;
      }
    }
  });
  return {
    unitType: unitType ?? null,
    amount: unitType ? totalAmount : null,
    fallbackPackages,
  };
};

const selectBestGroupCandidate = (entries, requirementSummary) => {
  if (!entries.length) {
    return null;
  }
  const candidates = new Map();
  entries.forEach((entry) => {
    const key = entry.productId || entry.text;
    if (!candidates.has(key)) {
      candidates.set(key, {
        key,
        productId: entry.productId,
        productName: entry.productName || entry.text,
        displayText: entry.text,
        packMeasurement: entry.packageMeasurement || null,
        entries: [],
      });
    }
    const candidate = candidates.get(key);
    candidate.entries.push(entry);
    if (!candidate.packMeasurement && entry.packageMeasurement) {
      candidate.packMeasurement = entry.packageMeasurement;
    }
  });
  let best = null;
  for (const candidate of candidates.values()) {
    const measurement = candidate.packMeasurement;
    const requirementAmount = requirementSummary.amount;
    let packagesNeeded = null;
    let coverage = null;
    let hasMeasurement = false;
    if (
      measurement &&
      measurement.baseAmount > 0 &&
      requirementAmount != null &&
      measurement.unitType === requirementSummary.unitType
    ) {
      hasMeasurement = true;
      const packSize = measurement.baseAmount;
      packagesNeeded = Math.max(1, Math.ceil(requirementAmount / packSize));
      coverage = packagesNeeded * packSize;
    } else if (measurement && measurement.baseAmount > 0 && requirementSummary.unitType == null) {
      const observedAmount = candidate.entries.reduce((sum, entry) => {
        const packMeasure = entry.packageMeasurement;
        const packQty =
          typeof entry.requiredQuantity === "number" && Number.isFinite(entry.requiredQuantity)
            ? entry.requiredQuantity
            : 1;
        if (packMeasure && packMeasure.baseAmount > 0 && packMeasure.unitType === measurement.unitType) {
          return sum + packMeasure.baseAmount * packQty;
        }
        return sum;
      }, 0);
      if (observedAmount > 0) {
        hasMeasurement = true;
        const packSize = measurement.baseAmount;
        packagesNeeded = Math.max(1, Math.ceil(observedAmount / packSize));
        coverage = packagesNeeded * packSize;
      }
    }
    if (packagesNeeded == null) {
      packagesNeeded = candidate.entries.reduce((sum, entry) => {
        const qty =
          typeof entry.requiredQuantity === "number" && Number.isFinite(entry.requiredQuantity)
            ? entry.requiredQuantity
            : 1;
        return sum + qty;
      }, 0);
      coverage = packagesNeeded;
    }
    const waste =
      hasMeasurement && requirementAmount != null && coverage != null
        ? Math.max(0, coverage - requirementAmount)
        : null;
    candidate.evaluation = {
      packagesNeeded,
      coverage,
      waste,
      hasMeasurement,
      packSize: measurement?.baseAmount ?? null,
    };
    if (!best) {
      best = candidate;
      continue;
    }
    const currentEval = candidate.evaluation;
    const bestEval = best.evaluation;
    if (currentEval.hasMeasurement && !bestEval.hasMeasurement) {
      best = candidate;
      continue;
    }
    if (!currentEval.hasMeasurement && bestEval.hasMeasurement) {
      continue;
    }
    if (currentEval.hasMeasurement && bestEval.hasMeasurement) {
      if (currentEval.packagesNeeded === 1 && bestEval.packagesNeeded !== 1) {
        best = candidate;
        continue;
      }
      if (currentEval.packagesNeeded !== bestEval.packagesNeeded) {
        if (currentEval.packagesNeeded < bestEval.packagesNeeded) {
          best = candidate;
        }
        continue;
      }
      if (currentEval.waste != null && bestEval.waste != null && currentEval.waste !== bestEval.waste) {
        if (currentEval.waste < bestEval.waste) {
          best = candidate;
        }
        continue;
      }
      if (currentEval.packSize != null && bestEval.packSize != null && currentEval.packSize !== bestEval.packSize) {
        if (currentEval.packSize < bestEval.packSize) {
          best = candidate;
        }
        continue;
      }
    } else {
      if (currentEval.packagesNeeded !== bestEval.packagesNeeded) {
        if (currentEval.packagesNeeded < bestEval.packagesNeeded) {
          best = candidate;
        }
        continue;
      }
    }
    const currentName = candidate.productName || candidate.displayText || "";
    const bestName = best.productName || best.displayText || "";
    if (currentName.localeCompare(bestName) < 0) {
      best = candidate;
    }
  }
  return best;
};

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
  const { headlineFontSize, cardMaxWidth, menuOverlayTop, insets } = useResponsive();
  const [isWelcomeComplete, setIsWelcomeComplete] = useState(false);
  const [preferenceResponses, setPreferenceResponses] = useState({});
  const [isPreferenceStateReady, setIsPreferenceStateReady] = useState(false);
  const [isPreferencesFlowComplete, setIsPreferencesFlowComplete] =
    useState(false);
  const [activePreferenceIndex, setActivePreferenceIndex] = useState(0);
  const [hasAcknowledgedPreferenceComplete, setHasAcknowledgedPreferenceComplete] =
    useState(false);
  const [hasSeenExplorationResults, setHasSeenExplorationResults] = useState(false);
  const [hasFetchedRemotePreferences, setHasFetchedRemotePreferences] = useState(false);
  const [isOnboardingActive, setIsOnboardingActive] = useState(false);
  const [homeSurface, setHomeSurface] = useState("meal");
  const [isMealMenuOpen, setIsMealMenuOpen] = useState(false);
  const [, setIsPreferenceSyncing] = useState(false);
  const [, setPreferencesSyncError] = useState(null);
  const [, setLastPreferencesSyncedAt] = useState(null);
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
  const [payfastMonitor, setPayfastMonitor] = useState(null);
  const [explorationState, setExplorationState] = useState("idle");
  const [explorationMeals, setExplorationMeals] = useState([]);
  const [explorationNotes, setExplorationNotes] = useState([]);
  const [explorationSessionId, setExplorationSessionId] = useState(null);
  const [explorationError, setExplorationError] = useState(null);
  const [explorationReactions, setExplorationReactions] = useState({});
  const [isCompletingExploration, setIsCompletingExploration] = useState(false);
  const [homeRecommendedMeals, setHomeRecommendedMeals] = useState([]);
  const [homeRecommendationsGeneratedAt, setHomeRecommendationsGeneratedAt] = useState(null);
  const [selectedHomeMealIds, setSelectedHomeMealIds] = useState({});
  const [homeMealDislikedIds, setHomeMealDislikedIds] = useState({});
  const [homeMealModal, setHomeMealModal] = useState({
    visible: false,
    meal: null,
  });
  const [confirmationDialog, setConfirmationDialog] = useState({
    visible: false,
    context: null,
  });
  const [confirmationDialogSubtitle, setConfirmationDialogSubtitle] = useState(
    "Use one free use"
  );
  const [pastOrders, setPastOrders] = useState([]);
  const [activePastOrder, setActivePastOrder] = useState(null);
  const [isSorryToHearScreenVisible, setIsSorryToHearScreenVisible] = useState(false);
  const [ingredientQuantities, setIngredientQuantities] = useState({});
  const [shoppingListItems, setShoppingListItems] = useState([]);
  const [shoppingListStatus, setShoppingListStatus] = useState("idle");
  const [shoppingListError, setShoppingListError] = useState(null);
  const [checkedShoppingListItems, setCheckedShoppingListItems] = useState(() => new Set());
  const [imageReloadCounters, setImageReloadCounters] = useState({});
  const imageRetryTimeouts = useRef({});
  const imagePrefetchTimeouts = useRef({});
  const prefetchedImageUrls = useRef(new Set());
  const [activePastOrderShoppingList, setActivePastOrderShoppingList] = useState(null);
  const [isCartPushPending, setIsCartPushPending] = useState(false);
  const preferenceSyncHashRef = useRef(null);
  const preferenceEntryContextRef = useRef(null);
  const homeMealsBackupRef = useRef(null);
  const toggleDefaultsInitializedRef = useRef({});
  const latestRecommendationsRequestRef = useRef(null);
  const shoppingListLearningIntentRef = useRef(false);
  const shoppingListNextScreenRef = useRef("ingredients");

  const applyHomeRecommendedMeals = useCallback((meals, options = {}) => {
    const nextSource = Array.isArray(meals)
      ? meals.filter((meal) => Boolean(meal))
      : [];
    const randomizedMeals = shuffleMeals(nextSource);
    setHomeRecommendedMeals(randomizedMeals);
    setSelectedHomeMealIds({});
    setHomeMealDislikedIds({});
    const normalizedGeneratedAt = normalizeGeneratedAtValue(options.generatedAt);
    setHomeRecommendationsGeneratedAt(normalizedGeneratedAt);
  }, []);

  const persistPastOrders = useCallback(async (orders) => {
    try {
      await SecureStore.setItemAsync(
        PAST_ORDERS_STORAGE_KEY,
        JSON.stringify(orders ?? [])
      );
    } catch (error) {
      if (__DEV__) {
        console.warn("Unable to persist past orders", error);
      }
    }
  }, []);

  const hydratePastOrders = useCallback(async () => {
    try {
      const stored = await SecureStore.getItemAsync(PAST_ORDERS_STORAGE_KEY);
      if (!stored) {
        return;
      }
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) {
        setPastOrders(parsed);
      }
    } catch (error) {
      if (__DEV__) {
        console.warn("Unable to restore past orders", error);
      }
    }
  }, []);

  useEffect(() => {
    hydratePastOrders();
  }, [hydratePastOrders]);

  useEffect(() => {
    return () => {
      if (latestRecommendationsRequestRef.current) {
        latestRecommendationsRequestRef.current.abort();
        latestRecommendationsRequestRef.current = null;
      }
    };
  }, []);

  const persistShoppingList = useCallback(async (snapshot) => {
    try {
      await SecureStore.setItemAsync(
        SHOPPING_LIST_STORAGE_KEY,
        JSON.stringify(snapshot)
      );
    } catch (error) {
      if (__DEV__) {
        console.warn("Unable to persist shopping list", error);
      }
    }
  }, []);

  const hydrateShoppingList = useCallback(async () => {
    try {
      const stored = await SecureStore.getItemAsync(SHOPPING_LIST_STORAGE_KEY);
      if (!stored) {
        return;
      }
      const parsed = JSON.parse(stored);
      const items = Array.isArray(parsed?.items)
        ? parsed.items
        : Array.isArray(parsed)
        ? parsed
        : [];
      if (items.length) {
        setShoppingListItems(items);
        setShoppingListStatus(
          typeof parsed?.status === "string" ? parsed.status : "ready"
        );
      }
    } catch (error) {
      if (__DEV__) {
        console.warn("Unable to restore shopping list", error);
      }
    }
  }, []);

  const { height: SCREEN_HEIGHT } = Dimensions.get("window");
  const isSmallDevice = SCREEN_HEIGHT < 740;
  const FREE_USES_FONT_SIZE = isSmallDevice ? 68 : 84;
  const FOOTER_PADDING = 110;
  const preferenceCategories = useMemo(
    () => buildPreferenceCategories(preferenceResponses),
    [preferenceResponses]
  );
  const activePreferenceCategory =
    preferenceCategories[activePreferenceIndex] ?? null;
  const isSingleSelectCategory =
    activePreferenceCategory != null &&
    isSingleSelectPreferenceCategory(activePreferenceCategory.id);
  const activeToggleConfig = activePreferenceCategory
    ? getToggleCategoryConfig(activePreferenceCategory.id)
    : null;
  const isToggleCategory = Boolean(activeToggleConfig);
  const activeCategorySelections =
    activePreferenceCategory && preferenceResponses?.[activePreferenceCategory.id]
      ? preferenceResponses[activePreferenceCategory.id]
      : null;
  const activeCategoryRatingsCount = activeCategorySelections
    ? Object.keys(activeCategorySelections).length
    : 0;
  const hasOnlyToggleDefaultActive =
    isToggleCategory &&
    activeToggleConfig &&
    activeCategoryRatingsCount === 1 &&
    activeCategorySelections?.[activeToggleConfig.defaultTagId] ===
      activeToggleConfig.defaultState;
  const toggleDefaultLabel = isToggleCategory
    ? activePreferenceCategory?.tags?.find(
        (tag) => tag.id === activeToggleConfig?.defaultTagId
      )?.label ?? "the default option"
    : null;
  const shouldShowPreferenceCompletionScreen =
    isOnboardingActive &&
    isWelcomeComplete &&
    isPreferenceStateReady &&
    isPreferencesFlowComplete &&
    !hasAcknowledgedPreferenceComplete;
  const isMealHomeSurface = homeSurface === "meal";
  const displayedMeals = useMemo(() => {
    if (!homeRecommendedMeals.length) {
      return [];
    }
    return homeRecommendedMeals.slice(0, HOME_MEAL_DISPLAY_LIMIT);
  }, [homeRecommendedMeals]);
  const displayedMealsCount = displayedMeals.length;
  const selectedHomeMeals = useMemo(() => {
    if (!displayedMeals.length || !selectedHomeMealIds) {
      return [];
    }
    return displayedMeals.filter((meal) => {
      const mealId = meal?.mealId;
      if (!mealId) {
        return false;
      }
      return Boolean(selectedHomeMealIds[mealId]);
    });
  }, [displayedMeals, selectedHomeMealIds]);
  const { stapleIngredients, primaryIngredients } = useMemo(() => {
    const staples = [];
    const primary = [];
    shoppingListItems.forEach((item) => {
      if (!item) {
        return;
      }
      if ((item.classification ?? "").toLowerCase() === "pantry") {
        staples.push(item);
      } else {
        primary.push(item);
      }
    });
    return { stapleIngredients: staples, primaryIngredients: primary };
  }, [shoppingListItems]);

  useEffect(() => {
    persistShoppingList({
      status: shoppingListStatus,
      items: shoppingListItems,
    });
  }, [persistShoppingList, shoppingListItems, shoppingListStatus]);

  useEffect(() => {
    setCheckedShoppingListItems(new Set());
    setImageReloadCounters({});
    prefetchedImageUrls.current.clear();
    clearPrefetchTimeoutsFor("shoppingList");
    clearPrefetchTimeoutsFor("ingredients");
    Object.values(imageRetryTimeouts.current).forEach(clearTimeout);
    imageRetryTimeouts.current = {};
  }, [shoppingListItems, clearPrefetchTimeoutsFor]);

  useEffect(() => {
    return () => {
      Object.values(imageRetryTimeouts.current).forEach(clearTimeout);
      Object.values(imagePrefetchTimeouts.current).forEach(clearTimeout);
      imageRetryTimeouts.current = {};
      imagePrefetchTimeouts.current = {};
    };
  }, []);

  const canSendShoppingListToCart =
    shoppingListStatus === "ready" && shoppingListItems.length > 0;

  useEffect(() => {
    setIngredientQuantities((prev) => {
      const next = {};
      shoppingListItems.forEach((ingredient) => {
        if (!ingredient?.id) {
          return;
        }
        const existingValue = prev?.[ingredient.id];
        if (typeof existingValue === "number" && Number.isFinite(existingValue)) {
          next[ingredient.id] = existingValue;
        } else {
          next[ingredient.id] =
            typeof ingredient.defaultQuantity === "number" && Number.isFinite(ingredient.defaultQuantity)
              ? ingredient.defaultQuantity
              : typeof ingredient.requiredQuantity === "number" && Number.isFinite(ingredient.requiredQuantity)
              ? ingredient.requiredQuantity
              : 1;
        }
      });
      return next;
    });
  }, [shoppingListItems]);

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
      setPayfastMonitor(null);
      setPayfastSession(null);
      setIsOnboardingActive(false);
      setHomeSurface("meal");
      applyHomeRecommendedMeals([]);
    } catch (error) {
      console.error("Failed to sign out", error);
      Alert.alert("Sign-out failed", "Please try again.");
    }
  }, [applyHomeRecommendedMeals, signOut]);

  const handleMealMenuSignOut = useCallback(() => {
    closeMealMenu();
    handleSignOut();
  }, [closeMealMenu, handleSignOut]);

  const renderAccountBanner = useCallback(
    (options = {}) => {
      if (!userDisplayName) {
        return null;
      }
      const { hideSignOutButton = false } = options;
      return (
        <View style={styles.accountRow}>
          <Text style={styles.accountText}>{userDisplayName}</Text>
          {!hideSignOutButton ? (
            <TouchableOpacity style={styles.signOutButton} onPress={handleSignOut}>
              <Text style={styles.signOutButtonText}>Sign out</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      );
    },
    [handleSignOut, userDisplayName]
  );

  const handleShareForFreeUses = useCallback(async () => {
    try {
      await Share.share({
        title: "Invite friends to Yummi",
        message:
          "Try Yummi for curated meals and grocery automation. Use my link to get bonus uses: https://yummi.app/referral",
      });
    } catch (error) {
      Alert.alert("Unable to share", "Please try again.");
    }
  }, []);

  const handleOpenPastOrders = useCallback(() => {
    setActivePastOrder(null);
    setScreen("pastOrders");
  }, []);

  const handleToggleHomeMealDislike = useCallback(
    (mealId) => {
      if (!mealId) {
        return;
      }
      setHomeMealDislikedIds((prev) => {
        const next = { ...(prev || {}) };
        const isCurrentlyDisliked = Boolean(next[mealId]);
        const shouldEnable = !isCurrentlyDisliked;
        if (shouldEnable) {
          next[mealId] = true;
        } else {
          delete next[mealId];
        }
        if (shouldEnable) {
          setSelectedHomeMealIds((prevSelected) => {
            if (prevSelected?.[mealId]) {
              const updated = { ...(prevSelected || {}) };
              delete updated[mealId];
              return updated;
            }
            return prevSelected;
          });
        }
        return next;
      });
    },
    []
  );

  const handleToggleHomeMealSelection = useCallback(
    (mealId) => {
      if (!mealId) {
        return;
      }
      setSelectedHomeMealIds((prev) => {
        const next = { ...(prev || {}) };
        const isCurrentlySelected = Boolean(next[mealId]);
        const shouldEnable = !isCurrentlySelected;
        if (shouldEnable) {
          next[mealId] = true;
        } else {
          delete next[mealId];
        }
        if (shouldEnable) {
          setHomeMealDislikedIds((prevDisliked) => {
            if (prevDisliked?.[mealId]) {
              const updated = { ...(prevDisliked || {}) };
              delete updated[mealId];
              return updated;
            }
            return prevDisliked;
          });
        }
        return next;
      });
    },
    []
  );

  const handleIngredientQuantityDecrease = useCallback((ingredientId) => {
    if (!ingredientId) {
      return;
    }
    setIngredientQuantities((prev) => {
      const currentValue = prev?.[ingredientId];
      const numericValue =
        typeof currentValue === "number" && Number.isFinite(currentValue)
          ? currentValue
          : 0;
      const nextValue = Math.max(0, numericValue - 1);
      if (nextValue === numericValue) {
        return prev;
      }
      return {
        ...prev,
        [ingredientId]: nextValue,
      };
    });
  }, []);

  const handleIngredientQuantityIncrease = useCallback((ingredientId) => {
    if (!ingredientId) {
      return;
    }
    setIngredientQuantities((prev) => {
      const currentValue = prev?.[ingredientId];
      const numericValue =
        typeof currentValue === "number" && Number.isFinite(currentValue)
          ? currentValue
          : 0;
      const nextValue = numericValue + 1;
      if (nextValue === numericValue) {
        return prev;
      }
      return {
        ...prev,
        [ingredientId]: nextValue,
      };
    });
  }, []);

  const formatIngredientQuantity = useCallback((value) => {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "0";
    }
    if (Math.abs(value - Math.round(value)) < 0.01) {
      return String(Math.round(value));
    }
    const formatted = value.toFixed(1);
    return formatted.replace(/\.0+$/, "");
  }, []);

  const getIngredientQuantityValue = useCallback(
    (ingredient) => {
      if (!ingredient) {
        return 0;
      }
      const storedQuantity = ingredientQuantities?.[ingredient.id];
      if (typeof storedQuantity === "number" && Number.isFinite(storedQuantity)) {
        return storedQuantity;
      }
      if (
        typeof ingredient.defaultQuantity === "number" &&
        Number.isFinite(ingredient.defaultQuantity)
      ) {
        return ingredient.defaultQuantity;
      }
      if (
        typeof ingredient.requiredQuantity === "number" &&
        Number.isFinite(ingredient.requiredQuantity)
      ) {
        return ingredient.requiredQuantity;
      }
      return 0;
    },
    [ingredientQuantities]
  );

  const getIngredientTrackingId = useCallback((ingredient, fallbackIndex = 0) => {
    if (!ingredient || typeof ingredient !== "object") {
      return `ingredient-${fallbackIndex}`;
    }
    return (
      ingredient.id ??
      ingredient.groupKey ??
      ingredient.text ??
      ingredient.productId ??
      ingredient.productName ??
      ingredient.name ??
      `ingredient-${fallbackIndex}`
    );
  }, []);

  const getIngredientImageUrl = useCallback((ingredient) => {
    const preferredProduct = pickPreferredShoppingListProduct(ingredient);
    return preferredProduct?.imageUrl ?? null;
  }, []);

  const getPastOrderQuantity = useCallback((item) => {
    if (!item) {
      return 0;
    }
    if (typeof item.qty === "number" && Number.isFinite(item.qty)) {
      return item.qty;
    }
    if (typeof item.requiredQuantity === "number" && Number.isFinite(item.requiredQuantity)) {
      return item.requiredQuantity;
    }
    if (typeof item.defaultQuantity === "number" && Number.isFinite(item.defaultQuantity)) {
      return item.defaultQuantity;
    }
    return 0;
  }, []);

  const buildShoppingListEntries = useCallback(
    (items, resolveQuantity) => {
      if (!Array.isArray(items)) {
        return [];
      }
      const entries = [];
      items.forEach((ingredient, index) => {
        if (!ingredient) {
          return;
        }
        const preferredProduct = pickPreferredShoppingListProduct(ingredient);
        const displayName =
          preferredProduct?.name ??
          ingredient.productName ??
          ingredient.text ??
          ingredient.groupKey ??
          "Ingredient";
        const imageUrl = preferredProduct?.imageUrl ?? null;
        const quantityValue =
          typeof resolveQuantity === "function"
            ? resolveQuantity(ingredient)
            : 0;
        const trackingId = getIngredientTrackingId(
          ingredient,
          `built-${index}-${ingredient?.id ?? ""}`
        );
        entries.push({
          id: trackingId,
          displayName,
          imageUrl,
          displayQuantity: formatIngredientQuantity(quantityValue),
        });
      });
      return entries.sort((a, b) =>
        a.displayName.localeCompare(b.displayName, undefined, { sensitivity: "base" })
      );
    },
    [formatIngredientQuantity, getIngredientTrackingId]
  );

  const shoppingListDisplayItems = useMemo(
    () =>
      buildShoppingListEntries(shoppingListItems, (ingredient) =>
        getIngredientQuantityValue(ingredient)
      ),
    [buildShoppingListEntries, getIngredientQuantityValue, shoppingListItems]
  );

  const pastOrderShoppingListDisplayItems = useMemo(() => {
    if (!activePastOrderShoppingList) {
      return [];
    }
    return buildShoppingListEntries(
      activePastOrderShoppingList.shoppingListItems ?? [],
      getPastOrderQuantity
    );
  }, [activePastOrderShoppingList, buildShoppingListEntries, getPastOrderQuantity]);

  const incrementImageReloadCounter = useCallback((itemId) => {
    if (!itemId) {
      return;
    }
    setImageReloadCounters((prev) => ({
      ...prev,
      [itemId]: (prev[itemId] ?? 0) + 1,
    }));
  }, []);

  const scheduleImageReload = useCallback(
    (itemId) => {
      if (!itemId) {
        return;
      }
      const pending = imageRetryTimeouts.current[itemId];
      if (pending) {
        clearTimeout(pending);
      }
      imageRetryTimeouts.current[itemId] = setTimeout(() => {
        incrementImageReloadCounter(itemId);
        delete imageRetryTimeouts.current[itemId];
      }, 2200);
    },
    [incrementImageReloadCounter]
  );

  const clearPrefetchTimeoutsFor = useCallback((category) => {
    if (!category) {
      return;
    }
    const prefix = `${category}-`;
    Object.keys(imagePrefetchTimeouts.current).forEach((key) => {
      if (key.startsWith(prefix)) {
        clearTimeout(imagePrefetchTimeouts.current[key]);
        delete imagePrefetchTimeouts.current[key];
      }
    });
  }, []);

  const handleToggleShoppingListItem = useCallback((itemId) => {
    if (!itemId) {
      return;
    }
    setCheckedShoppingListItems((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    clearPrefetchTimeoutsFor("shoppingList");
    shoppingListDisplayItems.forEach((item, index) => {
      if (!item?.imageUrl || prefetchedImageUrls.current.has(item.imageUrl)) {
        return;
      }
      const delay = Math.min(2200, index * 150);
      const timeoutKey = `shoppingList-${item.id}`;
      const timeoutId = setTimeout(() => {
        Image.prefetch(item.imageUrl)
          .then(() => {
            prefetchedImageUrls.current.add(item.imageUrl);
          })
          .catch(() => {
            scheduleImageReload(item.id);
          })
          .finally(() => {
            delete imagePrefetchTimeouts.current[timeoutKey];
          });
      }, delay);
      imagePrefetchTimeouts.current[timeoutKey] = timeoutId;
    });
    return () => {
      clearPrefetchTimeoutsFor("shoppingList");
    };
  }, [clearPrefetchTimeoutsFor, scheduleImageReload, shoppingListDisplayItems]);

  useEffect(() => {
    clearPrefetchTimeoutsFor("ingredients");
    const ingredients = [...stapleIngredients, ...primaryIngredients];
    ingredients.forEach((ingredient, index) => {
      const imageUrl = getIngredientImageUrl(ingredient);
      if (!imageUrl || prefetchedImageUrls.current.has(imageUrl)) {
        return;
      }
      const trackingId = getIngredientTrackingId(ingredient, index);
      const timeoutKey = `ingredients-${trackingId}`;
      const delay = Math.min(2200, index * 120);
      const timeoutId = setTimeout(() => {
        Image.prefetch(imageUrl)
          .then(() => {
            prefetchedImageUrls.current.add(imageUrl);
          })
          .catch(() => {
            scheduleImageReload(trackingId);
          })
          .finally(() => {
            delete imagePrefetchTimeouts.current[timeoutKey];
          });
      }, delay);
      imagePrefetchTimeouts.current[timeoutKey] = timeoutId;
    });
    return () => {
      clearPrefetchTimeoutsFor("ingredients");
    };
  }, [
    clearPrefetchTimeoutsFor,
    getIngredientImageUrl,
    getIngredientTrackingId,
    scheduleImageReload,
    stapleIngredients,
    primaryIngredients,
  ]);

  useEffect(() => {
    if (screen !== "pastOrderShoppingList") {
      return undefined;
    }
    clearPrefetchTimeoutsFor("pastOrderList");
    pastOrderShoppingListDisplayItems.forEach((item, index) => {
      if (!item?.imageUrl || prefetchedImageUrls.current.has(item.imageUrl)) {
        return;
      }
      const timeoutKey = `pastOrderList-${item.id}`;
      const delay = Math.min(2200, index * 120);
      const timeoutId = setTimeout(() => {
        Image.prefetch(item.imageUrl)
          .then(() => {
            prefetchedImageUrls.current.add(item.imageUrl);
          })
          .catch(() => {
            scheduleImageReload(item.id);
          })
          .finally(() => {
            delete imagePrefetchTimeouts.current[timeoutKey];
          });
      }, delay);
      imagePrefetchTimeouts.current[timeoutKey] = timeoutId;
    });
    return () => {
      clearPrefetchTimeoutsFor("pastOrderList");
    };
  }, [
    clearPrefetchTimeoutsFor,
    pastOrderShoppingListDisplayItems,
    scheduleImageReload,
    screen,
  ]);

  const getIngredientUnitPriceMinor = useCallback((ingredient) => {
    if (!ingredient) {
      return null;
    }
    const serverMinor =
      ingredient.unitPriceMinor ??
      ingredient.unit_price_minor ??
      ingredient.unit_priceMinor ??
      null;
    if (typeof serverMinor === "number" && Number.isFinite(serverMinor)) {
      return Math.max(0, Math.round(serverMinor));
    }
    if (typeof serverMinor === "string" && serverMinor.trim() !== "") {
      const parsed = Number(serverMinor);
      if (!Number.isNaN(parsed)) {
        return Math.max(0, Math.round(parsed));
      }
    }
    const serverUnit =
      ingredient.unitPrice ??
      ingredient.unit_price ??
      ingredient.unit_price_value ??
      null;
    const normalizedServerUnit = normalizePriceToMinorUnits(serverUnit);
    if (normalizedServerUnit != null) {
      return normalizedServerUnit;
    }
    const linkedProducts = Array.isArray(ingredient.linkedProducts)
      ? ingredient.linkedProducts
      : Array.isArray(ingredient.linked_products)
      ? ingredient.linked_products
      : [];
    const extractProductPrice = (product) => {
      if (!product) {
        return null;
      }
      const salePriceValue =
        product.salePrice ??
        product.sale_price ??
        product.price ??
        (product.metadata ? product.metadata.salePrice : null);
      return normalizePriceToMinorUnits(salePriceValue);
    };
    const pricedProduct = linkedProducts.find(
      (product) => extractProductPrice(product) != null
    );
    if (pricedProduct) {
      return extractProductPrice(pricedProduct);
    }
    if (ingredient?.salePrice != null || ingredient?.sale_price != null) {
      return (
        normalizePriceToMinorUnits(ingredient.salePrice) ??
        normalizePriceToMinorUnits(ingredient.sale_price)
      );
    }
    return null;
  }, []);

  const shoppingListPricing = useMemo(() => {
    const priceMap = {};
    let basketTotalMinor = 0;
    let hasAnyPrice = false;
    shoppingListItems.forEach((ingredient) => {
      if (!ingredient?.id) {
        return;
      }
      const quantity = getIngredientQuantityValue(ingredient);
      const numericQuantity =
        typeof quantity === "number" && Number.isFinite(quantity) ? Math.max(0, quantity) : 0;
      const unitPriceMinor = getIngredientUnitPriceMinor(ingredient);
      let lineTotalMinor = null;
      if (unitPriceMinor != null) {
        hasAnyPrice = true;
        if (numericQuantity > 0) {
          lineTotalMinor = Math.round(unitPriceMinor * numericQuantity);
          if (lineTotalMinor > 0) {
            basketTotalMinor += lineTotalMinor;
          }
        } else {
          lineTotalMinor = 0;
        }
      }
      priceMap[ingredient.id] = {
        unitPriceMinor,
        lineTotalMinor,
      };
    });
    return {
      byIngredientId: priceMap,
      basketTotalMinor,
      hasAnyPrice,
    };
  }, [getIngredientQuantityValue, getIngredientUnitPriceMinor, shoppingListItems]);

  const buildShoppingListCartItems = useCallback(() => {
    const readyItems = [];
    const skipped = [];
    shoppingListItems.forEach((ingredient) => {
      if (!ingredient) {
        return;
      }
      const quantity = getIngredientQuantityValue(ingredient);
      if (!(typeof quantity === "number" && Number.isFinite(quantity) && quantity > 0)) {
        return;
      }
      const normalizedQuantity = Math.max(1, Math.ceil(quantity));
      const selection = pickPreferredShoppingListProduct(ingredient);
      if (!selection || (!selection.productId && !selection.catalogRefId)) {
        const displayName =
          selection?.name ??
          ingredient.productName ??
          ingredient.text ??
          ingredient.groupKey ??
          "Ingredient";
        skipped.push({
          id: ingredient.id ?? ingredient.groupKey ?? ingredient.text ?? null,
          label: displayName,
        });
        return;
      }
      const detailUrl =
        selection.detailUrl ??
        buildProductDetailUrl(selection.productId, selection.catalogRefId);
      readyItems.push({
        key:
          ingredient.id ??
          ingredient.groupKey ??
          selection.productId ??
          selection.catalogRefId ??
          `ingredient-${readyItems.length + 1}`,
        title:
          selection.name ??
          ingredient.productName ??
          ingredient.text ??
          "Ingredient",
        productId: selection.productId,
        catalogRefId: selection.catalogRefId ?? selection.productId,
        qty: normalizedQuantity,
        url: detailUrl,
        detailUrl,
        itemListName: "Yummi Shopping List",
        metadata: {
          ingredientId: ingredient.id ?? null,
          groupKey: ingredient.groupKey ?? null,
          classification: ingredient.classification ?? null,
          requestedQuantity: quantity,
          recommendedQuantity: ingredient.requiredQuantity ?? null,
          notes: ingredient.notes ?? null,
          productName: selection.name ?? null,
          productPackagesSuggested: selection.packages ?? null,
        },
      });
    });
    return { items: readyItems, skipped };
  }, [getIngredientQuantityValue, shoppingListItems]);

  const renderIngredientRow = useCallback(
    (ingredient, fallbackIndex = 0) => {
      if (!ingredient) {
        return null;
      }
      const trackingId = getIngredientTrackingId(ingredient, fallbackIndex);
      const preferredProduct = pickPreferredShoppingListProduct(ingredient);
      const productImageUrl = getIngredientImageUrl(ingredient);
      const displayName =
        preferredProduct?.name ??
        ingredient.productName ??
        ingredient.text ??
        ingredient.groupKey ??
        "Ingredient";
      const placeholderInitial = (displayName ?? "?")
        .trim()
        .charAt(0)
        .toUpperCase();
      const numericQuantity = getIngredientQuantityValue(ingredient);
      const displayQuantity = formatIngredientQuantity(numericQuantity);
      const needsManualProductSelection = Boolean(
        ingredient.needsManualProductSelection ?? ingredient.needs_manual_product_selection
      );
      const manualNote =
        ingredient.notes ??
        "We couldn't find a Woolworths product to cover this ingredient yet. Please pick one manually.";
      const disableDecrease = needsManualProductSelection || numericQuantity <= 0;
      const disableIncrease = needsManualProductSelection;
      const priceDetails =
        shoppingListPricing.byIngredientId?.[ingredient.id] ?? {};
      const unitPriceMinor =
        typeof priceDetails.unitPriceMinor === "number"
          ? priceDetails.unitPriceMinor
          : null;
      const lineTotalMinor =
        typeof priceDetails.lineTotalMinor === "number" &&
        priceDetails.lineTotalMinor > 0
          ? priceDetails.lineTotalMinor
          : null;
      const imageReloadKey = imageReloadCounters[trackingId] ?? 0;
      return (
        <View
          key={ingredient.id}
          style={[
            styles.ingredientsListItem,
            needsManualProductSelection && styles.ingredientsListItemManual,
          ]}
        >
          <View style={styles.ingredientsListItemRow}>
            <View style={styles.ingredientsItemImageWrapper}>
              {productImageUrl ? (
                <Image
                  key={`ingredient-image-${trackingId}-${imageReloadKey}`}
                  source={{ uri: productImageUrl, cache: "reload" }}
                  style={styles.ingredientsItemImage}
                  onError={() => scheduleImageReload(trackingId)}
                />
              ) : (
                <View style={styles.ingredientsItemImagePlaceholder}>
                  <Text style={styles.ingredientsItemImagePlaceholderText}>
                    {placeholderInitial || "?"}
                  </Text>
                </View>
              )}
            </View>
            <View style={styles.ingredientsItemBody}>
              <View style={styles.ingredientsItemHeader}>
                <Text style={styles.ingredientsItemText}>{displayName}</Text>
                {lineTotalMinor != null ? (
                  <Text style={styles.ingredientsItemLineTotal}>
                    {formatCurrency(lineTotalMinor)}
                  </Text>
                ) : null}
              </View>
              {unitPriceMinor != null ? (
                <Text style={styles.ingredientsItemUnitPrice}>
                  {`${formatCurrency(unitPriceMinor)} each`}
                </Text>
              ) : null}
              {needsManualProductSelection ? (
                <View style={styles.ingredientsManualNotice}>
                  <Text style={styles.ingredientsManualNoticeText}>{manualNote}</Text>
                </View>
              ) : null}
              <View style={styles.ingredientsQuantityRow}>
                <TouchableOpacity
                  style={[
                    styles.ingredientsQuantityButton,
                    disableDecrease && styles.ingredientsQuantityButtonDisabled,
                  ]}
                  onPress={() => handleIngredientQuantityDecrease(ingredient.id)}
                  accessibilityRole="button"
                  accessibilityLabel={`Decrease quantity for ${displayName}`}
                  disabled={disableDecrease}
                >
                  <Text
                    style={[
                      styles.ingredientsQuantityButtonText,
                      disableDecrease &&
                        styles.ingredientsQuantityButtonTextDisabled,
                    ]}
                  >
                    -
                  </Text>
                </TouchableOpacity>
                <View style={styles.ingredientsQuantityValue}>
                  <Text style={styles.ingredientsQuantityValueText}>
                    {displayQuantity}
                  </Text>
                </View>
                <TouchableOpacity
                  style={[
                    styles.ingredientsQuantityButton,
                    disableIncrease && styles.ingredientsQuantityButtonDisabled,
                  ]}
                  onPress={() => handleIngredientQuantityIncrease(ingredient.id)}
                  accessibilityRole="button"
                  accessibilityLabel={`Increase quantity for ${displayName}`}
                  disabled={disableIncrease}
                >
                  <Text
                    style={[
                      styles.ingredientsQuantityButtonText,
                      disableIncrease && styles.ingredientsQuantityButtonTextDisabled,
                    ]}
                  >
                    +
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </View>
      );
    },
    [
      formatIngredientQuantity,
      getIngredientQuantityValue,
      getIngredientImageUrl,
      getIngredientTrackingId,
      handleIngredientQuantityDecrease,
      handleIngredientQuantityIncrease,
      scheduleImageReload,
      shoppingListPricing,
      imageReloadCounters,
    ]
  );

  const toggleMealMenu = useCallback(() => {
    setIsMealMenuOpen((prev) => !prev);
  }, []);

  const closeMealMenu = useCallback(() => {
    setIsMealMenuOpen(false);
  }, []);

  const handleOpenRunnerSurface = useCallback(() => {
    setHomeSurface("runner");
    setScreen("home");
  }, []);

  const handleReturnToMealHome = useCallback(() => {
    setHomeSurface("meal");
    setScreen("home");
  }, []);

  const handleIngredientsBackToHome = useCallback(() => {
    setIsMealMenuOpen(false);
    setScreen("home");
  }, []);

  const handleReturnToIngredients = useCallback(() => {
    setIsMealMenuOpen(false);
    setScreen("ingredients");
  }, []);

  const handleReturnToWelcome = useCallback(() => {
    setIsWelcomeComplete(false);
    setIsOnboardingActive(false);
    setIsMealMenuOpen(false);
    setHomeSurface("meal");
    setScreen("home");
    setIsSorryToHearScreenVisible(false);
  }, []);

  const handleConfirmReturnHome = useCallback(() => {
    showConfirmationDialog(
      "returnHomeFromShoppingList",
      "This will take you back to the home screen.\nYou can always get your shopping list in past orders."
    );
  }, [showConfirmationDialog]);

  const recordPastOrder = useCallback(
    (mealsSnapshot, options = {}) => {
      if (!Array.isArray(mealsSnapshot) || mealsSnapshot.length === 0) {
        return;
      }
      const entries = mealsSnapshot
        .filter((meal) => meal && meal.mealId)
        .map((meal) => {
          try {
            return JSON.parse(JSON.stringify(meal));
          } catch (error) {
            return { ...meal };
          }
        })
        .filter(Boolean);
      if (!entries.length) {
        return;
      }
      const shoppingListSnapshot = Array.isArray(options.shoppingListItems)
        ? options.shoppingListItems
            .map((item) => {
              if (!item) {
                return null;
              }
              try {
                return JSON.parse(JSON.stringify(item));
              } catch (error) {
                return { ...item };
              }
            })
            .filter(Boolean)
        : [];
      const entry = {
        orderId: `past-${Date.now()}`,
        createdAt: new Date().toISOString(),
        meals: entries,
        shoppingListItems: shoppingListSnapshot,
      };
      setPastOrders((prev) => {
        const next = [entry, ...(prev ?? [])];
        if (next.length > 20) {
          next.length = 20;
        }
        persistPastOrders(next);
        return next;
      });
    },
    [persistPastOrders]
  );

  const handleClosePastOrders = useCallback(() => {
    setActivePastOrder(null);
    handleReturnToWelcome();
  }, [handleReturnToWelcome]);

  const getPastOrderLabel = useCallback((order) => {
    if (!order) {
      return "Shopping List";
    }
    const orderDate = order.createdAt ? new Date(order.createdAt) : null;
    if (orderDate && !Number.isNaN(orderDate.getTime())) {
      const dayLabel = orderDate.toLocaleDateString(undefined, { weekday: "short" });
      const dateLabel = orderDate.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      });
      return `Shopping List · ${dayLabel} ${dateLabel}`;
    }
    return "Shopping List";
  }, []);

  const handleShowPastOrderShoppingList = useCallback(
    (order) => {
      if (!order) {
        return;
      }
      const items = Array.isArray(order.shoppingListItems)
        ? order.shoppingListItems
        : [];
      if (!items.length) {
        Alert.alert(
          "Shopping list unavailable",
          "This past order does not have a saved shopping list."
        );
        return;
      }
      setActivePastOrderShoppingList(order);
      setScreen("pastOrderShoppingList");
    },
    []
  );

  const handleClosePastOrderShoppingList = useCallback(() => {
    setActivePastOrderShoppingList(null);
    setScreen("pastOrders");
  }, []);

  const deleteOrderRef = useRef(null);
  const handleDeletePastOrder = useCallback(
    (order) => {
      if (!order?.orderId) {
        return;
      }
      deleteOrderRef.current = order;
      showConfirmationDialog("deletePastOrder", "delete past order");
    },
    [showConfirmationDialog]
  );

  const handleOpenPastOrderDetails = useCallback((order) => {
    if (!order) {
      return;
    }
    setSelectedHomeMealIds({});
    setHomeMealDislikedIds({});
    setActivePastOrder(order);
    setScreen("pastOrderDetails");
  }, []);

  const handleClosePastOrderDetails = useCallback(() => {
    setActivePastOrder(null);
    setScreen("pastOrders");
  }, []);

  const homeModalMeal = homeMealModal.meal;
  const homeModalPrepSteps =
    homeMealModal.visible && homeModalMeal ? getMealPrepSteps(homeModalMeal) : [];
  const homeModalCookSteps =
    homeMealModal.visible && homeModalMeal ? getMealCookSteps(homeModalMeal) : [];
  const homeModalIngredients =
    homeMealModal.visible && homeModalMeal
      ? getMealDetailIngredients(homeModalMeal)
      : [];

  const homeMealDetailModal =
    homeMealModal.visible && homeModalMeal ? (
      <View style={styles.mealDetailModalContainer} pointerEvents="box-none">
        <TouchableWithoutFeedback
          onPress={() => setHomeMealModal({ visible: false, meal: null })}
        >
          <View style={styles.mealDetailBackdrop} />
        </TouchableWithoutFeedback>
        <View style={styles.mealDetailCard}>
          <ScrollView
            style={styles.mealDetailScroll}
            contentContainerStyle={styles.mealDetailContent}
          >
            <Text style={styles.mealDetailTitle}>
              {homeModalMeal.name ?? "Meal"}
            </Text>
            {homeModalMeal.description ? (
              <Text style={styles.mealDetailDescription}>
                {homeModalMeal.description}
              </Text>
            ) : null}
            {homeModalPrepSteps.length > 0 ? (
              <View style={styles.mealDetailSection}>
                <Text style={styles.mealDetailSectionTitle}>Prep Steps</Text>
                {homeModalPrepSteps.map((step, idx) => (
                  <Text key={`prep-${idx}`} style={styles.mealDetailSectionItem}>
                    {idx + 1}. {step}
                  </Text>
                ))}
              </View>
            ) : null}
            {homeModalCookSteps.length > 0 ? (
              <View style={styles.mealDetailSection}>
                <Text style={styles.mealDetailSectionTitle}>Cooking Steps</Text>
                {homeModalCookSteps.map((step, idx) => (
                  <Text key={`cook-${idx}`} style={styles.mealDetailSectionItem}>
                    {idx + 1}. {step}
                  </Text>
                ))}
              </View>
            ) : null}
            {homeModalIngredients.length > 0 ? (
              <View style={styles.mealDetailSection}>
                <Text style={styles.mealDetailSectionTitle}>Ingredients</Text>
                {homeModalIngredients.map((ingredient, idx) => {
                  const label = formatMealIngredientDetailText(ingredient, idx);
                  return (
                    <Text
                      key={`detail-ingredient-${idx}`}
                      style={styles.mealDetailSectionItem}
                    >
                      • {label}
                    </Text>
                  );
                })}
              </View>
            ) : null}
          </ScrollView>
          <TouchableOpacity
            style={styles.mealDetailCloseButton}
            onPress={() => setHomeMealModal({ visible: false, meal: null })}
          >
            <Text style={styles.mealDetailCloseText}>×</Text>
          </TouchableOpacity>
        </View>
      </View>
    ) : null;

  useEffect(() => {
    setHasFetchedRemotePreferences(false);
    setLastPreferencesSyncedAt(null);
    setPreferencesSyncError(null);
    setIsOnboardingActive(false);
    setHomeSurface("meal");
    setIsMealMenuOpen(false);
    applyHomeRecommendedMeals([]);
    preferenceSyncHashRef.current = null;
    setExplorationState("idle");
    setExplorationMeals([]);
    setExplorationNotes([]);
    setExplorationSessionId(null);
    setExplorationError(null);
    setExplorationReactions({});
    setHasSeenExplorationResults(false);
    toggleDefaultsInitializedRef.current = {};
    setShoppingListItems([]);
    setShoppingListStatus("idle");
    setShoppingListError(null);
    setIngredientQuantities({});
  }, [applyHomeRecommendedMeals, userId]);

  useEffect(() => {
    hydrateShoppingList();
  }, [hydrateShoppingList, userId]);

  useEffect(() => {
    if (screen !== "home" || !isMealHomeSurface) {
      setIsMealMenuOpen(false);
    }
  }, [isMealHomeSurface, screen]);

  useEffect(() => {
    if (screen !== "home" || !isMealHomeSurface) {
      return;
    }
    refreshLatestRecommendations({ skipIfPending: true });
  }, [screen, isMealHomeSurface, refreshLatestRecommendations]);

  useEffect(() => {
    let isActive = true;
    const hydratePreferences = async () => {
      try {
        const [storedState, storedCompletion] = await Promise.all([
          SecureStore.getItemAsync(PREFERENCES_STATE_STORAGE_KEY),
          SecureStore.getItemAsync(PREFERENCES_COMPLETED_STORAGE_KEY),
        ]);
        if (!isActive) {
          return;
        }
        if (storedState) {
          try {
            const parsedState = JSON.parse(storedState);
            setPreferenceResponses(applyPreferenceSmartLogic(parsedState));
          } catch (error) {
            console.warn("Unable to parse stored preference state", error);
          }
        }
        if (storedCompletion === "true") {
          setIsPreferencesFlowComplete(true);
        }
      } catch (error) {
        console.warn("Failed to load preference flow state", error);
      } finally {
        if (isActive) {
          setIsPreferenceStateReady(true);
        }
      }
    };
    hydratePreferences();
    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    if (!isPreferenceStateReady) {
      return;
    }
    const persistPreferences = async () => {
      try {
        await SecureStore.setItemAsync(
          PREFERENCES_STATE_STORAGE_KEY,
          JSON.stringify(preferenceResponses)
        );
      } catch (error) {
        console.warn("Unable to persist preference selections", error);
      }
    };
    persistPreferences();
  }, [preferenceResponses, isPreferenceStateReady]);

  useEffect(() => {
    if (!isPreferenceStateReady) {
      return;
    }
    const persistCompletion = async () => {
      try {
        if (isPreferencesFlowComplete) {
          await SecureStore.setItemAsync(
            PREFERENCES_COMPLETED_STORAGE_KEY,
            "true"
          );
        } else {
          await SecureStore.deleteItemAsync(
            PREFERENCES_COMPLETED_STORAGE_KEY
          );
        }
      } catch (error) {
        console.warn("Unable to persist preference completion flag", error);
      }
    };
    persistCompletion();
  }, [isPreferenceStateReady, isPreferencesFlowComplete]);

  useEffect(() => {
    if (
      !isPreferenceStateReady ||
      !PREFERENCES_API_ENDPOINT ||
      hasFetchedRemotePreferences ||
      !userId
    ) {
      return;
    }
    let isCancelled = false;
    const fetchPreferences = async () => {
      try {
        const headers = await buildAuthHeaders();
        const response = await fetch(PREFERENCES_API_ENDPOINT, {
          headers,
        });
        if (!response.ok) {
          if (response.status === 404) {
            return;
          }
          throw new Error(`Preference fetch failed (${response.status})`);
        }
        const payload = await response.json();
        if (isCancelled) {
          return;
        }
        const remoteResponses = payload?.responses ?? {};
        const remoteHash = JSON.stringify(remoteResponses ?? {});
        const localHash = JSON.stringify(preferenceResponses ?? {});
        const hasRemoteSelections =
          remoteResponses && Object.keys(remoteResponses).length > 0;
        const hasLocalSelections = hasMeaningfulPreferenceSelections(
          preferenceResponses
        );
        if (hasRemoteSelections && !hasLocalSelections) {
          setPreferenceResponses(applyPreferenceSmartLogic(remoteResponses));
          preferenceSyncHashRef.current = remoteHash;
        } else if (remoteHash && remoteHash === localHash) {
          preferenceSyncHashRef.current = remoteHash;
        }
        if (payload?.completionStage === "complete") {
          setIsPreferencesFlowComplete(true);
          setHasAcknowledgedPreferenceComplete(true);
        }
        if (payload?.lastSyncedAt) {
          const parsed = parseServerDate(payload.lastSyncedAt);
          if (parsed) {
            setLastPreferencesSyncedAt(parsed);
          }
        }
        const latestMealsSource =
          Array.isArray(payload?.latestRecommendations?.meals)
            ? payload.latestRecommendations.meals
            : Array.isArray(payload?.latestRecommendationMeals)
            ? payload.latestRecommendationMeals
            : [];
        if (latestMealsSource.length > 0) {
          const latestGeneratedAt =
            payload?.latestRecommendations?.generatedAt ??
            payload?.latestRecommendationsGeneratedAt ??
            null;
          applyHomeRecommendedMeals(latestMealsSource, {
            generatedAt: latestGeneratedAt,
          });
        } else {
          applyHomeRecommendedMeals([]);
        }
      } catch (error) {
        if (__DEV__) {
          console.warn("Unable to fetch saved preferences", error);
        }
      } finally {
        if (!isCancelled) {
          setHasFetchedRemotePreferences(true);
        }
      }
    };
    fetchPreferences();
    return () => {
      isCancelled = true;
    };
  }, [
    applyHomeRecommendedMeals,
    buildAuthHeaders,
    hasFetchedRemotePreferences,
    isPreferenceStateReady,
    preferenceResponses,
    userId,
  ]);

  useEffect(() => {
    if (!isPreferenceStateReady) {
      return;
    }
    const shouldWaitForRemote =
      Boolean(PREFERENCES_API_ENDPOINT && userId) &&
      !hasFetchedRemotePreferences;
    if (shouldWaitForRemote) {
      return;
    }
    const pendingCategoryIds = Object.keys(TOGGLE_CATEGORY_BEHAVIOR).filter(
      (categoryId) => !toggleDefaultsInitializedRef.current[categoryId]
    );
    if (pendingCategoryIds.length === 0) {
      return;
    }
    const categoriesNeedingDefault = pendingCategoryIds.filter(
      (categoryId) => {
        const selections = preferenceResponses?.[categoryId] ?? {};
        if (selections && Object.keys(selections).length > 0) {
          toggleDefaultsInitializedRef.current[categoryId] = true;
          return false;
        }
        return true;
      }
    );
    if (categoriesNeedingDefault.length === 0) {
      return;
    }
    setPreferenceResponses((prev) => {
      let hasChanges = false;
      const next = { ...prev };
      categoriesNeedingDefault.forEach((categoryId) => {
        const prevSelections = prev?.[categoryId] ?? {};
        if (prevSelections && Object.keys(prevSelections).length > 0) {
          toggleDefaultsInitializedRef.current[categoryId] = true;
          return;
        }
        const config = getToggleCategoryConfig(categoryId);
        if (!config) {
          toggleDefaultsInitializedRef.current[categoryId] = true;
          return;
        }
        next[categoryId] = {
          [config.defaultTagId]: config.defaultState,
        };
        toggleDefaultsInitializedRef.current[categoryId] = true;
        hasChanges = true;
      });
      if (!hasChanges) {
        return prev;
      }
      return applyPreferenceSmartLogic(next);
    });
  }, [
    hasFetchedRemotePreferences,
    isPreferenceStateReady,
    preferenceResponses,
    userId,
    PREFERENCES_API_ENDPOINT,
  ]);

  useEffect(() => {
    if (
      !isPreferenceStateReady ||
      !PREFERENCES_API_ENDPOINT ||
      !isPreferencesFlowComplete
    ) {
      return;
    }
    const responsesSnapshot = preferenceResponses ?? {};
    const serialized = JSON.stringify(responsesSnapshot ?? {});
    if (!serialized) {
      return;
    }
    if (preferenceSyncHashRef.current === serialized) {
      return;
    }
    let isCancelled = false;
    const syncPreferences = async () => {
      setIsPreferenceSyncing(true);
      setPreferencesSyncError(null);
      try {
        const headers = await buildAuthHeaders({
          "Content-Type": "application/json",
        });
        const response = await fetch(PREFERENCES_API_ENDPOINT, {
          method: "PUT",
          headers,
          body: JSON.stringify({
            tagsVersion: PREFERENCES_TAGS_VERSION,
            responses: responsesSnapshot,
            completionStage: "complete",
            completedAt: new Date().toISOString(),
          }),
        });
        if (!response.ok) {
          throw new Error(`Preference sync failed (${response.status})`);
        }
        const payload = await response.json();
        if (isCancelled) {
          return;
        }
        preferenceSyncHashRef.current = serialized;
        setPreferencesSyncError(null);
        const parsed = parseServerDate(payload?.lastSyncedAt) ?? new Date();
        setLastPreferencesSyncedAt(parsed);
      } catch (error) {
        if (isCancelled) {
          return;
        }
        setPreferencesSyncError(error?.message ?? "Unable to sync preferences");
      } finally {
        if (!isCancelled) {
          setIsPreferenceSyncing(false);
        }
      }
    };
    syncPreferences();
    return () => {
      isCancelled = true;
    };
  }, [
    buildAuthHeaders,
    isPreferenceStateReady,
    isPreferencesFlowComplete,
    preferenceResponses,
  ]);

  useEffect(() => {
    if (activePreferenceIndex >= preferenceCategories.length) {
      setActivePreferenceIndex(
        preferenceCategories.length > 0
          ? preferenceCategories.length - 1
          : 0
      );
    }
  }, [activePreferenceIndex, preferenceCategories.length]);

  useEffect(() => {
    if (
      isPreferenceStateReady &&
      !isPreferencesFlowComplete &&
      preferenceCategories.length === 0
    ) {
      setIsPreferencesFlowComplete(true);
    }
  }, [
    isPreferenceStateReady,
    isPreferencesFlowComplete,
    preferenceCategories.length,
  ]);

  useEffect(() => {
    if (!isPreferencesFlowComplete) {
      setHasAcknowledgedPreferenceComplete(false);
      setHasSeenExplorationResults(false);
    }
  }, [isPreferencesFlowComplete]);

  useEffect(() => {
    if (
      !isOnboardingActive ||
      !isPreferenceStateReady ||
      !isPreferencesFlowComplete ||
      !hasAcknowledgedPreferenceComplete ||
      explorationState !== "idle" ||
      !EXPLORATION_API_ENDPOINT
    ) {
      return;
    }
    startExplorationRun();
  }, [
    explorationState,
    isOnboardingActive,
    isPreferenceStateReady,
    isPreferencesFlowComplete,
    hasAcknowledgedPreferenceComplete,
    startExplorationRun,
    EXPLORATION_API_ENDPOINT,
  ]);

const handlePreferenceSelection = useCallback(
  (categoryId, tagId, value) => {
    setPreferenceResponses((prev) => {
      const prevCategoryValues = prev[categoryId] ?? {};
      const currentValue = prevCategoryValues[tagId] ?? "neutral";
      const resolvedValue = resolvePreferenceSelectionValue(
        currentValue,
        value
      );
      const next = { ...prev };
      if (isSingleSelectPreferenceCategory(categoryId)) {
        if (resolvedValue === "neutral") {
          delete next[categoryId];
        } else {
          next[categoryId] = { [tagId]: resolvedValue };
        }
        return applyPreferenceSmartLogic(next);
      }
      const nextCategoryValues = { ...prevCategoryValues };
      if (resolvedValue === "neutral") {
        delete nextCategoryValues[tagId];
      } else {
        nextCategoryValues[tagId] = resolvedValue;
      }
      const toggleConfig = getToggleCategoryConfig(categoryId);
      if (toggleConfig) {
        if (
          tagId === toggleConfig.defaultTagId &&
          resolvedValue !== "neutral"
        ) {
          Object.keys(nextCategoryValues).forEach((key) => {
            if (key !== toggleConfig.defaultTagId) {
              delete nextCategoryValues[key];
            }
          });
        } else if (resolvedValue !== "neutral") {
          delete nextCategoryValues[toggleConfig.defaultTagId];
        }
      }
      if (Object.keys(nextCategoryValues).length === 0) {
        delete next[categoryId];
      } else {
        next[categoryId] = nextCategoryValues;
      }
      return applyPreferenceSmartLogic(next);
    });
  },
  []
);

  const handlePreferenceContinue = useCallback(() => {
    if (preferenceCategories.length === 0) {
      setIsPreferencesFlowComplete(true);
      return;
    }
    if (activePreferenceIndex >= preferenceCategories.length - 1) {
      setIsPreferencesFlowComplete(true);
      return;
    }
    setActivePreferenceIndex((prev) =>
      Math.min(prev + 1, preferenceCategories.length - 1)
    );
  }, [activePreferenceIndex, preferenceCategories.length]);

  const handleResetPreferencesFlow = useCallback(
    async (options = {}) => {
      const { returnToSorryScreen = false } = options;
      preferenceEntryContextRef.current = {
        screen,
        homeSurface,
        returnToSorryScreen,
      };
      homeMealsBackupRef.current = homeRecommendedMeals;
      setIsSorryToHearScreenVisible(false);
      setIsOnboardingActive(true);
      setHomeSurface("meal");
      setActivePreferenceIndex(0);
      setIsPreferencesFlowComplete(false);
      setHasAcknowledgedPreferenceComplete(false);
      setHasSeenExplorationResults(false);
      setExplorationState("idle");
      setExplorationMeals([]);
      setExplorationNotes([]);
      setExplorationSessionId(null);
      setExplorationError(null);
      setExplorationReactions({});
      applyHomeRecommendedMeals([]);
      preferenceSyncHashRef.current = null;
    },
    [applyHomeRecommendedMeals, homeRecommendedMeals, homeSurface, screen]
  );

  const restoreHomeMealsFromBackup = useCallback(() => {
    if (homeMealsBackupRef.current != null) {
      applyHomeRecommendedMeals(homeMealsBackupRef.current);
      homeMealsBackupRef.current = null;
    }
  }, [applyHomeRecommendedMeals]);

  const handleMealMenuReset = useCallback(() => {
    closeMealMenu();
    handleResetPreferencesFlow();
  }, [closeMealMenu, handleResetPreferencesFlow]);

  const handleSorryScreenUpdatePreferences = useCallback(() => {
    handleResetPreferencesFlow({ returnToSorryScreen: true });
  }, [handleResetPreferencesFlow]);

  const handlePreferenceCompleteBack = useCallback(() => {
    setIsMealMenuOpen(false);
    setIsPreferencesFlowComplete(false);
    setHasAcknowledgedPreferenceComplete(false);
    setHasSeenExplorationResults(false);
    setActivePreferenceIndex(
      preferenceCategories.length > 0 ? preferenceCategories.length - 1 : 0
    );
  }, [preferenceCategories.length]);

  const handlePreferenceBack = useCallback(() => {
    if (activePreferenceIndex > 0) {
      setActivePreferenceIndex((prev) => Math.max(prev - 1, 0));
      return;
    }
    const entryContext = preferenceEntryContextRef.current;
    setIsOnboardingActive(false);
    setIsMealMenuOpen(false);
    if (entryContext?.returnToSorryScreen) {
      setIsSorryToHearScreenVisible(true);
    } else {
      setIsSorryToHearScreenVisible(false);
    }
    if (entryContext?.screen) {
      setScreen(entryContext.screen);
    } else {
      setScreen("home");
    }
    if (entryContext?.screen === "home" && entryContext?.homeSurface) {
      setHomeSurface(entryContext.homeSurface);
    }
    setActivePreferenceIndex(0);
    preferenceEntryContextRef.current = null;
    if (!hasAcknowledgedPreferenceComplete) {
      restoreHomeMealsFromBackup();
    } else {
      homeMealsBackupRef.current = null;
    }
  }, [activePreferenceIndex, hasAcknowledgedPreferenceComplete, restoreHomeMealsFromBackup]);

  const handleConfirmPreferenceComplete = useCallback(() => {
    setHasAcknowledgedPreferenceComplete(true);
  }, []);

  const showConfirmationDialog = useCallback(
    (context, subtitle = "Use one free use") => {
    setConfirmationDialog({
      visible: true,
      context,
    });
    setConfirmationDialogSubtitle(subtitle);
  }, []);

  const handleCloseConfirmationDialog = useCallback(() => {
    setConfirmationDialog({
      visible: false,
      context: null,
    });
    shoppingListLearningIntentRef.current = false;
    shoppingListNextScreenRef.current = "ingredients";
  }, []);

  const handleConfirmDialog = useCallback(() => {
    const context = confirmationDialog.context;
    setConfirmationDialog({
      visible: false,
      context: null,
    });
    if (context === "shoppingList") {
      const triggerLearning = shoppingListLearningIntentRef.current;
      const nextScreen = shoppingListNextScreenRef.current || "ingredients";
      shoppingListLearningIntentRef.current = false;
      shoppingListNextScreenRef.current = "ingredients";
      handleBuildShoppingList({
        triggerLearning,
        nextScreen,
      });
    } else if (context === "newMeals") {
      handleConfirmPreferenceComplete();
    } else if (context === "woolworthsCart") {
      recordPastOrder(selectedHomeMeals, { shoppingListItems });
      handleSendShoppingListToCart();
    } else if (context === "deletePastOrder") {
      const order = deleteOrderRef.current;
      if (order?.orderId) {
        setPastOrders((prev) => {
          const next = Array.isArray(prev)
            ? prev.filter((entry) => entry.orderId !== order.orderId)
            : [];
          persistPastOrders(next);
          return next;
        });
      }
      deleteOrderRef.current = null;
    } else if (context === "returnHomeFromShoppingList") {
      handleReturnToWelcome();
    }
  }, [
    confirmationDialog.context,
    handleBuildShoppingList,
    handleConfirmPreferenceComplete,
    handleSendShoppingListToCart,
    recordPastOrder,
    selectedHomeMeals,
    persistPastOrders,
    handleReturnToWelcome,
  ]);

  const handleOpenShoppingListConfirm = useCallback((options = {}) => {
    const { triggerLearning = false, nextScreen = "ingredients" } = options;
    shoppingListLearningIntentRef.current = triggerLearning;
    shoppingListNextScreenRef.current = nextScreen;
    if (!selectedHomeMeals.length) {
      showConfirmationDialog(
        "noMeals",
        "Please select at least one meal to build a shopping list."
      );
      return;
    }
    if (!SHOPPING_LIST_API_ENDPOINT) {
      Alert.alert(
        "Shopping list unavailable",
        "Update the app configuration to enable shopping list preparation."
      );
      return;
    }
    showConfirmationDialog("shoppingList", "Use one free use");
  }, [SHOPPING_LIST_API_ENDPOINT, selectedHomeMeals.length, showConfirmationDialog]);

  const handleIngredientsShoppingListNotice = useCallback(() => {
    if (!selectedHomeMeals.length) {
      Alert.alert(
        "Select meals",
        "Choose at least one meal before preparing your shopping list."
      );
      return;
    }
    if (shoppingListStatus === "pending") {
      Alert.alert(
        "Please wait",
        "We're still preparing your shopping list. Try again in a moment."
      );
      return;
    }
    if (shoppingListItems.length > 0 && shoppingListStatus === "ready") {
      setScreen("shoppingList");
      return;
    }
    handleOpenShoppingListConfirm({
      triggerLearning: true,
      nextScreen: "shoppingList",
    });
  }, [
    handleOpenShoppingListConfirm,
    selectedHomeMeals.length,
    setScreen,
    shoppingListItems.length,
    shoppingListStatus,
  ]);

  const confirmationDialogPortal = confirmationDialog.visible ? (
    <View style={styles.mealDetailModalContainer} pointerEvents="box-none">
      <TouchableWithoutFeedback onPress={handleCloseConfirmationDialog}>
        <View style={styles.mealDetailBackdrop} />
      </TouchableWithoutFeedback>
      <View style={styles.mealDetailCard}>
        <View style={styles.confirmModalContent}>
          <Text style={styles.mealDetailTitle}>Please confirm</Text>
          <Text style={styles.confirmSubtitle}>{confirmationDialogSubtitle}</Text>
        </View>
        <TouchableOpacity
          style={styles.confirmAcceptButton}
          onPress={handleConfirmDialog}
          accessibilityRole="button"
          accessibilityLabel="Confirm action"
        >
          <Text style={styles.confirmAcceptText}>✓</Text>
        </TouchableOpacity>
        {confirmationDialog.context !== "noMeals" && (
          <TouchableOpacity
            style={styles.mealDetailCloseButton}
            onPress={handleCloseConfirmationDialog}
            accessibilityRole="button"
            accessibilityLabel="Dismiss confirmation"
          >
            <Text style={styles.mealDetailCloseText}>×</Text>
          </TouchableOpacity>
        )}
      </View>
    </View>
  ) : null;

  const handleOpenNewMealsConfirm = useCallback(() => {
    showConfirmationDialog("newMeals");
  }, [showConfirmationDialog]);
  const handleOpenWoolworthsCartConfirm = useCallback(() => {
    if (!canSendShoppingListToCart) {
      const message =
        shoppingListStatus === "pending"
          ? "We're still preparing your shopping list. Try again in a moment."
          : "Prepare your shopping list before sending it to Woolworths.";
      Alert.alert("Shopping list not ready", message);
      return;
    }
    showConfirmationDialog("woolworthsCart");
  }, [canSendShoppingListToCart, shoppingListStatus, showConfirmationDialog]);
  const handleOpenSorryToHearScreen = useCallback(() => {
    setIsMealMenuOpen(false);
    setIsSorryToHearScreenVisible(true);
  }, []);
  const handleCloseSorryToHearScreen = useCallback(() => {
    setIsSorryToHearScreenVisible(false);
  }, []);
  const walletEndpoint = API_BASE_URL ? `${API_BASE_URL}/wallet/balance` : null;
  const payfastInitiateEndpoint = API_BASE_URL
    ? `${API_BASE_URL}/payments/payfast/initiate`
    : null;
  const payfastStatusEndpoint = API_BASE_URL
    ? `${API_BASE_URL}/payments/payfast/status`
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

  const buildShoppingListRequestPayload = useCallback(() => {
    const mealsPayload = selectedHomeMeals
      .filter((meal) => meal && meal.mealId)
      .map((meal) => {
        const finalIngredients = Array.isArray(meal.finalIngredients)
          ? meal.finalIngredients
          : Array.isArray(meal.final_ingredients)
          ? meal.final_ingredients
          : Array.isArray(meal.ingredients)
          ? meal.ingredients
          : [];
        const servingsText = deriveServingsTextForPayload(meal);
        return {
          mealId: meal.mealId,
          name: meal.name,
          servings: servingsText,
          ingredients: finalIngredients.filter(Boolean),
        };
      })
      .filter((meal) => meal.ingredients.length > 0);
    return { meals: mealsPayload };
  }, [selectedHomeMeals]);

  const refreshLatestRecommendations = useCallback(
    async (options = {}) => {
      if (!RECOMMENDATIONS_LATEST_ENDPOINT || !userId) {
        return false;
      }
      const { skipIfPending = false, force = false } = options;
      if (latestRecommendationsRequestRef.current) {
        if (skipIfPending) {
          return false;
        }
        latestRecommendationsRequestRef.current.abort();
      }
      const controller = new AbortController();
      latestRecommendationsRequestRef.current = controller;
      try {
        const headers = await buildAuthHeaders();
        const response = await fetch(RECOMMENDATIONS_LATEST_ENDPOINT, {
          headers,
          signal: controller.signal,
        });
        if (!response.ok) {
          if (response.status === 404) {
            return false;
          }
          throw new Error(`Latest recommendations request failed (${response.status})`);
        }
        const payload = await response.json();
        const recommendationMeals = Array.isArray(payload?.latestRecommendationMeals)
          ? payload.latestRecommendationMeals.filter(Boolean)
          : Array.isArray(payload?.meals)
          ? payload.meals.filter(Boolean)
          : [];
        if (!recommendationMeals.length) {
          return false;
        }
        const generatedAtValue =
          payload?.generatedAt ??
          payload?.generated_at ??
          payload?.generatedAt ??
          null;
        const normalizedGeneratedAt = normalizeGeneratedAtValue(generatedAtValue);
        if (
          normalizedGeneratedAt &&
          homeRecommendationsGeneratedAt &&
          !force
        ) {
          const currentTs = new Date(homeRecommendationsGeneratedAt).valueOf();
          const nextTs = new Date(normalizedGeneratedAt).valueOf();
          if (!Number.isNaN(currentTs) && !Number.isNaN(nextTs) && nextTs <= currentTs) {
            return false;
          }
        }
        applyHomeRecommendedMeals(recommendationMeals, { generatedAt: normalizedGeneratedAt });
        return true;
      } catch (error) {
        if (error?.name === "AbortError") {
          return false;
        }
        if (__DEV__) {
          console.warn("Failed to refresh latest recommendations", error);
        }
        return false;
      } finally {
        if (latestRecommendationsRequestRef.current === controller) {
          latestRecommendationsRequestRef.current = null;
        }
      }
    },
    [
      applyHomeRecommendedMeals,
      buildAuthHeaders,
      homeRecommendationsGeneratedAt,
      userId,
    ]
  );

  const handleBuildShoppingList = useCallback(async (options = {}) => {
    const {
      triggerLearning = false,
      nextScreen = "ingredients",
    } = options;
    const payload = buildShoppingListRequestPayload();
    if (!payload.meals.length) {
      setShoppingListItems([]);
      setShoppingListStatus("idle");
      setShoppingListError("Select at least one meal to prepare a shopping list.");
      Alert.alert(
        "Missing meal details",
        "We couldn't find ingredient details for the selected meals. Refresh recommendations and try again."
      );
      return;
    }
    if (!SHOPPING_LIST_API_ENDPOINT) {
      setShoppingListStatus("error");
      setShoppingListError("Shopping list builder is not configured.");
      return;
    }
    setIsMealMenuOpen(false);
    setScreen("buildingShoppingList");
    setShoppingListStatus("pending");
    setShoppingListError(null);
    setShoppingListItems([]);
    try {
      const headers = await buildAuthHeaders({ "Content-Type": "application/json" });
      const response = await fetch(SHOPPING_LIST_API_ENDPOINT, {
        method: "POST",
        headers,
        body: JSON.stringify({
          ...payload,
          triggerRecommendationLearning: Boolean(triggerLearning),
        }),
      });
      if (!response.ok) {
        throw new Error(`Shopping list request failed (${response.status})`);
      }
      const data = await response.json();
      const items = Array.isArray(data?.items) ? data.items.filter(Boolean) : [];
      setShoppingListItems(items);
      setShoppingListStatus("ready");
      if (nextScreen === "shoppingList") {
        recordPastOrder(selectedHomeMeals, { shoppingListItems: items });
        setScreen("shoppingList");
      } else {
        setScreen("ingredients");
      }
    } catch (error) {
      console.warn("Shopping list build failed", error);
      setShoppingListStatus("error");
      setShoppingListError(
        error?.message ?? "Something went wrong while preparing your shopping list."
      );
      setScreen("ingredients");
    }
  }, [
    SHOPPING_LIST_API_ENDPOINT,
    buildAuthHeaders,
    buildShoppingListRequestPayload,
    recordPastOrder,
    selectedHomeMeals,
  ]);

  const handleSendShoppingListToCart = useCallback(async () => {
    if (!canSendShoppingListToCart) {
      const message =
        shoppingListStatus === "pending"
          ? "We're still preparing your shopping list. Try again in a moment."
          : "Prepare your shopping list before sending it to Woolworths.";
      Alert.alert("Shopping list not ready", message);
      return;
    }
    const { items: cartItems, skipped } = buildShoppingListCartItems();
    if (!cartItems.length) {
      const summary =
        formatSkippedIngredientSummary(skipped) ??
        "We couldn't find Woolworths products for your current selections.";
      Alert.alert("Nothing to send", summary);
      return;
    }
    if (skipped.length) {
      const summary = formatSkippedIngredientSummary(skipped);
      if (summary) {
        Alert.alert(
          "Skipping a few items",
          `We'll skip ${summary} because we couldn't find Woolworths product matches.`
        );
      }
    }
    const selectedMealIds = selectedHomeMeals
      .map((meal) => meal?.mealId)
      .filter(Boolean);
    setIsCartPushPending(true);
    setBasket(cartItems);
    setErrorMessage(null);
    try {
      await placeRunnerOrder(cartItems, {
        trigger: "shopping_list",
        shoppingListItemCount: shoppingListItems.length,
        cartItemCount: cartItems.length,
        skippedIngredientCount: skipped.length,
        skippedIngredients: skipped.map((entry) => entry.label).filter(Boolean),
        selectedMealIds,
      });
    } catch (error) {
      console.error("Failed to push shopping list to Woolworths", error);
      const message = error?.message ?? "Unable to start the Woolworths cart fill.";
      setErrorMessage(message);
      Alert.alert("Cart fill failed", message);
    } finally {
      setIsCartPushPending(false);
    }
  }, [
    buildShoppingListCartItems,
    canSendShoppingListToCart,
    placeRunnerOrder,
    selectedHomeMeals,
    shoppingListItems.length,
    shoppingListStatus,
  ]);

  const startExplorationRun = useCallback(async () => {
    if (!EXPLORATION_API_ENDPOINT) {
      return;
    }
    setExplorationState("running");
    setExplorationError(null);
    setHasSeenExplorationResults(false);
    try {
      const headers = await buildAuthHeaders({
        "Content-Type": "application/json",
      });
      const response = await fetch(EXPLORATION_API_ENDPOINT, {
        method: "POST",
        headers,
        body: JSON.stringify({ mealCount: EXPLORATION_MEAL_TARGET }),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(
          errorPayload?.detail ?? "Unable to prepare exploration recipes"
        );
      }
      const data = await response.json();
      setExplorationSessionId(data?.sessionId ?? null);
      setExplorationMeals(data?.meals ?? []);
      setExplorationNotes(data?.infoNotes ?? []);
      setExplorationReactions({});
      setExplorationState("ready");
    } catch (error) {
      console.warn("Exploration run failed", error);
      setExplorationError(
        error?.message ?? "Something went wrong while preparing recipes."
      );
      setExplorationState("error");
    }
  }, [buildAuthHeaders, EXPLORATION_API_ENDPOINT]);

  const handleRetryExploration = useCallback(() => {
    setExplorationMeals([]);
    setExplorationNotes([]);
    setExplorationSessionId(null);
    setExplorationReactions({});
    setExplorationState("idle");
    setHasSeenExplorationResults(false);
  }, []);

  const runRecommendationFeed = useCallback(async () => {
    if (!RECOMMENDATION_API_ENDPOINT || !explorationSessionId) {
      return [];
    }
    const headers = await buildAuthHeaders({
      "Content-Type": "application/json",
    });
    const reactionPayload = Object.entries(explorationReactions || {})
      .filter(([, state]) => state === "like" || state === "dislike")
      .map(([mealId, reaction]) => ({ mealId, reaction }));
    const response = await fetch(RECOMMENDATION_API_ENDPOINT, {
      method: "POST",
      headers,
      body: JSON.stringify({
        explorationSessionId,
        mealCount: RECOMMENDATION_MEAL_TARGET,
        reactions: reactionPayload,
      }),
    });
    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      throw new Error(
        errorPayload?.detail ??
          "Unable to generate follow-up recommendations right now."
      );
    }
    const data = await response.json();
    return Array.isArray(data?.meals) ? data.meals : [];
  }, [
    RECOMMENDATION_API_ENDPOINT,
    buildAuthHeaders,
    explorationReactions,
    explorationSessionId,
  ]);

  const handleCompleteOnboardingFlow = useCallback((options = {}) => {
    const { seedHomeMeals } = options;
    setHasFetchedRemotePreferences(false);
    if (Array.isArray(seedHomeMeals)) {
      applyHomeRecommendedMeals(seedHomeMeals);
    } else {
      applyHomeRecommendedMeals([]);
    }
    setIsOnboardingActive(false);
    setExplorationState("idle");
    setExplorationMeals([]);
    setExplorationNotes([]);
    setExplorationSessionId(null);
    setExplorationError(null);
    setExplorationReactions({});
    setHomeSurface("meal");
    setIsMealMenuOpen(false);
    setScreen("home");
    preferenceEntryContextRef.current = null;
  }, [applyHomeRecommendedMeals]);

  const handleConfirmExplorationReview = useCallback(async () => {
    if (isCompletingExploration) {
      return;
    }
    setIsCompletingExploration(true);
    setScreen("buildingRecommendations");
    try {
      const meals = await runRecommendationFeed();
      handleCompleteOnboardingFlow({
        seedHomeMeals: meals.length ? meals : undefined,
      });
    } catch (error) {
      console.warn("Failed to build recommendation feed", error);
      handleCompleteOnboardingFlow();
    } finally {
      setIsCompletingExploration(false);
    }
  }, [
    handleCompleteOnboardingFlow,
    isCompletingExploration,
    runRecommendationFeed,
    setScreen,
  ]);

  const handleExplorationReaction = useCallback((mealId, value) => {
    setExplorationReactions((prev) => {
      const currentValue = prev[mealId] ?? "neutral";
      const resolvedValue = resolvePreferenceSelectionValue(
        currentValue,
        value
      );
      if (resolvedValue === "neutral") {
        if (!prev[mealId]) {
          return prev;
        }
        const next = { ...prev };
        delete next[mealId];
        return next;
      }
      return {
        ...prev,
        [mealId]: resolvedValue,
      };
    });
  }, []);

  const renderExplorationMeal = useCallback(
    ({ item }) => {
      const reaction = explorationReactions[item.mealId];
      const tagEntries = Object.entries(item.tags ?? {}).slice(0, 3);
      return (
        <View style={styles.explorationCard}>
          <Text style={styles.explorationMealName}>{item.name}</Text>
          {item.description ? (
            <Text style={styles.explorationMealDescription}>
              {item.description}
            </Text>
          ) : null}
          {tagEntries.length > 0 && (
            <View style={styles.explorationTagRow}>
              {tagEntries.map(([category, values]) => (
                <View key={`${item.mealId}-${category}`} style={styles.explorationTagChip}>
                  <Text style={styles.explorationTagText}>
                    {`${category}: ${values.slice(0, 2).join(", ")}`}
                  </Text>
                </View>
              ))}
            </View>
          )}
          {item.keyIngredients?.length ? (
            <View style={styles.explorationIngredients}>
              <Text style={styles.explorationIngredientLabel}>
                Key ingredients
              </Text>
              {item.keyIngredients.slice(0, 4).map((ingredient, index) => (
                <Text key={`${item.mealId}-ingredient-${index}`} style={styles.explorationIngredientText}>
                  • {ingredient.name}
                  {ingredient.quantity ? ` (${ingredient.quantity})` : ""}
                </Text>
              ))}
            </View>
          ) : null}
          <View style={styles.explorationActions}>
            <TouchableOpacity
              style={[
                styles.explorationActionButton,
                reaction === "like" && styles.explorationActionButtonActive,
              ]}
              onPress={() => handleExplorationReaction(item.mealId, "like")}
            >
              <Text
                style={[
                  styles.explorationActionText,
                  reaction === "like" && styles.explorationActionTextActive,
                ]}
              >
                Like
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[
                styles.explorationActionButton,
                styles.explorationActionButtonDislike,
                reaction === "dislike" && styles.explorationActionButtonActive,
              ]}
              onPress={() =>
                handleExplorationReaction(item.mealId, "dislike")
              }
            >
              <Text
                style={[
                  styles.explorationActionText,
                  reaction === "dislike" && styles.explorationActionTextActive,
                ]}
              >
                Dislike
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      );
    },
    [explorationReactions, handleExplorationReaction]
  );

  const renderExplorationMealCard = useCallback(
    (meal, index = 0) => {
      if (!meal) {
        return null;
      }
      const reaction = explorationReactions[meal.mealId] ?? "neutral";
      const servingsCount = deriveMealServingsCount(meal);
      const servingsLabel = formatServingsPeopleLabel(servingsCount);
      const mealKeyBase = meal.mealId ?? meal.name ?? "meal";
      const mealKey = `${mealKeyBase}-${index}`;
      return (
        <View key={mealKey} style={styles.homeMealCard}>
          <Text style={styles.homeMealTitle}>
            {meal.name ?? "Meal"}
          </Text>
          {meal.description ? (
            <Text style={styles.homeMealDescription}>
              {meal.description}
            </Text>
          ) : null}
          {Array.isArray(meal.tags?.PrepTime) &&
          meal.tags.PrepTime.length > 0 ? (
            <Text style={styles.homeMealPrepTime}>
              Prep time: {meal.tags.PrepTime.join(", ")}
            </Text>
          ) : null}
          <View style={styles.homeMealFooterRow}>
            <View style={styles.homeMealServingsRow}>
              <Text style={styles.homeMealServingsLabel}>Servings:</Text>
              <Text style={styles.homeMealServingsValueText}>
                {servingsLabel}
              </Text>
            </View>
            <View style={styles.explorationReactionInlineControls}>
              {PREFERENCE_CONTROL_STATES.map((control) => {
                const isSelected = reaction === control.id;
                const controlStyles = [
                  styles.prefControlButton,
                  control.id === "like" && styles.prefControlButtonLike,
                  control.id === "dislike" &&
                    styles.prefControlButtonDislike,
                  control.id === "neutral" &&
                    styles.prefControlButtonNeutral,
                  isSelected && styles.prefControlButtonActive,
                  isSelected &&
                    control.id === "like" &&
                    styles.prefControlButtonLikeActive,
                  isSelected &&
                    control.id === "dislike" &&
                    styles.prefControlButtonDislikeActive,
                  isSelected &&
                    control.id === "neutral" &&
                    styles.prefControlButtonNeutralActive,
                ];
                return (
                  <TouchableOpacity
                    key={`${mealKey}-${control.id}`}
                    style={controlStyles}
                    onPress={() => {
                      handleExplorationReaction(meal.mealId, control.id);
                    }}
                    accessibilityRole="button"
                    accessibilityLabel={`${control.label} ${meal.name ?? ""}`}
                  >
                    <Text
                      style={[
                        styles.prefControlIcon,
                        control.id === "like" && styles.prefControlIconLike,
                        control.id === "dislike" &&
                          styles.prefControlIconDislike,
                        control.id === "neutral" &&
                          styles.prefControlIconNeutral,
                        isSelected && styles.prefControlIconActive,
                        isSelected &&
                          control.id === "like" &&
                          styles.prefControlIconLikeActive,
                        isSelected &&
                          control.id === "dislike" &&
                          styles.prefControlIconDislikeActive,
                        isSelected &&
                          control.id === "neutral" &&
                          styles.prefControlIconNeutralActive,
                      ]}
                    >
                      {control.icon}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </View>
        </View>
      );
    },
    [explorationReactions, handleExplorationReaction]
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
  const payfastMonitorRef = useRef(null);
  const payfastAlertRef = useRef(null);

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

  useEffect(() => {
    payfastMonitorRef.current = payfastMonitor;
  }, [payfastMonitor]);

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

  useEffect(() => {
    if (!payfastMonitor?.reference || !payfastStatusEndpoint) {
      return undefined;
    }
    let cancelled = false;
    let timeoutId = null;
    let attempts = 0;

    const pollStatus = async () => {
      if (cancelled) {
        return;
      }
      const current = payfastMonitorRef.current;
      if (
        !current ||
        !current.reference ||
        current.reference !== payfastMonitor.reference ||
        isPayfastTrackerDone(current.status, current.walletCredited)
      ) {
        return;
      }
      if (attempts >= MAX_PAYFAST_POLLS) {
        setPayfastMonitor((prev) => {
          if (!prev || prev.reference !== current.reference) {
            return prev;
          }
          return {
            ...prev,
            error: prev.error ?? "Timed out waiting for PayFast confirmation.",
            attempts: prev.attempts + 1,
          };
        });
        return;
      }
      attempts += 1;
      try {
        const headers = await buildAuthHeaders();
        const url = `${payfastStatusEndpoint}?reference=${encodeURIComponent(current.reference)}`;
        const response = await fetch(url, { headers });
        if (!response.ok) {
          throw new Error(`Status check failed (${response.status})`);
        }
        const payload = await response.json();
        if (cancelled) {
          return;
        }
        const normalizedWalletCredited =
          typeof payload.walletCredited !== "undefined"
            ? payload.walletCredited
            : payload.wallet_credited;
        const nextStatus = payload.status ?? current.status;
        const finished = isPayfastTrackerDone(
          nextStatus,
          typeof normalizedWalletCredited !== "undefined"
            ? normalizedWalletCredited
            : current.walletCredited
        );
        setPayfastMonitor((prev) => {
          if (!prev || prev.reference !== current.reference) {
            return prev;
          }
          return {
            ...prev,
            status: nextStatus,
            message: payload.message ?? prev.message,
            pfStatus: payload.pfStatus ?? payload.pf_status ?? prev.pfStatus ?? null,
            walletCredited: Boolean(
              typeof normalizedWalletCredited !== "undefined"
                ? normalizedWalletCredited
                : prev.walletCredited
            ),
            providerPaymentId:
              payload.providerPaymentId ??
              payload.provider_payment_id ??
              prev.providerPaymentId ??
              null,
            updatedAt: payload.updatedAt ?? payload.updated_at ?? prev.updatedAt ?? null,
            attempts: prev.attempts + 1,
            error: null,
            lastChecked: new Date().toISOString(),
          };
        });

        if (!finished && !cancelled) {
          timeoutId = setTimeout(pollStatus, 4000);
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        setPayfastMonitor((prev) => {
          if (!prev || !prev.reference) {
            return prev;
          }
          return {
            ...prev,
            attempts: prev.attempts + 1,
            error: error.message ?? "Unable to check PayFast status",
            lastChecked: new Date().toISOString(),
          };
        });
        timeoutId = setTimeout(pollStatus, 6000);
      }
    };

    pollStatus();

    return () => {
      cancelled = true;
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, [payfastMonitor?.reference, payfastStatusEndpoint, buildAuthHeaders]);

  useEffect(() => {
    if (!payfastMonitor || !payfastMonitor.reference) {
      return;
    }
    if (!isPayfastTrackerDone(payfastMonitor.status, payfastMonitor.walletCredited)) {
      return;
    }
    const key = `${payfastMonitor.reference}:${payfastMonitor.status}:${payfastMonitor.walletCredited}`;
    if (payfastAlertRef.current === key) {
      return;
    }
    payfastAlertRef.current = key;
    if (payfastMonitor.status === "complete") {
      fetchWallet();
      // Success handled via inline status card so no modal tap needed.
      return;
    }
    const title = payfastMonitor.status === "complete" ? "Top-up Complete" : "PayFast Update";
    const message =
      payfastMonitor.message ??
      (payfastMonitor.status === "complete"
        ? "Wallet credited via PayFast."
        : "Payment did not complete. Please review your PayFast session.");
    Alert.alert(title, message);
  }, [payfastMonitor, fetchWallet]);

  const placeRunnerOrder = useCallback(
    async (items, metadataOverrides = {}) => {
      const normalizedItems = normalizeOrderItems(items ?? []);
      if (!normalizedItems.length) {
        throw new Error("Cart is empty. Select at least one product.");
      }
      const metadataPayload = {
        source: "thin-slice-app",
        requestedAt: new Date().toISOString(),
        ...(metadataOverrides && typeof metadataOverrides === "object"
          ? metadataOverrides
          : {}),
      };
      const response = await fetch(PLACE_ORDER_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: normalizedItems,
          metadata: metadataPayload,
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
      setOrderStatus({
        message,
        receivedItems: payload?.receivedItems ?? normalizedItems.length,
        timestamp: new Date(),
        orderId,
      });
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
      return { orderId, redirectUrl };
    },
    [resetRunnerLogFile]
  );

  const handleSubmitOrder = useCallback(async () => {
    if (!basket.length) {
      setErrorMessage("Basket empty. Build the basket before placing an order.");
      return;
    }
    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      await placeRunnerOrder(basket);
    } catch (error) {
      console.error("Failed to submit order", error);
      const msg = error.message ?? "Unknown order submission error";
      setErrorMessage(msg);
      Alert.alert("Order Placement Failed", msg);
    } finally {
      setIsSubmitting(false);
    }
  }, [basket, placeRunnerOrder]);

  const handleRefreshWallet = useCallback(() => {
    fetchWallet();
  }, [fetchWallet]);

  const handlePayfastNavigation = useCallback(
    (state) => {
      if (!state?.url) return;
      const currentUrl = state.url;
      const reachedReturn =
        urlStartsWith(currentUrl, PAYFAST_RETURN_URL) ||
        urlStartsWith(currentUrl, payfastSession?.returnUrl);
      const reachedCancel =
        urlStartsWith(currentUrl, PAYFAST_CANCEL_URL) ||
        urlStartsWith(currentUrl, payfastSession?.cancelUrl);
      if (reachedReturn) {
        setPayfastSession(null);
        setScreen("home");
        setPayfastMonitor((prev) =>
          prev
            ? {
                ...prev,
                status: prev.status === "pending" ? "processing" : prev.status,
                message: "PayFast response received. Waiting for confirmation.",
              }
            : prev
        );
        fetchWallet();
      } else if (reachedCancel) {
        setPayfastSession(null);
        setScreen("home");
        Alert.alert("Payment Cancelled", "Top-up was cancelled by the user.");
        setPayfastMonitor((prev) =>
          prev
            ? {
                ...prev,
                status: "cancelled",
                message: "Payment cancelled before completion.",
              }
            : prev
        );
        fetchWallet();
      }
    },
    [fetchWallet, payfastSession]
  );

  const handleCancelPayfast = useCallback(() => {
    setPayfastSession(null);
    setScreen("home");
    setPayfastMonitor((prev) =>
      prev
        ? {
            ...prev,
            status: "cancelled",
            message: "Payment cancelled before completion.",
          }
        : prev
    );
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
    if (__DEV__) {
      console.log("Top-up endpoint:", payfastInitiateEndpoint);
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
        if (__DEV__) {
          try {
            const errorText = await response.text();
            console.log("Top-up response body:", errorText);
          } catch (respErr) {
            console.log("Top-up response body unavailable:", respErr);
          }
        }
        if (response.status === 401) {
          throw new Error("Unauthorized. Please sign in again.");
        }
        throw new Error(`Top-up initiation failed (${response.status})`);
      }
      const payload = await response.json();
      if (!payload?.url || !payload?.params) {
        throw new Error("Unexpected response from server");
      }
      if (!payload?.reference) {
        throw new Error("Missing PayFast reference from server response");
      }
      setPayfastMonitor({
        reference: payload.reference,
        status: "pending",
        message: "Waiting for PayFast confirmation.",
        attempts: 0,
        walletCredited: false,
        error: null,
        pfStatus: null,
        lastChecked: null,
        updatedAt: null,
        providerPaymentId: null,
      });
      setPayfastSession({
        html: buildAutoSubmitHtml(payload.url, payload.params),
        reference: payload.reference,
        returnUrl: payload.params?.return_url ?? null,
        cancelUrl: payload.params?.cancel_url ?? null,
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
    setHomeSurface("runner");
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

  const mealMenuOverlay = isMealMenuOpen ? (
    <View style={styles.mealMenuContainer} pointerEvents="box-none">
      <TouchableWithoutFeedback onPress={closeMealMenu}>
        <View style={styles.mealMenuBackdrop} />
      </TouchableWithoutFeedback>
      <View style={[styles.mealMenuCard, { top: menuOverlayTop }]}>
        <TouchableOpacity style={styles.mealMenuItem} onPress={handleMealMenuReset}>
          <Text style={styles.mealMenuItemText}>Update Preferences</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.mealMenuItem} onPress={handleMealMenuSignOut}>
          <Text style={[styles.mealMenuItemText, styles.mealMenuItemDanger]}>
            Sign out
          </Text>
        </TouchableOpacity>
      </View>
    </View>
  ) : null;

  if (screen === "pastOrders") {
    return (
      <SafeAreaView style={styles.mealHomeSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.mealHomeHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handleClosePastOrders}
            accessibilityRole="button"
            accessibilityLabel="Back to welcome"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.mealHomeMenuButton}
            onPress={toggleMealMenu}
            accessibilityRole="button"
            accessibilityLabel="Open menu"
          >
            <Feather name="menu" size={24} color="#0c3c26" />
          </TouchableOpacity>
        </View>
        <ScrollView
          style={styles.pastOrdersScroll}
          contentContainerStyle={styles.pastOrdersContent}
          showsVerticalScrollIndicator={false}
        >
          {pastOrders.length === 0 ? (
            <View style={styles.pastOrdersEmptyState}>
              <Text style={styles.pastOrdersEmptyTitle}>No orders yet</Text>
              <Text style={styles.pastOrdersEmptySubtitle}>
                Send a shopping list to Woolworths to build your first past order.
              </Text>
            </View>
          ) : (
            pastOrders.map((order) => {
              const orderDate = new Date(order.createdAt);
              const hasValidDate = !Number.isNaN(orderDate.getTime());
              const dayLabel = hasValidDate
                ? orderDate.toLocaleDateString(undefined, { weekday: "long" })
                : "Unknown day";
              const dateLabel = hasValidDate
                ? orderDate.toLocaleDateString(undefined, {
                    month: "long",
                    day: "numeric",
                  })
                : "Unknown date";
              const mealCount = Array.isArray(order.meals)
                ? order.meals.length
                : 0;
              return (
                <TouchableOpacity
                  key={order.orderId ?? `${order.createdAt}-${mealCount}`}
                  style={styles.pastOrderCard}
                  onPress={() => handleOpenPastOrderDetails(order)}
                >
                <View style={styles.pastOrderCardHeader}>
                  <Text style={styles.pastOrderCardDay}>{dayLabel}</Text>
                  <View style={styles.pastOrderCardDateWrapper}>
                    <Text style={styles.pastOrderCardDate}>{dateLabel}</Text>
                  </View>
                </View>
                <Text style={styles.pastOrderCardMeta}>
                  {mealCount} {mealCount === 1 ? "meal" : "meals"}
                </Text>
                <View style={styles.pastOrderFooter}>
                  <TouchableOpacity
                    style={[
                      styles.prefControlButton,
                      styles.prefControlButtonPrimary,
                      styles.pastOrderListButton,
                    ]}
                    onPress={(event) => {
                      event?.stopPropagation?.();
                      handleShowPastOrderShoppingList(order);
                    }}
                    accessibilityRole="button"
                    accessibilityLabel="View shopping list"
                  >
                    <Feather
                      name="list"
                      size={16}
                      style={[styles.prefControlIcon, styles.prefControlIconPrimary]}
                    />
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[
                      styles.prefControlButton,
                      styles.prefControlButtonDislike,
                      styles.pastOrderDeleteButton,
                    ]}
                    onPress={(event) => {
                      event?.stopPropagation?.();
                      handleDeletePastOrder(order);
                    }}
                    accessibilityRole="button"
                    accessibilityLabel="Delete past order"
                  >
                    <Feather
                      name="trash-2"
                      size={16}
                      style={[
                        styles.prefControlIcon,
                        styles.prefControlIconDislike,
                      ]}
                    />
                  </TouchableOpacity>
                </View>
              </TouchableOpacity>
            );
          })
          )}
        </ScrollView>
        {mealMenuOverlay}
        {confirmationDialogPortal}
        {homeMealDetailModal}
      </SafeAreaView>
    );
  }

  if (screen === "pastOrderShoppingList" && activePastOrderShoppingList) {
    const listLabel = getPastOrderLabel(activePastOrderShoppingList);
    return (
      <SafeAreaView style={styles.shoppingListSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.shoppingListHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handleClosePastOrderShoppingList}
            accessibilityRole="button"
            accessibilityLabel="Back to past orders"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <View>
            <Text style={styles.shoppingListHeaderTitle}>{listLabel}</Text>
            <Text style={styles.shoppingListHeaderSubtitle}>Past order</Text>
          </View>
          <View style={styles.shoppingListHeaderAction} />
        </View>
        <View style={styles.shoppingListBody}>
          {pastOrderShoppingListDisplayItems.length === 0 ? (
            <View style={styles.shoppingListEmptyState}>
              <Text style={styles.shoppingListEmptyTitle}>No shopping list items</Text>
              <Text style={styles.shoppingListEmptySubtitle}>
                We couldn&apos;t find any saved ingredients for this order.
              </Text>
            </View>
          ) : (
            <ScrollView
              style={styles.shoppingListScroll}
              contentContainerStyle={styles.shoppingListScrollContent}
              showsVerticalScrollIndicator={false}
            >
              {pastOrderShoppingListDisplayItems.map((item) => {
                const placeholderInitial = (item.displayName ?? "?")
                  .trim()
                  .charAt(0)
                  .toUpperCase();
                const isChecked = checkedShoppingListItems.has(item.id);
                return (
                  <TouchableOpacity
                    key={item.id}
                    style={[
                      styles.shoppingListItem,
                      isChecked && styles.shoppingListItemChecked,
                    ]}
                    onPress={() => handleToggleShoppingListItem(item.id)}
                    activeOpacity={0.7}
                  >
                    <View style={styles.shoppingListItemImageWrapper}>
                      {item.imageUrl ? (
                        <Image
                          source={{ uri: item.imageUrl, cache: "reload" }}
                          style={styles.shoppingListItemImage}
                          onError={() => scheduleImageReload(item.id)}
                          key={`past-order-${item.id}-${imageReloadCounters[item.id] ?? 0}`}
                        />
                      ) : (
                        <View style={styles.shoppingListItemImagePlaceholder}>
                          <Text style={styles.shoppingListItemImagePlaceholderText}>
                            {placeholderInitial || "?"}
                          </Text>
                        </View>
                      )}
                    </View>
                    <View style={styles.shoppingListItemBody}>
                      <Text
                        style={[
                          styles.shoppingListItemName,
                          isChecked && styles.shoppingListItemNameChecked,
                        ]}
                      >
                        {item.displayName}
                      </Text>
                      <Text
                        style={[
                          styles.shoppingListItemQuantity,
                          isChecked && styles.shoppingListItemQuantityChecked,
                        ]}
                      >
                        Quantity: {item.displayQuantity}
                      </Text>
                    </View>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          )}
        </View>
        {mealMenuOverlay}
        {confirmationDialogPortal}
      </SafeAreaView>
    );
  }

  if (screen === "pastOrderDetails" && activePastOrder) {
    const mealsList = Array.isArray(activePastOrder.meals)
      ? activePastOrder.meals
      : [];
    return (
      <SafeAreaView style={styles.mealHomeSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.mealHomeHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handleClosePastOrderDetails}
            accessibilityRole="button"
            accessibilityLabel="Back to past orders"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.mealHomeMenuButton}
            onPress={toggleMealMenu}
            accessibilityRole="button"
            accessibilityLabel="Open menu"
          >
            <Feather name="menu" size={24} color="#0c3c26" />
          </TouchableOpacity>
        </View>
        <View style={styles.pastOrderInstructionWrapper}>
          <View style={styles.pastOrderInstructionCard}>
            <Text style={styles.pastOrderInstructionText}>
              Tap meal card to see details.
            </Text>
            <Text style={styles.pastOrderInstructionText}>
              Thumbs down if you didn’t enjoy a meal.
            </Text>
            <Text style={styles.pastOrderInstructionText}>
              Heart adds it to your favorites.
            </Text>
          </View>
        </View>
        <View style={styles.pastOrderMealsContainer}>
          <ScrollView
            style={styles.pastOrderMealsScroll}
            contentContainerStyle={styles.pastOrderMealsContent}
            showsVerticalScrollIndicator={false}
          >
            {mealsList.map((meal, index) => {
              const servingsCount = deriveMealServingsCount(meal);
              const servingsLabel = formatServingsPeopleLabel(servingsCount);
              const isSelected = Boolean(selectedHomeMealIds[meal?.mealId]);
              const isDisliked = Boolean(homeMealDislikedIds[meal?.mealId]);
              return (
                <TouchableOpacity
                  key={`${meal?.mealId ?? "meal"}-${index}`}
                  style={styles.homeMealCard}
                  activeOpacity={0.9}
                  onPress={() => setHomeMealModal({ visible: true, meal })}
                >
                  <Text style={styles.homeMealTitle}>{meal?.name ?? "Meal"}</Text>
                  {meal?.description ? (
                    <Text style={styles.homeMealDescription}>{meal.description}</Text>
                  ) : null}
                  {Array.isArray(meal?.tags?.PrepTime) &&
                  meal.tags.PrepTime.length > 0 ? (
                    <Text style={styles.homeMealPrepTime}>
                      Prep time: {meal.tags.PrepTime.join(", ")}
                    </Text>
                  ) : null}
                <View style={styles.homeMealFooterRow}>
                  <View style={styles.homeMealServingsRow}>
                    <Text style={styles.homeMealServingsLabel}>Servings:</Text>
                    <Text style={styles.homeMealServingsValueText}>
                      {servingsLabel}
                    </Text>
                  </View>
                  <View
                    style={[styles.prefControls, styles.pastOrderActionControls]}
                  >
                      {PAST_ORDER_REACTION_CONTROLS.map((control) => {
                        const isActive =
                          control.id === "dislike" ? isDisliked : isSelected;
                        const controlStyles = [
                          styles.prefControlButton,
                          control.id === "like" && styles.prefControlButtonLike,
                          control.id === "dislike" &&
                            styles.prefControlButtonDislike,
                          isActive && styles.prefControlButtonActive,
                          isActive &&
                            control.id === "like" &&
                            styles.prefControlButtonLikeActive,
                          isActive &&
                            control.id === "dislike" &&
                            styles.prefControlButtonDislikeActive,
                        ];
                        const iconStyles = [
                          styles.prefControlIcon,
                          control.id === "like" && styles.prefControlIconLike,
                          control.id === "dislike" &&
                            styles.prefControlIconDislike,
                          isActive && styles.prefControlIconActive,
                          isActive &&
                            control.id === "like" &&
                            styles.prefControlIconLikeActive,
                          isActive &&
                            control.id === "dislike" &&
                            styles.prefControlIconDislikeActive,
                        ];
                        const handleControlPress = (event) => {
                          event?.stopPropagation?.();
                          if (control.id === "dislike") {
                            handleToggleHomeMealDislike(meal.mealId);
                          } else if (control.id === "like") {
                            handleToggleHomeMealSelection(meal.mealId);
                          }
                        };
                        return (
                          <TouchableOpacity
                            key={`${meal?.mealId ?? "meal"}-${control.id}`}
                            style={controlStyles}
                            onPress={handleControlPress}
                            accessibilityRole="button"
                            accessibilityLabel={`${control.label} ${
                              meal?.name ?? ""
                            }`}
                          >
                            <Text style={iconStyles}>{control.icon}</Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                </View>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </View>
        {mealMenuOverlay}
        {confirmationDialogPortal}
        {homeMealDetailModal}
      </SafeAreaView>
    );
  }

  if (!isWelcomeComplete) {
    return (
      <SafeAreaView style={styles.welcomeSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.welcomeBody}>
          <ScrollView
            contentContainerStyle={[
              styles.welcomeScroll,
              { paddingBottom: FOOTER_PADDING },
            ]}
            showsVerticalScrollIndicator={false}
          >
            <View style={styles.welcomeHero}>
              <Image
                source={require("./assets/yummi-logo.png")}
                style={styles.welcomeLogo}
                resizeMode="contain"
              />
              <Text style={styles.welcomeTagline}>Your personal meal shopper</Text>
            </View>
            <View style={styles.welcomeCenter}>
              <View style={styles.usagePanel}>
                <View style={styles.freeUsesCard}>
                  <View style={styles.freeUsesValueStack}>
                    <Text style={[styles.freeUsesValueNumber, { fontSize: FREE_USES_FONT_SIZE }]}>10</Text>
                    <Text style={styles.freeUsesValueSuffix}>Free uses left</Text>
                  </View>
                </View>
                <View style={styles.shareSection}>
                  <TouchableOpacity
                    style={[styles.welcomeButton, styles.shareButton]}
                    onPress={handleShareForFreeUses}
                    activeOpacity={0.9}
                  >
                    <Text style={[styles.welcomeButtonText, styles.shareButtonText]}>
                      Give 10, get 10
                    </Text>
                    <Feather name="share-2" size={22} style={styles.shareButtonIcon} />
                  </TouchableOpacity>
                  <Text style={styles.shareSubline}>
                    Share with a friend, you both get 10 free uses.
                  </Text>
                  <Text style={styles.shareSubline}>
                    You always get 1 free use a month.
                  </Text>
                </View>
              </View>
            </View>
          </ScrollView>
          <View style={styles.welcomeFooter}>
            <View style={styles.welcomeButtonGroup}>
              <TouchableOpacity
                style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
                onPress={handleOpenPastOrders}
              >
                <Text style={styles.welcomeButtonText}>Past Orders</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
                onPress={() => setIsWelcomeComplete(true)}
              >
                <Text style={styles.welcomeButtonText}>Start Shopping!</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </SafeAreaView>
    );
  }

  if (isWelcomeComplete && !isPreferenceStateReady) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="dark" />
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#00a651" />
          <Text style={styles.preferencesLoadingText}>
            Preparing your preferences…
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  if (
    isOnboardingActive &&
    isWelcomeComplete &&
    !isPreferencesFlowComplete &&
    activePreferenceCategory
  ) {
    const progressTotal = preferenceCategories.length || 1;
    const progressPosition = Math.min(
      activePreferenceIndex + 1,
      progressTotal
    );
    const progressPercent = Math.min(
      100,
      (progressPosition / progressTotal) * 100
    );
    const isOnLastCategory =
      preferenceCategories.length > 0 &&
      activePreferenceIndex >= preferenceCategories.length - 1;
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={[styles.mealHomeHeader, styles.prefHeaderBar]}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handlePreferenceBack}
            accessibilityRole="button"
            accessibilityLabel={
              activePreferenceIndex > 0
                ? "Back to previous category"
                : "Exit preference reset"
            }
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <View style={[styles.prefProgressContainer, styles.prefProgressInline]}>
            <Text style={styles.prefProgressLabel}>
              {`Category ${progressPosition} of ${progressTotal}`}
            </Text>
            <View style={styles.prefProgressTrack}>
              <View
                style={[
                  styles.prefProgressFill,
                  { width: `${progressPercent}%` },
                ]}
              />
            </View>
          </View>
        </View>
        <View style={styles.preferencesWrapper}>
          <View style={styles.prefHeaderCard}>
            <Text style={styles.prefCategoryTitle}>
              {activePreferenceCategory.title}
            </Text>
            <Text style={styles.prefCategorySubtitle}>
              {activePreferenceCategory.description}
            </Text>
          </View>
          <ScrollView
            style={styles.prefScroll}
            contentContainerStyle={styles.prefScrollContent}
            showsVerticalScrollIndicator={false}
          >
            {activePreferenceCategory.tags.map((tag) => {
              const tagValue = getPreferenceValue(
                preferenceResponses,
                activePreferenceCategory.id,
                tag.id
              );
              const toggleSelectionState =
                isToggleCategory && activeToggleConfig
                  ? tag.id === activeToggleConfig.defaultTagId
                    ? activeToggleConfig.defaultState
                    : activeToggleConfig.selectionState
                  : null;
              const singleControlTargetState = isToggleCategory
                ? toggleSelectionState ?? "like"
                : "like";
              const isTagSelected =
                isSingleSelectCategory || isToggleCategory
                  ? tagValue === singleControlTargetState
                  : tagValue === "like";
              const renderSingleSelectControl = () => (
                <TouchableOpacity
                  style={[
                    styles.prefControlButton,
                    styles.prefControlButtonNeutral,
                    isTagSelected && styles.prefControlButtonActive,
                    isTagSelected && styles.prefControlButtonNeutralActive,
                  ]}
                    onPress={() =>
                      handlePreferenceSelection(
                        activePreferenceCategory.id,
                        tag.id,
                        singleControlTargetState
                      )
                    }
                  accessibilityRole="button"
                  accessibilityLabel={`Select ${tag.label}`}
                >
                  <Text
                    style={[
                      styles.prefControlIcon,
                      styles.prefControlIconNeutral,
                      isTagSelected && styles.prefControlIconActive,
                      isTagSelected && styles.prefControlIconNeutralActive,
                    ]}
                  >
                    ○
                  </Text>
                </TouchableOpacity>
              );
              return (
                <View key={tag.id} style={styles.prefTagCard}>
                  <View style={styles.prefTagTextGroup}>
                    <Text style={styles.prefTagLabel}>{tag.label}</Text>
                    {tag.helper ? (
                      <Text style={styles.prefTagHelper}>{tag.helper}</Text>
                    ) : null}
                  </View>
                  <View
                    style={
                      isSingleSelectCategory || isToggleCategory
                        ? styles.prefSingleSelectControls
                        : styles.prefControls
                    }
                  >
                    {isSingleSelectCategory || isToggleCategory
                      ? renderSingleSelectControl()
                      : PREFERENCE_CONTROL_STATES.map((control) => {
                          const isSelected = tagValue === control.id;
                          const controlStyles = [
                            styles.prefControlButton,
                            control.id === "like" &&
                              styles.prefControlButtonLike,
                            control.id === "dislike" &&
                              styles.prefControlButtonDislike,
                            control.id === "neutral" &&
                              styles.prefControlButtonNeutral,
                            isSelected && styles.prefControlButtonActive,
                            isSelected &&
                              control.id === "like" &&
                              styles.prefControlButtonLikeActive,
                            isSelected &&
                              control.id === "dislike" &&
                              styles.prefControlButtonDislikeActive,
                            isSelected &&
                              control.id === "neutral" &&
                              styles.prefControlButtonNeutralActive,
                          ];
                          return (
                            <TouchableOpacity
                              key={control.id}
                              style={controlStyles}
                              onPress={() =>
                                handlePreferenceSelection(
                                  activePreferenceCategory.id,
                                  tag.id,
                                  control.id
                                )
                              }
                              accessibilityRole="button"
                              accessibilityLabel={`${control.label} ${tag.label}`}
                            >
                              <Text
                                style={[
                                  styles.prefControlIcon,
                                  control.id === "like" &&
                                    styles.prefControlIconLike,
                                  control.id === "dislike" &&
                                    styles.prefControlIconDislike,
                                  control.id === "neutral" &&
                                    styles.prefControlIconNeutral,
                                  isSelected && styles.prefControlIconActive,
                                  isSelected &&
                                    control.id === "like" &&
                                    styles.prefControlIconLikeActive,
                                  isSelected &&
                                    control.id === "dislike" &&
                                    styles.prefControlIconDislikeActive,
                                  isSelected &&
                                    control.id === "neutral" &&
                                    styles.prefControlIconNeutralActive,
                                ]}
                              >
                                {control.icon}
                              </Text>
                            </TouchableOpacity>
                          );
                        })}
                  </View>
                </View>
              );
            })}
          </ScrollView>
        </View>
        <View style={styles.prefFooter}>
          <Text style={styles.prefFooterHint}>
            {isSingleSelectCategory
              ? activeCategoryRatingsCount === 0
                ? "Pick the option that best fits—only one can be active."
                : "Tap another option to switch who you’re feeding anytime."
              : isToggleCategory
              ? hasOnlyToggleDefaultActive
                ? `${toggleDefaultLabel ?? "The default option"} is pre-selected—add any options that apply.`
                : `Choose as many as you like. Selecting "${toggleDefaultLabel ?? "the default option"}" clears the others.`
              : activeCategoryRatingsCount === 0
              ? "Neutral is the default—tap to highlight likes or dislikes."
              : "Adjust anything you like now or later in Settings."}
          </Text>
          <TouchableOpacity
            style={styles.prefContinueButton}
            onPress={handlePreferenceContinue}
          >
            <Text style={styles.prefContinueButtonText}>
              {isOnLastCategory ? "Update Preferences" : "Next"}
            </Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (isOnboardingActive && isWelcomeComplete && !isPreferencesFlowComplete) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="dark" />
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#00a651" />
        </View>
      </SafeAreaView>
    );
  }

  if (shouldShowPreferenceCompletionScreen) {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.mealHomeHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handlePreferenceCompleteBack}
            accessibilityRole="button"
            accessibilityLabel="Back to categories"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.mealHomeMenuButton}
            onPress={toggleMealMenu}
            accessibilityRole="button"
            accessibilityLabel="Open menu"
          >
            <Feather name="menu" size={24} color="#0c3c26" />
          </TouchableOpacity>
        </View>
        <View style={styles.prefCompleteContent}>
          <View style={styles.prefCompleteInfoCard}>
            <View style={styles.prefCompleteIconCircle}>
              <Feather name="check" size={28} color="#ffffff" />
            </View>
            <View style={styles.prefCompleteTextGroup}>
              <Text style={[styles.prefCompleteHeadline, { fontSize: headlineFontSize }]}>Preferences Saved!</Text>
              <Text style={styles.prefCompleteSubheadline}>
                Why do I have to pay for new meals?
              </Text>
              <Text style={[styles.prefCompleteExplanation, { maxWidth: cardMaxWidth }]}>
                Each time you ask for a fresh set of meals, the app has to do extra work to build it. The small fee helps us cover that cost and prevent people from over-using the system.
              </Text>
              <Text style={[styles.prefCompleteExplanation, { maxWidth: cardMaxWidth }]}>
                You’ll still get fresh meal plans automatically whenever you use the “Get Shopping List” or “Add to Woolworths Cart” buttons.
              </Text>
            </View>
          </View>
        </View>
        <View style={styles.flexSpacer} />
        <View style={[styles.prefFooter, styles.prefFooterFixed, { paddingBottom: 16 + insets.bottom }]}>
          <View style={styles.ingredientsButtonGroup}>
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={handleOpenNewMealsConfirm}
            >
              <Text style={styles.welcomeButtonText}>
                Get New Meals (Use a free use)
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={handleCompleteOnboardingFlow}
            >
              <Text style={styles.welcomeButtonText}>Back to Shopping</Text>
            </TouchableOpacity>
          </View>
        </View>
        {mealMenuOverlay}
        {confirmationDialogPortal}
      </SafeAreaView>
    );
  }

  if (isSorryToHearScreenVisible) {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.mealHomeHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handleCloseSorryToHearScreen}
            accessibilityRole="button"
            accessibilityLabel="Back to home"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.mealHomeMenuButton}
            onPress={toggleMealMenu}
            accessibilityRole="button"
            accessibilityLabel="Open menu"
          >
            <Feather name="menu" size={24} color="#0c3c26" />
          </TouchableOpacity>
        </View>
        <View style={styles.prefCompleteContent}>
          <View style={styles.prefCompleteInfoCard}>
            <View style={styles.prefCompleteIconCircle}>
              <Feather name="frown" size={28} color="#ffffff" />
            </View>
            <View style={styles.prefCompleteTextGroup}>
              <Text style={[styles.prefCompleteHeadline, { fontSize: headlineFontSize }]}>Sorry to hear that</Text>
              <Text style={[styles.prefCompleteExplanation, { maxWidth: cardMaxWidth }]}>
                You’ll always get fresh meal plans when you use the “Get Shopping List” or “Add to Woolworths Cart” buttons.
              </Text>
              <Text style={[styles.prefCompleteExplanation, { maxWidth: cardMaxWidth }]}>
                Don’t like what you see?
              </Text>
              <Text style={[styles.prefCompleteExplanation, { maxWidth: cardMaxWidth }]}>1. Update your preferences</Text>
              <Text style={[styles.prefCompleteExplanation, { maxWidth: cardMaxWidth }]}>2. Get new meals</Text>
            </View>
          </View>
        </View>
        <View style={styles.flexSpacer} />
        <View style={styles.prefFooter}>
          <View style={styles.ingredientsButtonGroup}>
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={handleSorryScreenUpdatePreferences}
            >
              <Text style={styles.welcomeButtonText}>
                Update Preferences
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={handleCloseSorryToHearScreen}
            >
              <Text style={styles.welcomeButtonText}>Back to Shopping</Text>
            </TouchableOpacity>
          </View>
        </View>
        {mealMenuOverlay}
        {confirmationDialogPortal}
      </SafeAreaView>
    );
  }

  if (
    isOnboardingActive &&
    isPreferencesFlowComplete &&
    hasAcknowledgedPreferenceComplete &&
    !hasSeenExplorationResults &&
    (explorationState === "idle" || explorationState === "running")
  ) {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.explorationWrapper}>
          <ActivityIndicator size="large" color="#00a651" />
          <Text style={styles.explorationProcessingTitle}>
            Crafting your starter list…
          </Text>
          <Text style={styles.explorationProcessingSubtitle}>
            We’re running your preferences through our chef AI to gather ten meals. This usually takes 10–15 seconds.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  if (screen === "buildingShoppingList") {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.explorationWrapper}>
          <ActivityIndicator size="large" color="#00a651" />
          <Text style={styles.explorationProcessingTitle}>
            Building your shopping list…
          </Text>
          <Text style={styles.explorationProcessingSubtitle}>
            We're gathering the ingredients and quantities you selected. This usually takes just a few seconds.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  if (screen === "buildingRecommendations") {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.explorationWrapper}>
          <ActivityIndicator size="large" color="#00a651" />
          <Text style={styles.explorationProcessingTitle}>
            Generating your recommendations…
          </Text>
          <Text style={styles.explorationProcessingSubtitle}>
            We're sending your likes and dislikes to the chef AI before we surface your next meals. This usually takes 10–15 seconds.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  if (
    isOnboardingActive &&
    isPreferencesFlowComplete &&
    hasAcknowledgedPreferenceComplete &&
    !hasSeenExplorationResults &&
    explorationState === "error"
  ) {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.explorationWrapper}>
          <Text style={styles.prefCategoryTitle}>We hit a snag</Text>
          <Text style={styles.prefCategorySubtitle}>
            {explorationError ??
              "Something went wrong while preparing your recipes. Please try again."}
          </Text>
          <TouchableOpacity
            style={[styles.prefContinueButton, { marginTop: 24 }]}
            onPress={handleRetryExploration}
          >
            <Text style={styles.prefContinueButtonText}>Try again</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.prefRedoButton}
            onPress={handleResetPreferencesFlow}
          >
            <Text style={styles.prefRedoButtonText}>
              Adjust preferences
            </Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (
    isOnboardingActive &&
    isPreferencesFlowComplete &&
    hasAcknowledgedPreferenceComplete &&
    !hasSeenExplorationResults &&
    explorationState === "ready"
  ) {
    if (USE_MEAL_CARD_EXPLORATION_UI) {
      return (
        <SafeAreaView style={styles.mealHomeSafeArea}>
          <StatusBar style="dark" />
          <View style={styles.explorationMealCardScreen}>
            <View style={styles.explorationMealTipCard}>
              <Text style={styles.explorationMealTipTitle}>Help us learn</Text>
              <Text style={styles.explorationMealTipText}>
                👍 Like: We’ll show similar meals more often.
              </Text>
              <Text style={styles.explorationMealTipText}>
                ○ Neutral: Skip it for now without affecting your lineup.
              </Text>
              <Text style={styles.explorationMealTipText}>
                👎 Dislike: We’ll avoid meals like this.
              </Text>
            </View>
            <ScrollView
              style={styles.explorationMealCardScroll}
              contentContainerStyle={styles.explorationMealCardList}
              showsVerticalScrollIndicator={false}
            >
              {explorationMeals.length > 0 ? (
                explorationMeals.map((meal, index) =>
                  renderExplorationMealCard(meal, index)
                )
              ) : (
                <Text style={styles.mealRecommendationsPlaceholder}>
                  Starter meals will appear here momentarily.
                </Text>
              )}
            </ScrollView>
            <View style={styles.explorationMealCardFooter}>
              <TouchableOpacity
                style={[
                  styles.prefContinueButton,
                  isCompletingExploration && styles.prefContinueButtonDisabled,
                ]}
                onPress={handleConfirmExplorationReview}
                disabled={isCompletingExploration}
              >
                {isCompletingExploration ? (
                  <ActivityIndicator color="#ffffff" />
                ) : (
                  <Text style={styles.prefContinueButtonText}>Continue</Text>
                )}
              </TouchableOpacity>
            </View>
          </View>
        </SafeAreaView>
      );
    }

  }

  if (
    screen === "home" &&
    isMealHomeSurface &&
    isWelcomeComplete &&
    isPreferenceStateReady &&
    !isOnboardingActive
  ) {
    return (
      <SafeAreaView style={styles.mealHomeSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.mealHomeHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handleReturnToWelcome}
            accessibilityRole="button"
            accessibilityLabel="Back to welcome"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.mealHomeMenuButton}
            onPress={toggleMealMenu}
            accessibilityRole="button"
            accessibilityLabel="Open menu"
          >
            <Feather name="menu" size={24} color="#0c3c26" />
          </TouchableOpacity>
        </View>
        <View style={styles.mealHomeBody}>
          <View style={styles.mealHomeTipContainer}>
            <Text style={styles.mealHomeTipText}>Tap meal card to see details.</Text>
          </View>
          <ScrollView
            style={styles.mealRecommendationsScroll}
            contentContainerStyle={styles.mealRecommendationsContent}
            showsVerticalScrollIndicator={false}
          >
            {displayedMeals.length > 0 ? (
              <>
                {displayedMeals.map((meal) => {
                  const isSelected = Boolean(selectedHomeMealIds[meal.mealId]);
                  const isDisliked = Boolean(homeMealDislikedIds[meal.mealId]);
                  const servingsCount = deriveMealServingsCount(meal);
                  const servingsLabel = formatServingsPeopleLabel(servingsCount);
                  return (
                    <TouchableOpacity
                      key={meal.mealId}
                      style={styles.homeMealCard}
                      activeOpacity={0.9}
                      onPress={() =>
                        setHomeMealModal({ visible: true, meal })
                      }
                    >
                      <Text style={styles.homeMealTitle}>
                        {meal.name ?? "Meal"}
                      </Text>
                      {meal.description ? (
                        <Text style={styles.homeMealDescription}>
                          {meal.description}
                        </Text>
                      ) : null}
                      {Array.isArray(meal.tags?.PrepTime) &&
                      meal.tags.PrepTime.length > 0 ? (
                        <Text style={styles.homeMealPrepTime}>
                          Prep time: {meal.tags.PrepTime.join(", ")}
                        </Text>
                      ) : null}
                      <View style={styles.homeMealFooterRow}>
                        <View style={styles.homeMealServingsRow}>
                          <Text style={styles.homeMealServingsLabel}>Servings:</Text>
                          <Text style={styles.homeMealServingsValueText}>
                            {servingsLabel}
                          </Text>
                        </View>
                        <View style={styles.homeMealActionGroup}>
                          <TouchableOpacity
                            style={[
                              styles.homeMealDislikeButton,
                              isDisliked && styles.homeMealDislikeButtonActive,
                              isDisliked && styles.prefControlButtonActive,
                            ]}
                            onPress={(event) => {
                              event?.stopPropagation?.();
                              handleToggleHomeMealDislike(meal.mealId);
                            }}
                            accessibilityRole="button"
                            accessibilityLabel={
                              isDisliked ? "Undo dislike" : "Mark meal as disliked"
                            }
                          >
                            <Text
                              style={[
                                styles.homeMealDislikeButtonIcon,
                                isDisliked && styles.homeMealDislikeButtonIconActive,
                              ]}
                            >
                              👎
                            </Text>
                          </TouchableOpacity>
                          <TouchableOpacity
                            style={[
                              styles.homeMealChooseButton,
                              isSelected && styles.homeMealChooseButtonActive,
                              isSelected && styles.prefControlButtonActive,
                            ]}
                            onPress={(event) => {
                              event?.stopPropagation?.();
                              handleToggleHomeMealSelection(meal.mealId);
                            }}
                            accessibilityRole="button"
                            accessibilityLabel={
                              isSelected ? "Deselect meal" : "Select meal"
                            }
                          >
                            <Text
                              style={[
                                styles.homeMealChooseButtonText,
                                isSelected && styles.homeMealChooseButtonTextActive,
                              ]}
                            >
                              Select
                            </Text>
                          </TouchableOpacity>
                        </View>
                      </View>
                    </TouchableOpacity>
                  );
                })}
                <TouchableOpacity
                  style={styles.homeNoInterestButton}
                  onPress={handleOpenSorryToHearScreen}
                  accessibilityRole="button"
                  accessibilityLabel="None of these meals interest me"
                >
                  <Text style={styles.homeNoInterestButtonText}>
                    There is nothing I like
                  </Text>
                </TouchableOpacity>
              </>
            ) : (
              <Text style={styles.mealRecommendationsPlaceholder}>
                Recommended meals will appear here.
              </Text>
            )}
          </ScrollView>
          <TouchableOpacity
            style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
            onPress={handleOpenShoppingListConfirm}
          >
            <Text style={styles.welcomeButtonText}>Build Shopping List</Text>
          </TouchableOpacity>
        </View>
        {mealMenuOverlay}
        {confirmationDialogPortal}
        {homeMealDetailModal}
      </SafeAreaView>
    );
  }

  if (screen === "ingredients") {
    return (
      <SafeAreaView style={styles.mealHomeSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.mealHomeHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handleIngredientsBackToHome}
            accessibilityRole="button"
            accessibilityLabel="Back to home"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.mealHomeMenuButton}
            onPress={toggleMealMenu}
            accessibilityRole="button"
            accessibilityLabel="Open menu"
          >
            <Feather name="menu" size={24} color="#0c3c26" />
          </TouchableOpacity>
        </View>
        <View style={styles.ingredientsBody}>
          <View style={styles.mealHomeTipContainer}>
            <Text style={styles.mealHomeTipText}>Tap product card to see details.</Text>
          </View>
          <ScrollView
            style={styles.ingredientsList}
            contentContainerStyle={styles.ingredientsListContent}
            showsVerticalScrollIndicator={false}
          >
            {shoppingListStatus === "pending" ? (
              <View style={styles.ingredientsEmptyState}>
                <ActivityIndicator size="large" color="#0c3c26" />
                <Text style={styles.ingredientsEmptyText}>We're preparing your shopping list...</Text>
              </View>
            ) : shoppingListError ? (
              <View style={styles.ingredientsEmptyState}>
                <Text style={styles.ingredientsEmptyText}>{shoppingListError}</Text>
                <TouchableOpacity
                  style={styles.ingredientsRetryButton}
                  onPress={() =>
                    handleOpenShoppingListConfirm({
                      triggerLearning: true,
                      nextScreen: "shoppingList",
                    })
                  }
                >
                  <Text style={styles.ingredientsRetryButtonText}>Try again</Text>
                </TouchableOpacity>
              </View>
            ) : stapleIngredients.length > 0 || primaryIngredients.length > 0 ? (
              <>
                {stapleIngredients.length > 0 ? (
                  <View style={styles.ingredientsStapleSection}>
                    <View style={styles.ingredientsStapleNotice}>
                      <Text style={styles.ingredientsStapleTitle}>Likely already in your pantry</Text>
                      <Text style={styles.ingredientsStapleDescription}>
                        We think you already have these staples. Increase the quantity if you need to restock.
                      </Text>
                    </View>
                    {stapleIngredients.map((ingredient, index) =>
                      renderIngredientRow(ingredient, `staple-${index}`)
                    )}
                    <View style={styles.ingredientsPrimaryNotice}>
                      <Text style={styles.ingredientsPrimaryNoticeTitle}>Need to pick these up?</Text>
                      <Text style={styles.ingredientsPrimaryNoticeDescription}>
                        The rest of the list comes from your selected meals and usually needs a store run.
                      </Text>
                    </View>
                  </View>
                ) : null}
                {primaryIngredients.length > 0 ? (
                  <View style={styles.ingredientsPrimarySection}>
                    {primaryIngredients.map((ingredient, index) =>
                      renderIngredientRow(ingredient, `primary-${index}`)
                    )}
                  </View>
                ) : null}
              </>
            ) : (
              <View style={styles.ingredientsEmptyState}>
                <Text style={styles.ingredientsEmptyText}>
              {shoppingListStatus === "ready"
                ? "Your selected meals don't require any new ingredients."
                : selectedHomeMeals.length
                ? "Tap Build Shopping List on the previous screen to prepare your shopping list."
                : "Select meals on the previous screen to see their ingredients here."}
            </Text>
              </View>
            )}
          </ScrollView>
          {shoppingListItems.length > 0 ? (
            <View style={styles.ingredientsSummaryBar}>
              <View style={styles.ingredientsSummaryTextGroup}>
                <Text style={styles.ingredientsSummaryLabel}>Estimated basket total</Text>
                <Text style={styles.ingredientsSummaryCaption}>
                  {shoppingListPricing.hasAnyPrice
                    ? "Based on Woolworths pricing"
                    : "Pricing unavailable at the moment"}
                </Text>
              </View>
              <Text style={styles.ingredientsSummaryValue}>
                {shoppingListPricing.hasAnyPrice
                  ? formatCurrency(shoppingListPricing.basketTotalMinor)
                  : "—"}
              </Text>
            </View>
          ) : null}
          <View style={styles.ingredientsButtonGroup}>
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={handleIngredientsShoppingListNotice}
            >
              <Text style={styles.welcomeButtonText}>Get Shopping List</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[
                styles.welcomeButton,
                styles.mealHomeCtaButton,
                styles.welcomeCtaButton,
                (!canSendShoppingListToCart || isCartPushPending) && styles.disabledButton,
              ]}
              onPress={handleOpenWoolworthsCartConfirm}
              disabled={!canSendShoppingListToCart || isCartPushPending}
            >
              <Text style={styles.welcomeButtonText}>
                {isCartPushPending
                  ? "Sending to Woolworths..."
                  : "Add to Woolworths Cart"}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
        {mealMenuOverlay}
        {confirmationDialogPortal}
      </SafeAreaView>
    );
  }

  if (screen === "shoppingList") {
    return (
      <SafeAreaView style={styles.shoppingListSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.shoppingListHeader}>
          <TouchableOpacity
            style={styles.mealHomeBackButton}
            onPress={handleReturnToIngredients}
            accessibilityRole="button"
            accessibilityLabel="Back to ingredients"
          >
            <Feather name="arrow-left" size={24} color="#00a651" />
          </TouchableOpacity>
          <Text style={styles.shoppingListHeaderTitle}>Shopping List</Text>
          <View style={styles.shoppingListHeaderAction} />
        </View>
        <View style={styles.shoppingListBody}>
          {shoppingListDisplayItems.length === 0 ? (
            <View style={styles.shoppingListEmptyState}>
              <Text style={styles.shoppingListEmptyTitle}>No shopping list yet</Text>
              <Text style={styles.shoppingListEmptySubtitle}>
                Build a shopping list from the ingredients screen to see it here.
              </Text>
            </View>
          ) : (
            <ScrollView
              style={styles.shoppingListScroll}
              contentContainerStyle={styles.shoppingListScrollContent}
              showsVerticalScrollIndicator={false}
            >
              {shoppingListDisplayItems.map((item) => {
                const placeholderInitial = (item.displayName ?? "?")
                  .trim()
                  .charAt(0)
                  .toUpperCase();
                const isChecked = checkedShoppingListItems.has(item.id);
                return (
                  <TouchableOpacity
                    key={item.id}
                    style={[
                      styles.shoppingListItem,
                      isChecked && styles.shoppingListItemChecked,
                    ]}
                    onPress={() => handleToggleShoppingListItem(item.id)}
                    activeOpacity={0.7}
                  >
                    <View style={styles.shoppingListItemImageWrapper}>
                      {item.imageUrl ? (
                        <Image
                          key={`shopping-list-image-${item.id}-${imageReloadCounters[item.id] ?? 0}`}
                          source={{
                            uri: item.imageUrl,
                            cache: "reload",
                          }}
                          style={styles.shoppingListItemImage}
                          onError={() => scheduleImageReload(item.id)}
                        />
                      ) : (
                        <View style={styles.shoppingListItemImagePlaceholder}>
                          <Text style={styles.shoppingListItemImagePlaceholderText}>
                            {placeholderInitial || "?"}
                          </Text>
                        </View>
                      )}
                    </View>
                    <View style={styles.shoppingListItemBody}>
                      <Text
                        style={[
                          styles.shoppingListItemName,
                          isChecked && styles.shoppingListItemNameChecked,
                        ]}
                      >
                        {item.displayName}
                      </Text>
                      <Text
                        style={[
                          styles.shoppingListItemQuantity,
                          isChecked && styles.shoppingListItemQuantityChecked,
                        ]}
                      >
                        Quantity: {item.displayQuantity}
                      </Text>
                    </View>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          )}
        </View>
        <View style={styles.shoppingListFooter}>
          <TouchableOpacity
            style={[
              styles.welcomeButton,
              styles.mealHomeCtaButton,
              styles.welcomeCtaButton,
              styles.shoppingListHomeButton,
            ]}
            onPress={handleConfirmReturnHome}
          >
            <Text style={styles.welcomeButtonText}>Done</Text>
          </TouchableOpacity>
        </View>
        {mealMenuOverlay}
        {confirmationDialogPortal}
      </SafeAreaView>
    );
  }

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
    const itemsProgress = `${runnerState.processed}/${runnerState.total} items • OK ${runnerState.ok} • Failed ${runnerState.failed}`;

    return (
      <SafeAreaView style={styles.mealHomeSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.checkoutHeader}>
          <Text style={styles.checkoutTitle}>Woolworths Checkout</Text>
          <Text style={styles.checkoutSubtitle}>Hang tight—your basket is loading.</Text>
        </View>
        <View style={styles.checkoutStatusCard}>
          <Text style={styles.checkoutStatusLabel}>{stageLabel}</Text>
          <Text style={styles.checkoutStatusMeta}>{itemsProgress}</Text>
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
              <Text style={styles.overlayTitle}>Placing items in your cart…</Text>
              <Text style={styles.overlaySubtitle}>
                Keep this window open while we finish up.
              </Text>
            </View>
          ) : null}
        </View>
        <View style={styles.footer}>
          <TouchableOpacity
            style={styles.secondaryButton}
            onPress={() => {
              setActiveOrder(null);
              handleReturnToWelcome();
            }}
          >
            <Text style={styles.secondaryButtonText}>Finish</Text>
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
        {!isMealHomeSurface ? (
          <TouchableOpacity
            style={styles.mealHomeSwitchButton}
            onPress={handleReturnToMealHome}
          >
            <Text style={styles.mealHomeSwitchText}>Go to meal home</Text>
          </TouchableOpacity>
        ) : null}
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
          {payfastMonitor ? (
            <View
              style={[
                styles.payfastStatusCard,
                isPayfastTrackerDone(payfastMonitor.status, payfastMonitor.walletCredited)
                  ? styles.payfastStatusCardDone
                  : null,
              ]}
            >
              <View style={styles.payfastStatusHeader}>
                <Text style={styles.payfastStatusTitle}>PayFast Status</Text>
                <TouchableOpacity onPress={() => setPayfastMonitor(null)}>
                  <Text style={styles.payfastStatusDismiss}>Dismiss</Text>
                </TouchableOpacity>
              </View>
              <Text style={styles.payfastStatusValue}>
                {formatPayfastStatus(payfastMonitor.status)}
                {payfastMonitor.walletCredited ? " • Wallet credited" : ""}
              </Text>
              {payfastMonitor.message ? (
                <Text style={styles.payfastStatusMessage}>{payfastMonitor.message}</Text>
              ) : null}
              <Text style={styles.payfastStatusMeta}>
                Ref {shortReference(payfastMonitor.reference)}
                {payfastMonitor.lastChecked
                  ? ` • Checked ${formatTime(new Date(payfastMonitor.lastChecked))}`
                  : ""}
              </Text>
              {payfastMonitor.error ? (
                <Text style={styles.walletErrorText}>{payfastMonitor.error}</Text>
              ) : null}
            </View>
          ) : null}
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
    <SafeAreaProvider>
      <ClerkProvider publishableKey={publishableKey} tokenCache={clerkTokenCache}>
        <SignedIn>
          <AppContent />
        </SignedIn>
        <SignedOut>
          <SignedOutScreen />
        </SignedOut>
      </ClerkProvider>
    </SafeAreaProvider>
  );
}

// Centralized shadow tokens for consistent elevation across the app
const SHADOW = {
  button: {
    shadowColor: "#000",
    shadowOpacity: 0.2,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },
  card: {
    shadowColor: "#1c3d2d",
    shadowOpacity: 0.06,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 2,
  },
  cardLg: {
    shadowColor: "#1c3d2d",
    shadowOpacity: 0.08,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 10 },
    elevation: 4,
  },
  none: {},
};

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f5f5f5",
  },
  welcomeSafeArea: {
    flex: 1,
    backgroundColor: "#f4f9f5",
  },
  welcomeBody: {
    flex: 1,
    paddingBottom: 0,
    justifyContent: "space-between",
  },
  mealHomeSafeArea: {
    flex: 1,
    backgroundColor: "#f4f9f5",
  },
  mealHomeHeader: {
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 8,
    width: "100%",
    backgroundColor: "transparent",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    height: 64,
  },
  prefHeaderBar: {
    justifyContent: "flex-start",
  },
  mealHomeBackButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: "#d3d3d3",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#fff",
  },
  mealHomeTitle: {
    fontSize: 32,
    fontWeight: "700",
    color: "#0c3c26",
    marginBottom: 8,
  },
  mealHomeBody: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 16,
    gap: 12,
  },
  mealHomeTipContainer: {
    width: "100%",
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 18,
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    ...SHADOW.card,
  },
  mealHomeTipText: {
    fontSize: 15,
    fontWeight: "600",
    color: "#1f4b35",
    textAlign: "center",
  },
  pastOrderInstructionWrapper: {
    paddingHorizontal: 20,
    marginTop: 12,
  },
  pastOrderInstructionCard: {
    width: "100%",
    paddingVertical: 12,
    paddingHorizontal: 20,
    borderRadius: 18,
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
    ...SHADOW.card,
  },
  pastOrderInstructionText: {
    fontSize: 14,
    color: "#0c3c26",
    textAlign: "center",
    marginBottom: 4,
  },
  pastOrdersScroll: {
    flex: 1,
    width: "100%",
  },
  pastOrdersContent: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 24,
    gap: 12,
  },
  pastOrderCard: {
    backgroundColor: "#ffffff",
    borderRadius: 20,
    padding: 16,
    gap: 4,
    ...SHADOW.card,
  },
  pastOrderCardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  pastOrderCardDateWrapper: {
    alignItems: "flex-end",
    gap: 4,
  },
  pastOrderDeleteButton: {
    padding: 4,
  },
  pastOrderActionControls: {
    marginLeft: "auto",
  },
  pastOrderCardDay: {
    fontSize: 16,
    fontWeight: "700",
    color: "#0c3c26",
  },
  pastOrderCardDate: {
    fontSize: 14,
    color: "#4d4d4d",
  },
  pastOrderCardMeta: {
    fontSize: 14,
    color: "#0c3c26",
    fontWeight: "600",
  },
  pastOrderFooter: {
    flexDirection: "row",
    justifyContent: "flex-end",
    marginTop: 12,
  },
  pastOrdersEmptyState: {
    flex: 1,
    paddingTop: 32,
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  pastOrdersEmptyTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: "#0c3c26",
  },
  pastOrdersEmptySubtitle: {
    fontSize: 14,
    color: "#4d4d4d",
    textAlign: "center",
  },
  pastOrderDetailMeta: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 4,
    gap: 4,
  },
  pastOrderDetailMetaText: {
    fontSize: 14,
    color: "#4d4d4d",
  },
  pastOrderMealsContainer: {
    flex: 1,
    width: "100%",
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 16,
  },
  pastOrderMealsScroll: {
    flex: 1,
    width: "100%",
  },
  pastOrderMealsContent: {
    paddingBottom: 24,
    gap: 12,
  },
  ingredientsBody: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 16,
    gap: 16,
  },
  ingredientsList: {
    flex: 1,
  },
  ingredientsListContent: {
    paddingBottom: 8,
  },
  ingredientsStapleSection: {
    marginBottom: 20,
  },
  ingredientsStapleNotice: {
    backgroundColor: "#f2f7f4",
    borderRadius: 16,
    padding: 14,
    marginBottom: 12,
    ...SHADOW.card,
  },
  ingredientsStapleTitle: {
    fontSize: 16,
    fontWeight: "600",
    color: "#0c3c26",
  },
  ingredientsStapleDescription: {
    fontSize: 13,
    color: "#4d4d4d",
  },
  ingredientsPrimaryNotice: {
    backgroundColor: "#f0f5ff",
    borderRadius: 16,
    padding: 14,
    marginTop: 4,
    marginBottom: 12,
    ...SHADOW.card,
  },
  ingredientsPrimaryNoticeTitle: {
    fontSize: 15,
    fontWeight: "600",
    color: "#0c3c26",
  },
  ingredientsPrimaryNoticeDescription: {
    fontSize: 13,
    color: "#4d4d4d",
  },
  ingredientsPrimarySection: {},
  ingredientsListItem: {
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 14,
    marginBottom: 10,
    gap: 12,
    ...SHADOW.card,
  },
  ingredientsListItemManual: {
    borderWidth: 1,
    borderColor: "#fca5a5",
  },
  ingredientsListItemRow: {
    flexDirection: "row",
    alignItems: "stretch",
    gap: 12,
  },
  ingredientsItemImageWrapper: {
    width: 72,
    height: 72,
    borderRadius: 12,
    overflow: "hidden",
    backgroundColor: "#f1f4f2",
    alignItems: "center",
    justifyContent: "center",
  },
  ingredientsItemImage: {
    width: "100%",
    height: "100%",
    resizeMode: "cover",
  },
  ingredientsItemImagePlaceholder: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  ingredientsItemImagePlaceholderText: {
    fontSize: 18,
    fontWeight: "600",
    color: "#6c7a70",
  },
  ingredientsItemBody: {
    flex: 1,
    gap: 8,
  },
  ingredientsItemHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  ingredientsItemText: {
    fontSize: 14,
    color: "#1c1c1c",
    flexShrink: 1,
  },
  ingredientsItemUnitPrice: {
    fontSize: 13,
    color: "#4d4d4d",
  },
  ingredientsManualNotice: {
    padding: 10,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#fecdd3",
    backgroundColor: "#fff1f2",
  },
  ingredientsManualNoticeText: {
    fontSize: 12,
    lineHeight: 16,
    color: "#9f1239",
  },
  ingredientsItemLineTotal: {
    fontSize: 15,
    fontWeight: "600",
    color: "#0c3c26",
  },
  ingredientsQuantityRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 6,
  },
  ingredientsQuantityButton: {
    width: 32,
    height: 32,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#cfd8d2",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#f7f9f8",
  },
  ingredientsQuantityButtonDisabled: {
    opacity: 0.4,
  },
  ingredientsQuantityButtonText: {
    fontSize: 16,
    color: "#0c3c26",
    fontWeight: "600",
  },
  ingredientsQuantityButtonTextDisabled: {
    color: "#6f7c72",
  },
  ingredientsQuantityValue: {
    minWidth: 40,
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 10,
    backgroundColor: "#e8f3ed",
    alignItems: "center",
    justifyContent: "center",
  },
  ingredientsQuantityValueText: {
    fontSize: 14,
    fontWeight: "600",
    color: "#0c3c26",
  },
  ingredientsEmptyState: {
    paddingVertical: 48,
    paddingHorizontal: 12,
    alignItems: "center",
  },
  ingredientsEmptyText: {
    fontSize: 14,
    color: "#4d4d4d",
    textAlign: "center",
  },
  ingredientsRetryButton: {
    marginTop: 16,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 999,
    backgroundColor: "#0c3c26",
  },
  ingredientsRetryButtonText: {
    color: "#fff",
    fontWeight: "600",
  },
  ingredientsSummaryBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    padding: 16,
    marginTop: 12,
    marginBottom: 8,
    borderRadius: 16,
    backgroundColor: "#f7f9f8",
    gap: 12,
    ...SHADOW.card,
  },
  ingredientsSummaryTextGroup: {
    flexShrink: 1,
  },
  ingredientsSummaryLabel: {
    fontSize: 14,
    fontWeight: "600",
    color: "#0c3c26",
  },
  ingredientsSummaryCaption: {
    fontSize: 12,
    color: "#4d4d4d",
    marginTop: 2,
  },
  ingredientsSummaryValue: {
    fontSize: 20,
    fontWeight: "700",
    color: "#0c3c26",
  },
  ingredientsButtonGroup: {
    paddingTop: 8,
    gap: 12,
    alignSelf: "stretch",
    width: "100%",
  },
  shoppingListSafeArea: {
    flex: 1,
    backgroundColor: "#f4f9f5",
  },
  shoppingListHeader: {
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  shoppingListHeaderTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: "#0c3c26",
  },
  shoppingListHeaderSubtitle: {
    fontSize: 12,
    color: "#4d4d4d",
    marginTop: 4,
  },
  shoppingListHeaderAction: {
    width: 44,
    height: 44,
  },
  shoppingListBody: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 16,
  },
  shoppingListEmptyState: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12,
  },
  shoppingListEmptyTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: "#0c3c26",
    textAlign: "center",
    marginBottom: 6,
  },
  shoppingListEmptySubtitle: {
    fontSize: 14,
    color: "#4d4d4d",
    textAlign: "center",
  },
  shoppingListScroll: {
    flex: 1,
  },
  shoppingListScrollContent: {
    paddingVertical: 12,
    paddingBottom: 24,
  },
  shoppingListItem: {
    width: "100%",
    flexDirection: "row",
    alignItems: "center",
    padding: 14,
    borderRadius: 16,
    backgroundColor: "#fff",
    marginBottom: 12,
    ...SHADOW.card,
  },
  shoppingListItemChecked: {
    backgroundColor: "#e6f5ed",
  },
  shoppingListItemImageWrapper: {
    width: 56,
    height: 56,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#eef5ef",
  },
  shoppingListItemImage: {
    width: "100%",
    height: "100%",
    borderRadius: 14,
    resizeMode: "cover",
  },
  shoppingListItemImagePlaceholder: {
    width: "100%",
    height: "100%",
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#dfe7e1",
  },
  shoppingListItemImagePlaceholderText: {
    fontSize: 18,
    fontWeight: "700",
    color: "#0c3c26",
  },
  shoppingListItemBody: {
    flex: 1,
    justifyContent: "center",
    marginLeft: 10,
  },
  shoppingListItemName: {
    fontSize: 16,
    fontWeight: "600",
    color: "#0c3c26",
    marginBottom: 4,
  },
  shoppingListItemNameChecked: {
    color: "#4b7a5d",
    textDecorationLine: "line-through",
  },
  shoppingListItemQuantity: {
    fontSize: 14,
    color: "#4d4d4d",
  },
  shoppingListItemQuantityChecked: {
    color: "#4b7a5d",
    textDecorationLine: "line-through",
  },
  shoppingListFooter: {
    paddingHorizontal: 20,
    paddingBottom: 16,
    paddingTop: 6,
  },
  shoppingListHomeButton: {
    borderRadius: 14,
  },
  mealHomeMenuButton: {
    paddingVertical: 10,
    paddingHorizontal: 10,
    minWidth: 44,
    minHeight: 44,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#d3d3d3",
    backgroundColor: "#fff",
  },
  mealMenuContainer: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 20,
  },
  mealMenuBackdrop: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  mealMenuCard: {
    position: "absolute",
    top: 120,
    right: 24,
    backgroundColor: "#fff",
    borderRadius: 18,
    paddingVertical: 6,
    minWidth: 220,
    ...SHADOW.cardLg,
  },
  mealMenuItem: {
    paddingHorizontal: 18,
    paddingVertical: 14,
  },
  mealMenuItemText: {
    fontSize: 16,
    fontWeight: "600",
    color: "#0c3c26",
  },
  mealMenuItemDanger: {
    color: "#c53030",
  },
  mealHomeSwitchButton: {
    marginTop: 12,
    alignSelf: "flex-start",
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d3d3d3",
  },
  mealHomeSwitchText: {
    color: "#0c3c26",
    fontSize: 14,
    fontWeight: "600",
  },
  loadingContainer: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  welcomeScroll: {
    flexGrow: 1,
    paddingHorizontal: 24,
    paddingTop: 8,
    paddingBottom: 32,
  },
  welcomeHero: {
    width: "100%",
    alignItems: "center",
    marginBottom: -120,
  },
  welcomeCenter: {
    justifyContent: "flex-start",
    alignItems: "center",
    marginTop: -252,
    marginBottom: 0,
  },
  welcomeLogo: {
    width: "100%",
    aspectRatio: 4.5,
    maxHeight: 120,
  },
  welcomeTagline: {
    marginTop: -6,
    marginBottom: 12,
    fontSize: 16,
    fontWeight: "400",
    letterSpacing: 0.4,
    color: "rgba(27, 63, 47, 0.88)",
    textAlign: "center",
  },
  usagePanel: {
    width: "100%",
    maxWidth: 420,
    backgroundColor: "#ffffff",
    borderRadius: 24,
    padding: 20,
    shadowColor: "#2d4739",
    shadowOpacity: 0.08,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 12 },
    elevation: 6,
    gap: 32,
  },
  freeUsesCard: {
    width: "100%",
    paddingVertical: 24,
    paddingHorizontal: 22,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: "#d5e8dc",
    backgroundColor: "#f4fbf6",
    alignItems: "center",
    justifyContent: "center",
  },
  freeUsesValueStack: {
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
  },
  freeUsesValueNumber: {
    fontSize: 84,
    fontWeight: "700",
    color: "#00a651",
  },
  freeUsesValueSuffix: {
    fontSize: 18,
    fontWeight: "600",
    color: "#1a4f36",
  },
  shareSection: {
    width: "100%",
    alignItems: "center",
    gap: 8,
    marginBottom: 16,
  },
  shareButton: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 24,
    maxWidth: 420,
    alignSelf: "center",
  },
  shareButtonText: {
    flex: 1,
    textAlign: "center",
  },
  shareButtonIcon: {
    color: "#ffffff",
    marginLeft: 12,
  },
  shareSubline: {
    fontSize: 14,
    color: "#6b6b6b",
    textAlign: "center",
    width: "100%",
  },
  welcomeFooter: {
    paddingHorizontal: 20,
    paddingBottom: 16, // match home screen bottom inset
    paddingTop: 12,
    backgroundColor: "#f4f9f5",
    justifyContent: "center",
    alignItems: "center",
  },
  welcomeButtonGroup: {
    width: "100%",
    gap: 12,
  },
  welcomeButton: {
    backgroundColor: "#00a651",
    paddingVertical: 18,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
    ...SHADOW.button,
  },
  welcomeCtaButton: {
    alignSelf: "stretch",
  },
  welcomeButtonText: {
    fontSize: 16,
    fontWeight: "700",
    color: "#ffffff",
  },
  preferencesSafeArea: {
    flex: 1,
    backgroundColor: "#f4f9f5",
  },
  preferencesWrapper: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: 12,
    gap: 12,
  },
  prefCompleteContent: {
    paddingHorizontal: 24,
    paddingTop: 32,
    alignItems: "center",
  },
  prefCompleteInfoCard: {
    width: "100%",
    backgroundColor: "#ffffff",
    borderRadius: 28,
    paddingHorizontal: 28,
    paddingVertical: 32,
    alignItems: "center",
    gap: 20,
    ...SHADOW.cardLg,
  },
  prefCompleteIconCircle: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: "#00a651",
    alignItems: "center",
    justifyContent: "center",
    shadowColor: "#00a651",
    shadowOpacity: 0.3,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
  },
  prefCompleteTextGroup: {
    gap: 10,
    alignItems: "center",
  },
  prefCompleteHeadline: {
    fontSize: 34,
    fontWeight: "800",
    color: "#00a651",
    textAlign: "center",
  },
  prefCompleteSubheadline: {
    fontSize: 20,
    fontWeight: "600",
    color: "#103b29",
    textAlign: "center",
  },
  prefCompleteExplanation: {
    fontSize: 15,
    color: "#56695d",
    textAlign: "center",
    lineHeight: 22,
    maxWidth: 320,
  },
  flexSpacer: {
    flex: 1,
  },
  preferencesLoadingText: {
    marginTop: 12,
    fontSize: 14,
    color: "#3c5a47",
  },
  explorationWrapper: {
    flex: 1,
    paddingHorizontal: 32,
    paddingVertical: 48,
    justifyContent: "center",
    alignItems: "center",
  },
  explorationProcessingTitle: {
    marginTop: 24,
    fontSize: 22,
    fontWeight: "600",
    textAlign: "center",
    color: "#0f3c27",
  },
  explorationProcessingSubtitle: {
    marginTop: 12,
    fontSize: 15,
    textAlign: "center",
    color: "#4a5e53",
    lineHeight: 22,
  },
  explorationHeader: {
    paddingHorizontal: 24,
    paddingBottom: 8,
  },
  explorationMealCardScreen: {
    flex: 1,
    paddingHorizontal: 24,
    paddingTop: 12,
    paddingBottom: 24,
  },
  explorationCardHeader: {
    marginBottom: 16,
  },
  explorationCardTitle: {
    fontSize: 26,
    fontWeight: "700",
    color: "#0c3c26",
  },
  explorationMealTipCard: {
    borderRadius: 18,
    backgroundColor: "#f7f9f8",
    padding: 16,
    marginBottom: 16,
    ...SHADOW.card,
  },
  explorationMealTipTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: "#0c3c26",
  },
  explorationMealTipText: {
    fontSize: 14,
    color: "#4d6055",
    marginTop: 4,
  },
  explorationList: {
    paddingBottom: 60,
  },
  explorationMealCardScroll: {
    flex: 1,
  },
  explorationMealCardList: {
    paddingBottom: 32,
    gap: 18,
  },
  explorationCard: {
    marginHorizontal: 24,
    marginBottom: 18,
    backgroundColor: "#ffffff",
    borderRadius: 20,
    paddingHorizontal: 20,
    paddingVertical: 18,
    ...SHADOW.card,
  },
  explorationMealName: {
    fontSize: 18,
    fontWeight: "700",
    color: "#0f3c27",
    marginBottom: 6,
  },
  explorationMealDescription: {
    fontSize: 14,
    color: "#445248",
    marginBottom: 12,
    lineHeight: 20,
  },
  explorationTagRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 12,
  },
  explorationTagChip: {
    backgroundColor: "#eef5f0",
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  explorationTagText: {
    fontSize: 12,
    color: "#1b4a33",
  },
  explorationIngredients: {
    marginBottom: 14,
  },
  explorationIngredientLabel: {
    fontSize: 13,
    fontWeight: "600",
    color: "#0f3c27",
    marginBottom: 4,
  },
  explorationIngredientText: {
    fontSize: 13,
    color: "#4a5e53",
  },
  explorationActions: {
    flexDirection: "row",
    gap: 10,
  },
  explorationMealCardFooter: {
    paddingTop: 12,
  },
  explorationReactionInlineControls: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginLeft: "auto",
  },
  explorationActionButton: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#d7e3dc",
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 10,
  },
  explorationActionButtonDislike: {
    borderColor: "#f5d6d6",
  },
  explorationActionButtonActive: {
    borderColor: "#00a651",
    backgroundColor: "#e5f6ec",
  },
  explorationActionText: {
    fontSize: 15,
    fontWeight: "600",
    color: "#0f3c27",
  },
  explorationActionTextActive: {
    color: "#00a651",
  },
  explorationFooter: {
    paddingHorizontal: 24,
    paddingBottom: 36,
    paddingTop: 8,
  },
  prefProgressContainer: {
    backgroundColor: "#ffffff",
    paddingHorizontal: 18,
    paddingVertical: 16,
    borderRadius: 20,
    shadowColor: "#1c3d2d",
    shadowOpacity: 0.05,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 2,
    gap: 8,
  },
  prefProgressInline: {
    flex: 1,
    marginLeft: 12,
    paddingHorizontal: 14,
    paddingVertical: 6,
    height: 44,
  },
  prefProgressLabel: {
    fontSize: 13,
    fontWeight: "600",
    color: "#1d4731",
  },
  prefProgressTrack: {
    width: "100%",
    height: 6,
    borderRadius: 999,
    backgroundColor: "#e1efe7",
    overflow: "hidden",
  },
  prefProgressFill: {
    height: "100%",
    borderRadius: 999,
    backgroundColor: "#00a651",
  },
  prefHeaderCard: {
    backgroundColor: "#ffffff",
    borderRadius: 24,
    paddingHorizontal: 24,
    paddingVertical: 22,
    ...SHADOW.card,
    gap: 6,
  },
  prefCategoryTitle: {
    fontSize: 24,
    fontWeight: "700",
    color: "#103b29",
  },
  prefCategorySubtitle: {
    fontSize: 14,
    color: "#3f5c4b",
    lineHeight: 20,
  },
  prefScroll: {
    flex: 1,
  },
  prefScrollContent: {
    paddingBottom: 16,
    gap: 12,
  },
  prefTagCard: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 18,
    paddingVertical: 16,
    borderRadius: 20,
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#e6f0e8",
    ...SHADOW.card,
  },
  prefTagTextGroup: {
    flex: 1,
    marginRight: 12,
    gap: 2,
  },
  prefTagLabel: {
    fontSize: 16,
    fontWeight: "600",
    color: "#122e21",
  },
  prefTagHelper: {
    fontSize: 13,
    color: "#577063",
  },
  prefControls: {
    flexDirection: "row",
    gap: 8,
  },
  pastOrderActionControls: {
    marginLeft: "auto",
  },
  prefSingleSelectControls: {
    flexDirection: "row",
    justifyContent: "flex-end",
    minWidth: 160,
  },
  prefControlButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "#d5e8dc",
    backgroundColor: "#ffffff",
  },
  prefControlButtonActive: {
    shadowColor: "#1c3d2d",
    shadowOpacity: 0.12,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },
  prefControlButtonLike: {
    borderColor: "#d8f3e4",
  },
  prefControlButtonDislike: {
    borderColor: "#f9dede",
  },
  prefControlButtonNeutral: {
    borderColor: "#dae4dc",
  },
  prefControlButtonLikeActive: {
    backgroundColor: "#eaf8f0",
    borderColor: "#23a665",
  },
  prefControlButtonDislikeActive: {
    backgroundColor: "#fdeeee",
    borderColor: "#e56b6b",
  },
  prefControlButtonNeutralActive: {
    backgroundColor: "#eef3ef",
    borderColor: "#b9c9bf",
  },
  prefControlIcon: {
    fontSize: 20,
    color: "#5b7564",
  },
  prefControlIconLike: {
    color: "#7bb394",
  },
  prefControlIconDislike: {
    color: "#c77a7a",
  },
  prefControlIconNeutral: {
    color: "#7c8c83",
  },
  prefControlIconActive: {
    transform: [{ scale: 1.05 }],
  },
  prefControlIconLikeActive: {
    color: "#0d7c4b",
  },
  prefControlIconDislikeActive: {
    color: "#b53a3a",
  },
  prefControlIconNeutralActive: {
    color: "#324C3C",
  },
  prefControlButtonPrimary: {
    borderColor: "#d1f2da",
    backgroundColor: "#f1faf4",
  },
  prefControlIconPrimary: {
    color: "#0b7a3e",
  },
  pastOrderListButton: {
    marginRight: 8,
  },
  prefFooter: {
    paddingHorizontal: 20,
    paddingBottom: 16,
    paddingTop: 12,
    backgroundColor: "#f4f9f5",
    gap: 12,
    alignItems: "stretch",
  },
  prefFooterFixed: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
  },
  prefFooterHint: {
    fontSize: 13,
    color: "#4d6757",
    textAlign: "center",
  },
  prefContinueButton: {
    backgroundColor: "#00a651",
    paddingVertical: 18,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    width: "100%",
    ...SHADOW.button,
  },
  prefContinueButtonDisabled: {
    opacity: 0.7,
  },
  prefContinueButtonText: {
    fontSize: 16,
    fontWeight: "700",
    color: "#ffffff",
  },
  prefRedoButton: {
    borderWidth: 1,
    borderColor: "#c5d9cc",
    borderRadius: 20,
    paddingVertical: 16,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#ffffff",
    shadowColor: "#1c3d2d",
    shadowOpacity: 0.05,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
  prefRedoButtonText: {
    fontSize: 16,
    fontWeight: "600",
    color: "#184331",
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
  payfastStatusCard: {
    marginTop: 12,
    padding: 12,
    borderRadius: 12,
    backgroundColor: "#f4f4f4",
    borderWidth: 1,
    borderColor: "#e0e0e0",
  },
  payfastStatusCardDone: {
    borderColor: "#34a853",
    backgroundColor: "#e8f5e9",
  },
  payfastStatusHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  payfastStatusTitle: {
    fontSize: 14,
    fontWeight: "600",
    color: "#111",
  },
  payfastStatusDismiss: {
    fontSize: 12,
    color: "#0066cc",
  },
  payfastStatusValue: {
    fontSize: 16,
    fontWeight: "600",
    color: "#111",
  },
  payfastStatusMessage: {
    fontSize: 13,
    color: "#333",
  },
  payfastStatusMeta: {
    fontSize: 12,
    color: "#666",
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
  checkoutHeader: {
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: 8,
  },
  checkoutTitle: {
    fontSize: 30,
    fontWeight: "700",
    color: "#0c3c26",
  },
  checkoutSubtitle: {
    fontSize: 15,
    color: "#4b6856",
    marginTop: 4,
  },
  checkoutStatusCard: {
    marginHorizontal: 20,
    marginBottom: 12,
    padding: 16,
    borderRadius: 18,
    backgroundColor: "#ffffff",
    gap: 6,
    ...SHADOW.card,
  },
  checkoutStatusLabel: {
    fontSize: 15,
    fontWeight: "600",
    color: "#0c3c26",
  },
  checkoutStatusMeta: {
    fontSize: 13,
    color: "#4a6756",
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
  mealRecommendationsScroll: {
    flex: 1,
    marginTop: 12,
    marginBottom: 12,
  },
  mealRecommendationsContent: {
    paddingBottom: 24,
    gap: 12,
  },
  mealRecommendationsPlaceholder: {
    fontSize: 16,
    color: "#2d6041",
    textAlign: "center",
    marginTop: 40,
  },
  homeMealCard: {
    backgroundColor: "#ffffff",
    borderRadius: 20,
    paddingVertical: 20,
    paddingHorizontal: 18,
    ...SHADOW.card,
  },
  homeMealTitle: {
    fontSize: 22,
    fontWeight: "700",
    color: "#0c3c26",
    textAlign: "center",
    marginBottom: 8,
  },
  homeMealDescription: {
    fontSize: 15,
    color: "#2c4a38",
    textAlign: "center",
    marginBottom: 12,
  },
  homeMealIngredients: {
    display: "none",
  },
  homeMealIngredientsTitle: {
    fontSize: 14,
    fontWeight: "600",
    color: "#0c3c26",
    marginBottom: 4,
  },
  homeMealPrepTime: {
    marginTop: 6,
    fontSize: 13,
    color: "#4a6756",
    textAlign: "center",
  },
  homeMealFooterRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 16,
    gap: 12,
  },
  homeMealServingsRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    flexShrink: 1,
  },
  homeMealServingsLabel: {
    fontSize: 14,
    fontWeight: "600",
    color: "#0c3c26",
  },
  homeMealServingsValueText: {
    fontSize: 14,
    fontWeight: "600",
    color: "#0c3c26",
  },
  homeMealActionGroup: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginLeft: "auto",
  },
  homeMealDislikeButton: {
    width: 40,
    height: 40,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: "#f9dede",
    backgroundColor: "#fff5f5",
    alignItems: "center",
    justifyContent: "center",
  },
  homeMealDislikeButtonActive: {
    backgroundColor: "#fdeeee",
    borderColor: "#e56b6b",
  },
  homeMealDislikeButtonIcon: {
    fontSize: 18,
    color: "#c77a7a",
  },
  homeMealDislikeButtonIconActive: {
    color: "#b53a3a",
  },
  homeMealChooseButton: {
    marginLeft: 0,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: "#d8f3e4",
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: "#fff",
  },
  homeMealChooseButtonActive: {
    backgroundColor: "#eaf8f0",
    borderColor: "#23a665",
  },
  homeMealChooseButtonText: {
    fontSize: 14,
    fontWeight: "600",
    color: "#0c3c26",
  },
  homeMealChooseButtonTextActive: {
    color: "#0d7c4b",
  },
  homeMealChooseButtonIcon: {
    color: "#7bb394",
  },
  homeMealChooseButtonIconActive: {
    color: "#0d7c4b",
  },
  homeMealActionIconButton: {
    paddingHorizontal: 12,
  },
  homeMealIngredientItem: {
    fontSize: 14,
    color: "#2d6041",
    marginBottom: 2,
    display: "none",
  },
  homeNoInterestButton: {
    marginTop: 12,
    paddingVertical: 14,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: "#00a651",
    backgroundColor: "#ffffff",
    alignItems: "center",
    justifyContent: "center",
  },
  homeNoInterestButtonText: {
    fontSize: 15,
    fontWeight: "600",
    color: "#00a651",
  },
  mealHomeCtaButton: {
    alignSelf: "center",
    width: "100%",
    marginTop: 4,
  },
  mealDetailModalContainer: {
    position: "absolute",
    top: 0,
    bottom: 0,
    left: 0,
    right: 0,
    zIndex: 25,
    justifyContent: "center",
    alignItems: "center",
  },
  mealDetailBackdrop: {
    position: "absolute",
    top: 0,
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: "rgba(0,0,0,0.4)",
  },
  mealDetailCard: {
    width: "90%",
    maxHeight: "80%",
    backgroundColor: "#fff",
    borderRadius: 24,
    ...SHADOW.cardLg,
    paddingVertical: 20,
    paddingHorizontal: 20,
    flexDirection: "column",
  },
  confirmModalContent: {
    flexGrow: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 12,
  },
  confirmSubtitle: {
    fontSize: 16,
    color: "#0c3c26",
    marginTop: 8,
    textAlign: "center",
  },
  mealDetailScroll: {
    flexGrow: 1,
    flexShrink: 1,
  },
  mealDetailContent: {
    paddingBottom: 12,
  },
  mealDetailTitle: {
    fontSize: 24,
    fontWeight: "700",
    color: "#0c3c26",
    marginBottom: 12,
  },
  mealDetailDescription: {
    fontSize: 16,
    color: "#2c4a38",
    marginBottom: 16,
  },
  mealDetailSection: {
    marginBottom: 16,
  },
  mealDetailSectionTitle: {
    fontSize: 16,
    fontWeight: "600",
    color: "#0c3c26",
    marginBottom: 6,
  },
  mealDetailSectionItem: {
    fontSize: 15,
    color: "#2d6041",
    marginBottom: 4,
  },
  mealDetailCloseButton: {
    marginTop: 16,
    alignSelf: "center",
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: "#f4f9f5",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 2,
    borderColor: "#00a651",
  },
  mealDetailCloseText: {
    color: "#00a651",
    fontSize: 40,
    fontWeight: "700",
    lineHeight: 40,
  },
  confirmAcceptButton: {
    alignSelf: "center",
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: "#00a651",
    alignItems: "center",
    justifyContent: "center",
    marginTop: 12,
  },
  confirmAcceptText: {
    color: "#fff",
    fontSize: 40,
    fontWeight: "700",
    lineHeight: 40,
  },
});
