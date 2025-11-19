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
  FlatList,
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
const DEFAULT_HOME_MEAL_SERVINGS = 4;
const MIN_HOME_MEAL_SERVINGS = 1;
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

const PREFERENCES_STATE_STORAGE_KEY = "yummi.preferences.state.v1";
const PREFERENCES_COMPLETED_STORAGE_KEY = "yummi.preferences.completed.v1";
const PREFERENCES_TAGS_VERSION = "2025.02.0"; // Keep in sync with data/tags/defined_tags.json
const PREFERENCES_API_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/preferences`
  : null;
const EXPLORATION_MEAL_TARGET = 10;
const EXPLORATION_API_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/recommendations/exploration`
  : null;
const PREFERENCE_CONTROL_STATES = [
  { id: "like", label: "Like", icon: "👍" },
  { id: "neutral", label: "Skip", icon: "○" },
  { id: "dislike", label: "Dislike", icon: "👎" },
];
const RECOMMENDATION_MEAL_TARGET = 10;
const RECOMMENDATION_API_ENDPOINT = API_BASE_URL
  ? `${API_BASE_URL}/recommendations/feed`
  : null;
const VEGETARIAN_PROTEIN_DISLIKES = [
  "protein_chicken",
  "protein_beef",
  "protein_pork",
  "protein_lamb",
  "protein_seafood",
];
const VEGAN_ADDITIONAL_PROTEIN_DISLIKES = ["protein_egg"];
const PLANT_FRIENDLY_PROTEINS = [
  "protein_legume",
  "protein_tofu",
  "protein_mushroom",
  "protein_egg",
];
const BASE_PREFERENCE_CATEGORIES = [
  {
    id: "diet",
    title: "Diet Preferences",
    description: "Tell us which dietary patterns match your household.",
    tags: [
      { id: "diet_omnivore", label: "Omnivore" },
      { id: "diet_flex", label: "Flexitarian" },
      { id: "diet_veg", label: "Vegetarian" },
      { id: "diet_vegan", label: "Vegan" },
      { id: "diet_pesc", label: "Pescatarian" },
      { id: "diet_poultry", label: "Poultry only" },
      { id: "diet_lowcarb", label: "Low carb" },
      { id: "diet_keto", label: "Keto" },
      { id: "diet_glutenaware", label: "Gluten aware" },
      { id: "diet_highprotein", label: "High protein" },
    ],
  },
  {
    id: "cuisine",
    title: "Cuisine Types",
    description: "Highlight the cuisines you’re most excited about.",
    tags: [
      { id: "cuisine_southaf", label: "South African" },
      { id: "cuisine_american", label: "American" },
      { id: "cuisine_caribbean", label: "Caribbean" },
      { id: "cuisine_chinese", label: "Chinese" },
      { id: "cuisine_french", label: "French" },
      { id: "cuisine_greek", label: "Greek" },
      { id: "cuisine_indian", label: "Indian" },
      { id: "cuisine_italian", label: "Italian" },
      { id: "cuisine_japanese", label: "Japanese" },
      { id: "cuisine_korean", label: "Korean" },
      { id: "cuisine_latin", label: "Latin American" },
      { id: "cuisine_mexican", label: "Mexican" },
      { id: "cuisine_mideast", label: "Middle Eastern" },
      { id: "cuisine_northaf", label: "North African" },
      { id: "cuisine_portuguese", label: "Portuguese" },
      { id: "cuisine_spanish", label: "Spanish" },
      { id: "cuisine_thai", label: "Thai" },
      { id: "cuisine_turkish", label: "Turkish" },
      { id: "cuisine_vietnamese", label: "Vietnamese" },
    ],
  },
  {
    id: "cuisineOpenness",
    title: "Flavor Exploration",
    description: "How adventurous should we get with global flavors?",
    tags: [
      { id: "copen_familiar", label: "Familiar classics" },
      { id: "copen_regional", label: "Regional twists" },
      { id: "copen_global", label: "Global explorer" },
      { id: "copen_experimental", label: "Experimental fusion" },
    ],
  },
  {
    id: "proteinBase",
    title: "Protein Bases",
    description: "Pick the primary proteins that should drive meals.",
    tags: [
      { id: "protein_chicken", label: "Chicken" },
      { id: "protein_beef", label: "Beef" },
      { id: "protein_pork", label: "Pork" },
      { id: "protein_lamb", label: "Lamb" },
      { id: "protein_seafood", label: "Seafood" },
      { id: "protein_legume", label: "Legumes" },
      { id: "protein_tofu", label: "Tofu & tempeh" },
      { id: "protein_egg", label: "Eggs" },
      { id: "protein_mushroom", label: "Mushrooms" },
    ],
  },
  {
    id: "dishFormat",
    title: "Dish Formats",
    description: "Tell us the meal formats that feel like home.",
    tags: [
      { id: "format_bowl", label: "Bowls" },
      { id: "format_salad", label: "Salads" },
      { id: "format_soup", label: "Soups & stews" },
      { id: "format_onepan", label: "One-pan meals" },
      { id: "format_sheetpan", label: "Sheet-pan meals" },
      { id: "format_wrap", label: "Wraps & handhelds" },
      { id: "format_pasta", label: "Pastas" },
      { id: "format_sandwich", label: "Sandwiches" },
      { id: "format_mealprep", label: "Meal prep portions" },
    ],
  },
  {
    id: "technique",
    title: "Cooking Techniques",
    description: "Feature the cooking methods you love (or want to avoid).",
    tags: [
      { id: "tech_saute", label: "Sauté" },
      { id: "tech_roast", label: "Roast" },
      { id: "tech_grill", label: "Grill" },
      { id: "tech_stirfry", label: "Stir-fry" },
      { id: "tech_pressure", label: "Pressure cook" },
      { id: "tech_slow", label: "Slow cooker" },
      { id: "tech_airfry", label: "Air fry" },
      { id: "tech_nocook", label: "No-cook" },
      { id: "tech_bake", label: "Bake" },
    ],
  },
  {
    id: "prepTime",
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
    id: "complexity",
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
    id: "heatSpice",
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
    id: "audience",
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
    id: "budgetLevel",
    title: "Budget & Ingredients",
    description: "Set the ingredient spend you’re comfortable with.",
    tags: [
      { id: "budget_value", label: "Value staples" },
      { id: "budget_affordable", label: "Affordable" },
      { id: "budget_balanced", label: "Balanced mix" },
      { id: "budget_premium", label: "Premium occasions" },
      { id: "budget_luxury", label: "Luxury experience" },
    ],
  },
  {
    id: "occasion",
    title: "Occasions",
    description: "Tag the occasions you plan for most often.",
    tags: [
      { id: "occasion_weeknight", label: "Weeknight staples" },
      { id: "occasion_weekend", label: "Weekend projects" },
      { id: "occasion_entertaining", label: "Entertaining" },
      { id: "occasion_comfort", label: "Comfort food" },
      { id: "occasion_light", label: "Light lunches" },
    ],
  },
  {
    id: "ethics",
    title: "Ethics & Religious Needs",
    description: "Respect cultural or ethical guardrails every time.",
    tags: [
      { id: "ethics_halal", label: "Halal" },
      { id: "ethics_kosher", label: "Kosher-style" },
      { id: "ethics_jain", label: "Jain-friendly" },
      { id: "ethics_sussea", label: "Sustainable seafood" },
      { id: "ethics_animal", label: "Animal welfare" },
    ],
  },
  {
    id: "allergens",
    title: "Avoidances & Allergens",
    description: "Flag anything that must stay out of your kitchen.",
    tags: [
      { id: "allergen_gluten", label: "Gluten" },
      { id: "allergen_dairy", label: "Dairy" },
      { id: "allergen_egg", label: "Eggs" },
      { id: "allergen_soy", label: "Soy" },
      { id: "allergen_peanut", label: "Peanuts" },
      { id: "allergen_treenut", label: "Tree nuts" },
      { id: "allergen_shellfish", label: "Shellfish" },
      { id: "allergen_fish", label: "Fish" },
      { id: "allergen_sesame", label: "Sesame" },
      { id: "allergen_mustard", label: "Mustard" },
    ],
  },
  {
    id: "nutritionFocus",
    title: "Nutrition Focus",
    description: "Call out wellness goals we should optimize for.",
    tags: [
      { id: "nutrition_highprotein", label: "High protein" },
      { id: "nutrition_lowcal", label: "Low calorie" },
      { id: "nutrition_heart", label: "Heart healthy" },
      { id: "nutrition_fiber", label: "High fiber" },
      { id: "nutrition_diabetic", label: "Diabetic-friendly" },
      { id: "nutrition_immune", label: "Immune support" },
    ],
  },
  {
    id: "equipment",
    title: "Equipment",
    description: "Let us know what gear is fair game.",
    tags: [
      { id: "equip_none", label: "No special gear" },
      { id: "equip_oven", label: "Oven" },
      { id: "equip_instantpot", label: "Instant Pot / pressure cooker" },
      { id: "equip_slowcooker", label: "Slow cooker" },
      { id: "equip_airfryer", label: "Air fryer" },
      { id: "equip_blender", label: "Blender" },
      { id: "equip_grill", label: "Outdoor grill" },
    ],
  },
];

// Controls the order of preference categories in the UI while keeping the full metadata above.
const PREFERENCE_CATEGORY_ORDER = ["audience", "prepTime", "complexity"];
const ORDERED_PREFERENCE_CATEGORIES = [
  ...PREFERENCE_CATEGORY_ORDER.map((categoryId) =>
    BASE_PREFERENCE_CATEGORIES.find((category) => category.id === categoryId)
  ),
  ...BASE_PREFERENCE_CATEGORIES.filter(
    (category) => !PREFERENCE_CATEGORY_ORDER.includes(category.id)
  ),
].filter(Boolean);
const SINGLE_SELECT_PREFERENCE_CATEGORY_IDS = new Set(["audience"]);
const isSingleSelectPreferenceCategory = (categoryId) =>
  SINGLE_SELECT_PREFERENCE_CATEGORY_IDS.has(categoryId);

const PREFERENCE_TAG_LABEL_LOOKUP = BASE_PREFERENCE_CATEGORIES.reduce(
  (acc, category) => {
    category.tags.forEach((tag) => {
      acc[tag.id] = tag.label;
    });
    return acc;
  },
  {}
);

const clonePreferenceResponses = (responses = {}) => {
  return Object.entries(responses).reduce((acc, [categoryId, values]) => {
    acc[categoryId] = { ...values };
    return acc;
  }, {});
};

const ensurePreferenceValue = (
  draft,
  categoryId,
  tagId,
  desiredValue,
  options = {}
) => {
  const { protectLikes = true } = options;
  if (!draft[categoryId]) {
    draft[categoryId] = {};
  }
  const currentValue = draft[categoryId][tagId];
  if (protectLikes && currentValue === "like" && desiredValue !== "like") {
    return false;
  }
  if (currentValue === desiredValue) {
    return false;
  }
  draft[categoryId][tagId] = desiredValue;
  return true;
};

const applyPreferenceSmartLogic = (responses = {}) => {
  const draft = clonePreferenceResponses(responses);
  let didChange = false;

  const dietSelections = draft.diet ?? {};
  const isVegan = dietSelections.diet_vegan === "like";
  const isVegetarian = dietSelections.diet_veg === "like";

  if (isVegetarian) {
    VEGETARIAN_PROTEIN_DISLIKES.forEach((tagId) => {
      const changed = ensurePreferenceValue(
        draft,
        "proteinBase",
        tagId,
        "dislike"
      );
      if (changed) {
        didChange = true;
      }
    });
  }

  if (isVegan) {
    VEGETARIAN_PROTEIN_DISLIKES.concat(VEGAN_ADDITIONAL_PROTEIN_DISLIKES).forEach(
      (tagId) => {
        const changed = ensurePreferenceValue(
          draft,
          "proteinBase",
          tagId,
          "dislike"
        );
        if (changed) {
          didChange = true;
        }
      }
    );
  }

  return didChange ? draft : responses;
};

const shouldSkipPreferenceCategory = (categoryId, responses = {}) => {
  const dietSelections = responses.diet ?? {};
  if (categoryId === "proteinBase" && dietSelections.diet_vegan === "like") {
    return true;
  }
  return false;
};

const filterPreferenceCategoryTags = (category, responses = {}) => {
  const dietSelections = responses.diet ?? {};

  if (category.id === "proteinBase" && dietSelections.diet_veg === "like") {
    if (dietSelections.diet_vegan === "like") {
      return [];
    }
    return category.tags.filter((tag) =>
      PLANT_FRIENDLY_PROTEINS.includes(tag.id)
    );
  }

  return category.tags;
};

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

const formatListForNotice = (items) => {
  if (!items.length) {
    return "";
  }
  if (items.length === 1) {
    return items[0];
  }
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
};

const derivePreferenceNotices = (responses = {}) => {
  const notices = [];
  const dietSelections = responses.diet ?? {};
  const allergenSelections = responses.allergens ?? {};

  if (dietSelections.diet_vegan === "like") {
    notices.push("We’ll skip protein options that conflict with a vegan diet.");
  } else if (dietSelections.diet_veg === "like") {
    notices.push("Protein tags now focus on vegetarian-friendly options.");
  }

  const avoidedLabels = Object.entries(allergenSelections)
    .filter(([, value]) => value === "dislike")
    .map(
      ([key]) => PREFERENCE_TAG_LABEL_LOOKUP[key] ?? key.replace(/_/g, " ")
    );

  if (avoidedLabels.length > 0) {
    notices.push(
      `We’ll prioritize meals without ${formatListForNotice(avoidedLabels)}.`
    );
  }

  return notices;
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
  const [isRecommendationFlowVisible, setIsRecommendationFlowVisible] = useState(false);
  const [recommendationState, setRecommendationState] = useState("idle");
  const [recommendationMeals, setRecommendationMeals] = useState([]);
  const [recommendationNotes, setRecommendationNotes] = useState([]);
  const [recommendationError, setRecommendationError] = useState(null);
  const [homeRecommendedMeals, setHomeRecommendedMeals] = useState([]);
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
  const [isSorryToHearScreenVisible, setIsSorryToHearScreenVisible] = useState(false);
  const [mealServings, setMealServings] = useState({});
  const [ingredientQuantities, setIngredientQuantities] = useState({});
  const preferenceSyncHashRef = useRef(null);
  const preferenceEntryContextRef = useRef(null);
  const homeMealsBackupRef = useRef(null);

  const applyHomeRecommendedMeals = useCallback((meals) => {
    const nextSource = Array.isArray(meals)
      ? meals.filter((meal) => Boolean(meal))
      : [];
    const randomizedMeals = shuffleMeals(nextSource);
    setHomeRecommendedMeals(randomizedMeals);
    setSelectedHomeMealIds({});
    setHomeMealDislikedIds({});
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
  const preferenceNotices = useMemo(
    () => derivePreferenceNotices(preferenceResponses),
    [preferenceResponses]
  );
  const activeCategoryRatingsCount =
    activePreferenceCategory && preferenceResponses?.[activePreferenceCategory.id]
      ? Object.keys(preferenceResponses[activePreferenceCategory.id]).length
      : 0;
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
  const selectedMealIngredients = useMemo(() => {
    if (!selectedHomeMeals.length) {
      return [];
    }
    const items = [];
    selectedHomeMeals.forEach((meal) => {
      if (!meal) {
        return;
      }
      const mealIngredients = Array.isArray(meal.ingredients)
        ? meal.ingredients
        : [];
      if (!mealIngredients.length) {
        return;
      }
      mealIngredients.forEach((ingredient, index) => {
        if (!ingredient) {
          return;
        }
        const parts = [];
        if (ingredient.quantity) {
          parts.push(String(ingredient.quantity));
        }
        if (ingredient.unit) {
          parts.push(String(ingredient.unit));
        }
        if (ingredient.name) {
          parts.push(String(ingredient.name));
        }
        if (ingredient.preparation) {
          parts.push(`(${ingredient.preparation})`);
        }
        const fallback =
          parts.length > 0 ? parts.join(" ") : `Ingredient ${index + 1}`;
        const displayText =
          ingredient.productName?.trim() || fallback.trim();
        const requiredQuantity = parseIngredientQuantity(ingredient.quantity);
        items.push({
          id: `${meal.mealId ?? "meal"}-ingredient-${index}`,
          text: displayText,
          requiredQuantity,
        });
      });
    });
    return items.sort((a, b) => {
      const textA = a.text?.toLowerCase() ?? "";
      const textB = b.text?.toLowerCase() ?? "";
      if (textA < textB) {
        return -1;
      }
      if (textA > textB) {
        return 1;
      }
      return 0;
    });
  }, [selectedHomeMeals]);

  useEffect(() => {
    setIngredientQuantities((prev) => {
      const next = {};
      selectedMealIngredients.forEach((ingredient) => {
        const existingValue = prev?.[ingredient.id];
        if (typeof existingValue === "number" && Number.isFinite(existingValue)) {
          next[ingredient.id] = existingValue;
        } else {
          next[ingredient.id] =
            typeof ingredient.requiredQuantity === "number" &&
            Number.isFinite(ingredient.requiredQuantity)
              ? ingredient.requiredQuantity
              : 1;
        }
      });
      return next;
    });
  }, [selectedMealIngredients]);
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

  const handleHomeMealServingsIncrease = useCallback((mealId) => {
    if (!mealId) {
      return;
    }
    setMealServings((prev) => {
      const current = prev[mealId] ?? DEFAULT_HOME_MEAL_SERVINGS;
      const next = current + 1;
      if (next === current) {
        return prev;
      }
      const updated = { ...prev, [mealId]: next };
      if (next === DEFAULT_HOME_MEAL_SERVINGS) {
        delete updated[mealId];
      }
      return updated;
    });
  }, []);

  const handleHomeMealServingsDecrease = useCallback((mealId) => {
    if (!mealId) {
      return;
    }
    setMealServings((prev) => {
      const current = prev[mealId] ?? DEFAULT_HOME_MEAL_SERVINGS;
      const next = Math.max(MIN_HOME_MEAL_SERVINGS, current - 1);
      if (next === current) {
        return prev;
      }
      const updated = { ...prev, [mealId]: next };
      if (next === DEFAULT_HOME_MEAL_SERVINGS) {
        delete updated[mealId];
      }
      return updated;
    });
  }, []);

  const handleToggleHomeMealDislike = useCallback((mealId) => {
    if (!mealId) {
      return;
    }
    setHomeMealDislikedIds((prev) => {
      const next = { ...(prev || {}) };
      if (next[mealId]) {
        delete next[mealId];
      } else {
        next[mealId] = true;
      }
      return next;
    });
  }, []);

  const handleToggleHomeMealSelection = useCallback((mealId) => {
    if (!mealId) {
      return;
    }
    setSelectedHomeMealIds((prev) => {
      const next = { ...(prev || {}) };
      if (next[mealId]) {
        delete next[mealId];
      } else {
        next[mealId] = true;
      }
      return next;
    });
  }, []);

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

  const handleOpenIngredientsScreen = useCallback(() => {
    setIsMealMenuOpen(false);
    setScreen("ingredients");
  }, []);

  const handleIngredientsBackToHome = useCallback(() => {
    setIsMealMenuOpen(false);
    setScreen("home");
  }, []);

  const handleReturnToWelcome = useCallback(() => {
    setIsWelcomeComplete(false);
    setIsOnboardingActive(false);
    setIsMealMenuOpen(false);
    setHomeSurface("meal");
    setScreen("home");
    setIsSorryToHearScreenVisible(false);
  }, []);

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
    setIsRecommendationFlowVisible(false);
    setRecommendationState("idle");
    setRecommendationMeals([]);
    setRecommendationNotes([]);
    setRecommendationError(null);
    setHasSeenExplorationResults(false);
    setIsRecommendationFlowVisible(false);
    setRecommendationState("idle");
    setRecommendationMeals([]);
    setRecommendationNotes([]);
    setRecommendationError(null);
  }, [applyHomeRecommendedMeals, userId]);

  useEffect(() => {
    if (screen !== "home" || !isMealHomeSurface) {
      setIsMealMenuOpen(false);
    }
  }, [isMealHomeSurface, screen]);

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
        const hasLocalSelections =
          preferenceResponses && Object.keys(preferenceResponses).length > 0;
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
          applyHomeRecommendedMeals(latestMealsSource);
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
    setMealServings({});
  }, [homeRecommendedMeals]);

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
      setIsRecommendationFlowVisible(false);
      setRecommendationState("idle");
      setRecommendationMeals([]);
      setRecommendationNotes([]);
      setRecommendationError(null);
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

  const showConfirmationDialog = useCallback((context) => {
    setConfirmationDialog({
      visible: true,
      context,
    });
  }, []);

  const handleCloseConfirmationDialog = useCallback(() => {
    setConfirmationDialog({
      visible: false,
      context: null,
    });
  }, []);

  const handleConfirmDialog = useCallback(() => {
    const context = confirmationDialog.context;
    setConfirmationDialog({
      visible: false,
      context: null,
    });
    if (context === "shoppingList") {
      // Future: trigger shopping list build + checkout logic here.
    } else if (context === "newMeals") {
      handleConfirmPreferenceComplete();
    } else if (context === "woolworthsCart") {
      // Future: trigger add-to-cart flow once wired up.
    }
  }, [confirmationDialog.context, handleConfirmPreferenceComplete]);

  const handleOpenShoppingListConfirm = useCallback(() => {
    showConfirmationDialog("shoppingList");
  }, [showConfirmationDialog]);

  const handleOpenNewMealsConfirm = useCallback(() => {
    showConfirmationDialog("newMeals");
  }, [showConfirmationDialog]);
  const handleOpenWoolworthsCartConfirm = useCallback(() => {
    showConfirmationDialog("woolworthsCart");
  }, [showConfirmationDialog]);
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

  const startExplorationRun = useCallback(async () => {
    if (!EXPLORATION_API_ENDPOINT) {
      return;
    }
    setExplorationState("running");
    setExplorationError(null);
    setIsRecommendationFlowVisible(false);
    setRecommendationState("idle");
    setRecommendationMeals([]);
    setRecommendationNotes([]);
    setRecommendationError(null);
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
    setIsRecommendationFlowVisible(false);
    setRecommendationState("idle");
    setRecommendationMeals([]);
    setRecommendationNotes([]);
    setRecommendationError(null);
    setHasSeenExplorationResults(false);
  }, []);

  const runRecommendationFeed = useCallback(async () => {
    if (!RECOMMENDATION_API_ENDPOINT || !explorationSessionId) {
      return;
    }
    setRecommendationState("running");
    setRecommendationError(null);
    setRecommendationMeals([]);
    setRecommendationNotes([]);
    try {
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
      setRecommendationMeals(data?.meals ?? []);
      setRecommendationNotes(data?.notes ?? []);
      setRecommendationState("ready");
    } catch (error) {
      console.warn("Recommendation feed run failed", error);
      setRecommendationError(
        error?.message ?? "Something went wrong while building recommendations."
      );
      setRecommendationState("error");
    }
  }, [
    RECOMMENDATION_API_ENDPOINT,
    buildAuthHeaders,
    explorationReactions,
    explorationSessionId,
  ]);

  const handleCompleteOnboardingFlow = useCallback(() => {
    setHasFetchedRemotePreferences(false);
    applyHomeRecommendedMeals([]);
    setIsOnboardingActive(false);
    setIsRecommendationFlowVisible(false);
    setRecommendationState("idle");
    setRecommendationMeals([]);
    setRecommendationNotes([]);
    setRecommendationError(null);
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

  const handleConfirmExplorationReview = useCallback(() => {
    setHasSeenExplorationResults(true);
    if (!RECOMMENDATION_API_ENDPOINT || !explorationSessionId) {
      handleCompleteOnboardingFlow();
      return;
    }
    setIsRecommendationFlowVisible(true);
    runRecommendationFeed();
  }, [
    RECOMMENDATION_API_ENDPOINT,
    explorationSessionId,
    handleCompleteOnboardingFlow,
    runRecommendationFeed,
  ]);

  const handleRecommendationRetry = useCallback(() => {
    runRecommendationFeed();
  }, [runRecommendationFeed]);

  const handleRecommendationComplete = useCallback(() => {
    handleCompleteOnboardingFlow();
  }, [handleCompleteOnboardingFlow]);

  const handleSkipRecommendationFlow = useCallback(() => {
    handleCompleteOnboardingFlow();
  }, [handleCompleteOnboardingFlow]);

  const handleExplorationReaction = useCallback((mealId, value) => {
    setExplorationReactions((prev) => {
      const current = prev[mealId];
      if (current === value) {
        const next = { ...prev };
        delete next[mealId];
        return next;
      }
      return {
        ...prev,
        [mealId]: value,
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

  const renderRecommendationMeal = useCallback(({ item }) => {
    const tagEntries = Object.entries(item.tags ?? {}).slice(0, 2);
    const confidenceLabel =
      typeof item.confidence === "number"
        ? `${Math.round(item.confidence * 100)}% match`
        : null;
    return (
      <View style={styles.recommendationCard}>
        <View style={styles.recommendationHeading}>
          <View style={styles.recommendationRankBadge}>
            <Text style={styles.recommendationRankText}>#{item.rank}</Text>
          </View>
          <View style={styles.recommendationTitleGroup}>
            <Text style={styles.recommendationMealName}>{item.name}</Text>
            {confidenceLabel ? (
              <Text style={styles.recommendationConfidence}>{confidenceLabel}</Text>
            ) : null}
          </View>
        </View>
        {item.description ? (
          <Text style={styles.recommendationDescription}>{item.description}</Text>
        ) : null}
        {tagEntries.length ? (
          <View style={styles.recommendationTagRow}>
            {tagEntries.map(([category, values]) => (
              <View
                key={`${item.mealId}-${category}`}
                style={styles.recommendationTagChip}
              >
                <Text style={styles.recommendationTagText}>
                  {`${category}: ${values.slice(0, 2).join(", ")}`}
                </Text>
              </View>
            ))}
          </View>
        ) : null}
        {item.rationale ? (
          <Text style={styles.recommendationRationale}>{item.rationale}</Text>
        ) : null}
        {item.diversityAxes?.length ? (
          <View style={styles.recommendationAxes}>
            {item.diversityAxes.map((axis, index) => (
              <Text key={`${item.mealId}-axis-${index}`} style={styles.recommendationAxisText}>
                • {axis}
              </Text>
            ))}
          </View>
        ) : null}
      </View>
    );
  }, []);

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
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={() => setIsWelcomeComplete(true)}
            >
              <Text style={styles.welcomeButtonText}>Start Shopping!</Text>
            </TouchableOpacity>
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
          {preferenceNotices.length > 0 && (
            <View style={styles.prefLogicCard}>
              {preferenceNotices.map((notice) => (
                <Text key={notice} style={styles.prefLogicText}>
                  {notice}
                </Text>
              ))}
            </View>
          )}
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
              const isTagSelected =
                isSingleSelectCategory && tagValue === "like";
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
                      isSingleSelectCategory
                        ? styles.prefSingleSelectControls
                        : styles.prefControls
                    }
                  >
                    {isSingleSelectCategory ? (
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
                            "like"
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
                            isTagSelected &&
                              styles.prefControlIconNeutralActive,
                          ]}
                        >
                          ○
                        </Text>
                      </TouchableOpacity>
                    ) : (
                      PREFERENCE_CONTROL_STATES.map((control) => {
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
                      })
                    )}
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

  const mealMenuOverlay = isMealMenuOpen ? (
    <View style={styles.mealMenuContainer} pointerEvents="box-none">
      <TouchableWithoutFeedback onPress={closeMealMenu}>
        <View style={styles.mealMenuBackdrop} />
      </TouchableWithoutFeedback>
      <View style={[styles.mealMenuCard, { top: menuOverlayTop }] }>
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
        {confirmationDialog.visible ? (
          <View style={styles.mealDetailModalContainer} pointerEvents="box-none">
            <TouchableWithoutFeedback onPress={handleCloseConfirmationDialog}>
              <View style={styles.mealDetailBackdrop} />
            </TouchableWithoutFeedback>
            <View style={styles.mealDetailCard}>
              <View style={styles.confirmModalContent}>
                <Text style={styles.mealDetailTitle}>Please confirm</Text>
                <Text style={styles.confirmSubtitle}>use a free use</Text>
              </View>
              <TouchableOpacity
                style={styles.confirmAcceptButton}
                onPress={handleConfirmDialog}
                accessibilityRole="button"
                accessibilityLabel="Confirm action"
              >
                <Text style={styles.confirmAcceptText}>✓</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.mealDetailCloseButton}
                onPress={handleCloseConfirmationDialog}
                accessibilityRole="button"
                accessibilityLabel="Dismiss confirmation"
              >
                <Text style={styles.mealDetailCloseText}>×</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : null}
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
        {confirmationDialog.visible ? (
          <View style={styles.mealDetailModalContainer} pointerEvents="box-none">
            <TouchableWithoutFeedback onPress={handleCloseConfirmationDialog}>
              <View style={styles.mealDetailBackdrop} />
            </TouchableWithoutFeedback>
            <View style={styles.mealDetailCard}>
              <View style={styles.confirmModalContent}>
                <Text style={styles.mealDetailTitle}>Please confirm</Text>
                <Text style={styles.confirmSubtitle}>use a free use</Text>
              </View>
              <TouchableOpacity
                style={styles.confirmAcceptButton}
                onPress={handleConfirmDialog}
                accessibilityRole="button"
                accessibilityLabel="Confirm action"
              >
                <Text style={styles.confirmAcceptText}>✓</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.mealDetailCloseButton}
                onPress={handleCloseConfirmationDialog}
                accessibilityRole="button"
                accessibilityLabel="Dismiss confirmation"
              >
                <Text style={styles.mealDetailCloseText}>×</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : null}
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
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <FlatList
          data={explorationMeals}
          keyExtractor={(item) => item.mealId}
          renderItem={renderExplorationMeal}
          ListHeaderComponent={
            <View style={styles.explorationHeader}>
              <Text style={styles.prefCategoryTitle}>
                Tap like or dislike on each meal
              </Text>
              <Text style={styles.prefCategorySubtitle}>
                We’ll use your reactions plus the onboarding tags to refine future runs.
              </Text>
              {explorationNotes?.length ? (
                <View style={styles.explorationNotes}>
                  {explorationNotes.map((note, index) => (
                    <Text key={`note-${index}`} style={styles.explorationNoteText}>
                      • {note}
                    </Text>
                  ))}
                </View>
              ) : null}
            </View>
          }
          ListFooterComponent={
            <View style={styles.explorationFooter}>
              <TouchableOpacity
                style={styles.prefContinueButton}
                onPress={handleConfirmExplorationReview}
              >
                <Text style={styles.prefContinueButtonText}>Continue</Text>
              </TouchableOpacity>
            </View>
          }
          contentContainerStyle={styles.explorationList}
          showsVerticalScrollIndicator={false}
        />
      </SafeAreaView>
    );
  }

  if (
    isOnboardingActive &&
    isRecommendationFlowVisible &&
    (recommendationState === "idle" || recommendationState === "running")
  ) {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.explorationWrapper}>
          <ActivityIndicator size="large" color="#00a651" />
          <Text style={styles.explorationProcessingTitle}>
            Building your recommended meals…
          </Text>
          <Text style={styles.explorationProcessingSubtitle}>
            We’re combining your likes, dislikes, and saved tags to craft the first home feed.
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  if (
    isOnboardingActive &&
    isRecommendationFlowVisible &&
    recommendationState === "error"
  ) {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <View style={styles.explorationWrapper}>
          <Text style={styles.prefCategoryTitle}>Unable to build recommendations</Text>
          <Text style={styles.prefCategorySubtitle}>
            {recommendationError ??
              "Something went wrong while creating your feed. Please try again."}
          </Text>
          <TouchableOpacity
            style={[styles.prefContinueButton, { marginTop: 24 }]}
            onPress={handleRecommendationRetry}
          >
            <Text style={styles.prefContinueButtonText}>Try again</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.prefRedoButton}
            onPress={handleSkipRecommendationFlow}
          >
            <Text style={styles.prefRedoButtonText}>Skip for now</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (
    isOnboardingActive &&
    isRecommendationFlowVisible &&
    recommendationState === "ready"
  ) {
    return (
      <SafeAreaView style={styles.preferencesSafeArea}>
        <StatusBar style="dark" />
        <FlatList
          data={recommendationMeals}
          keyExtractor={(item) => `${item.mealId}-${item.rank}`}
          renderItem={renderRecommendationMeal}
          ListHeaderComponent={
            <View style={styles.recommendationHeader}>
              <Text style={styles.prefCategoryTitle}>
                Here’s your starter lineup
              </Text>
              <Text style={styles.prefCategorySubtitle}>
                We prioritized meals you’re likely to enjoy while keeping cuisines and prep times varied.
              </Text>
              {recommendationNotes?.length ? (
                <View style={styles.recommendationNotes}>
                  {recommendationNotes.map((note, index) => (
                    <Text key={`rec-note-${index}`} style={styles.recommendationNoteText}>
                      • {note}
                    </Text>
                  ))}
                </View>
              ) : null}
            </View>
          }
          ListFooterComponent={
            <View style={styles.recommendationFooter}>
              <TouchableOpacity
                style={styles.prefContinueButton}
                onPress={handleRecommendationComplete}
              >
                <Text style={styles.prefContinueButtonText}>Continue to home</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.prefRedoButton}
                onPress={handleRecommendationRetry}
              >
                <Text style={styles.prefRedoButtonText}>Refresh list</Text>
              </TouchableOpacity>
            </View>
          }
          ListEmptyComponent={
            <View style={styles.recommendationEmptyState}>
              <Text style={styles.recommendationEmptyTitle}>No meals to show</Text>
              <Text style={styles.recommendationEmptySubtitle}>
                We couldn’t map your feedback to new meals yet. Try refreshing the list.
              </Text>
            </View>
          }
          contentContainerStyle={styles.recommendationList}
          showsVerticalScrollIndicator={false}
        />
      </SafeAreaView>
    );
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
                  const servingsValue =
                    mealServings[meal.mealId] ?? DEFAULT_HOME_MEAL_SERVINGS;
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
                          <TouchableOpacity
                            style={styles.homeMealServingsButton}
                            onPress={(event) => {
                              event?.stopPropagation?.();
                              handleHomeMealServingsDecrease(meal.mealId);
                            }}
                            accessibilityRole="button"
                            accessibilityLabel="Decrease servings"
                          >
                            <Text style={styles.homeMealServingsButtonText}>-</Text>
                          </TouchableOpacity>
                          <View style={styles.homeMealServingsValue}>
                            <Text style={styles.homeMealServingsValueText}>
                              {servingsValue}
                            </Text>
                          </View>
                          <TouchableOpacity
                            style={styles.homeMealServingsButton}
                            onPress={(event) => {
                              event?.stopPropagation?.();
                              handleHomeMealServingsIncrease(meal.mealId);
                            }}
                            accessibilityRole="button"
                            accessibilityLabel="Increase servings"
                          >
                            <Text style={styles.homeMealServingsButtonText}>+</Text>
                          </TouchableOpacity>
                        </View>
                        <View style={styles.homeMealActionGroup}>
                          <TouchableOpacity
                            style={[
                              styles.homeMealDislikeButton,
                              isDisliked && styles.homeMealDislikeButtonActive,
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
            onPress={handleOpenIngredientsScreen}
          >
            <Text style={styles.welcomeButtonText}>Next</Text>
          </TouchableOpacity>
        </View>
        {mealMenuOverlay}
        {homeMealModal.visible && homeMealModal.meal ? (
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
                {homeMealModal.meal.name ?? "Meal"}
              </Text>
              {homeMealModal.meal.description ? (
                <Text style={styles.mealDetailDescription}>
                  {homeMealModal.meal.description}
                </Text>
              ) : null}
              {Array.isArray(homeMealModal.meal.prepSteps) &&
              homeMealModal.meal.prepSteps.length > 0 ? (
                <View style={styles.mealDetailSection}>
                  <Text style={styles.mealDetailSectionTitle}>Prep Steps</Text>
                  {homeMealModal.meal.prepSteps.map((step, idx) => (
                    <Text key={`prep-${idx}`} style={styles.mealDetailSectionItem}>
                      {idx + 1}. {step}
                    </Text>
                  ))}
                </View>
              ) : null}
              {Array.isArray(homeMealModal.meal.cookSteps) &&
              homeMealModal.meal.cookSteps.length > 0 ? (
                <View style={styles.mealDetailSection}>
                  <Text style={styles.mealDetailSectionTitle}>Cooking Steps</Text>
                  {homeMealModal.meal.cookSteps.map((step, idx) => (
                    <Text key={`cook-${idx}`} style={styles.mealDetailSectionItem}>
                      {idx + 1}. {step}
                    </Text>
                  ))}
                </View>
              ) : null}
              {Array.isArray(homeMealModal.meal.ingredients) &&
              homeMealModal.meal.ingredients.length > 0 ? (
                <View style={styles.mealDetailSection}>
                  <Text style={styles.mealDetailSectionTitle}>Ingredients</Text>
                  {homeMealModal.meal.ingredients.map((ingredient, idx) => {
                    const parts = [];
                    if (ingredient.quantity) {
                      parts.push(String(ingredient.quantity));
                    }
                    if (ingredient.name) {
                      parts.push(ingredient.name);
                    }
                    if (ingredient.preparation) {
                      parts.push(`(${ingredient.preparation})`);
                    }
                    const label = parts.length ? parts.join(" ") : `Ingredient ${idx + 1}`;
                    const product = ingredient.productName
                      ? ` – ${ingredient.productName}`
                      : "";
                    return (
                      <Text
                        key={`detail-ingredient-${idx}`}
                        style={styles.mealDetailSectionItem}
                      >
                        • {label}
                        {product}
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
      ) : null}
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
          <ScrollView
            style={styles.ingredientsList}
            contentContainerStyle={styles.ingredientsListContent}
            showsVerticalScrollIndicator={false}
          >
            {selectedMealIngredients.length > 0 ? (
              selectedMealIngredients.map((ingredient) => {
                const storedQuantity = ingredientQuantities?.[ingredient.id];
                const numericQuantity =
                  typeof storedQuantity === "number" && Number.isFinite(storedQuantity)
                    ? storedQuantity
                    : ingredient.requiredQuantity ?? 1;
                const displayQuantity = formatIngredientQuantity(numericQuantity);
                const disableDecrease = numericQuantity <= 0;
                return (
                  <View key={ingredient.id} style={styles.ingredientsListItem}>
                    <Text style={styles.ingredientsItemText}>{ingredient.text}</Text>
                    <View style={styles.ingredientsQuantityRow}>
                      <TouchableOpacity
                        style={[
                          styles.ingredientsQuantityButton,
                          disableDecrease && styles.ingredientsQuantityButtonDisabled,
                        ]}
                        onPress={() => handleIngredientQuantityDecrease(ingredient.id)}
                        accessibilityRole="button"
                        accessibilityLabel={`Decrease quantity for ${ingredient.text}`}
                        disabled={disableDecrease}
                      >
                        <Text
                          style={[
                            styles.ingredientsQuantityButtonText,
                            disableDecrease && styles.ingredientsQuantityButtonTextDisabled,
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
                        style={styles.ingredientsQuantityButton}
                        onPress={() => handleIngredientQuantityIncrease(ingredient.id)}
                        accessibilityRole="button"
                        accessibilityLabel={`Increase quantity for ${ingredient.text}`}
                      >
                        <Text style={styles.ingredientsQuantityButtonText}>+</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                );
              })
            ) : (
              <View style={styles.ingredientsEmptyState}>
                <Text style={styles.ingredientsEmptyText}>
                  Select meals on the previous screen to see their ingredients here.
                </Text>
              </View>
            )}
          </ScrollView>
          <View style={styles.ingredientsButtonGroup}>
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={handleOpenShoppingListConfirm}
            >
              <Text style={styles.welcomeButtonText}>Get Shopping List (Use a free use)</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.welcomeButton, styles.mealHomeCtaButton, styles.welcomeCtaButton]}
              onPress={handleOpenWoolworthsCartConfirm}
            >
              <Text style={styles.welcomeButtonText}>Add to Woolworths Cart (Use a free use)</Text>
            </TouchableOpacity>
          </View>
        </View>
        {mealMenuOverlay}
        {confirmationDialog.visible ? (
          <View style={styles.mealDetailModalContainer} pointerEvents="box-none">
            <TouchableWithoutFeedback onPress={handleCloseConfirmationDialog}>
              <View style={styles.mealDetailBackdrop} />
            </TouchableWithoutFeedback>
            <View style={styles.mealDetailCard}>
              <View style={styles.confirmModalContent}>
                <Text style={styles.mealDetailTitle}>Please confirm</Text>
                <Text style={styles.confirmSubtitle}>use a free use</Text>
              </View>
              <TouchableOpacity
                style={styles.confirmAcceptButton}
                onPress={handleConfirmDialog}
                accessibilityRole="button"
                accessibilityLabel="Confirm action"
              >
                <Text style={styles.confirmAcceptText}>✓</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.mealDetailCloseButton}
                onPress={handleCloseConfirmationDialog}
                accessibilityRole="button"
                accessibilityLabel="Dismiss confirmation"
              >
                <Text style={styles.mealDetailCloseText}>×</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : null}
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
  ingredientsListItem: {
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 14,
    marginBottom: 10,
    gap: 12,
    ...SHADOW.card,
  },
  ingredientsItemText: {
    fontSize: 14,
    color: "#1c1c1c",
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
  ingredientsButtonGroup: {
    paddingTop: 8,
    gap: 12,
    alignSelf: "stretch",
    width: "100%",
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
  explorationList: {
    paddingBottom: 60,
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
  explorationNotes: {
    marginTop: 14,
    backgroundColor: "#f2fbff",
    borderRadius: 14,
    padding: 12,
  },
  explorationNoteText: {
    fontSize: 13,
    color: "#1d3752",
  },
  recommendationHeader: {
    paddingHorizontal: 24,
    paddingBottom: 8,
  },
  recommendationList: {
    paddingBottom: 60,
  },
  recommendationCard: {
    marginHorizontal: 24,
    marginBottom: 18,
    backgroundColor: "#ffffff",
    borderRadius: 22,
    padding: 20,
    ...SHADOW.card,
  },
  recommendationHeading: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginBottom: 8,
  },
  recommendationRankBadge: {
    width: 42,
    height: 42,
    borderRadius: 12,
    backgroundColor: "#e8f6ec",
    alignItems: "center",
    justifyContent: "center",
  },
  recommendationRankText: {
    fontSize: 18,
    fontWeight: "700",
    color: "#0f3c27",
  },
  recommendationTitleGroup: {
    flex: 1,
  },
  recommendationMealName: {
    fontSize: 18,
    fontWeight: "700",
    color: "#0f3c27",
  },
  recommendationConfidence: {
    marginTop: 2,
    fontSize: 13,
    color: "#2f5b42",
  },
  recommendationDescription: {
    fontSize: 14,
    color: "#445248",
    marginBottom: 10,
    lineHeight: 20,
  },
  recommendationTagRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 10,
  },
  recommendationTagChip: {
    backgroundColor: "#eef5f0",
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  recommendationTagText: {
    fontSize: 12,
    color: "#1b4a33",
  },
  recommendationRationale: {
    fontSize: 14,
    color: "#1e3529",
    lineHeight: 20,
    marginBottom: 8,
  },
  recommendationAxes: {
    marginTop: 4,
    gap: 2,
  },
  recommendationAxisText: {
    fontSize: 12,
    color: "#506457",
  },
  recommendationNotes: {
    marginTop: 14,
    backgroundColor: "#f5f8ff",
    borderRadius: 14,
    padding: 12,
  },
  recommendationNoteText: {
    fontSize: 13,
    color: "#1d2f52",
  },
  recommendationFooter: {
    paddingHorizontal: 24,
    paddingBottom: 36,
    paddingTop: 12,
    gap: 12,
  },
  recommendationEmptyState: {
    paddingHorizontal: 24,
    paddingVertical: 32,
    alignItems: "center",
    gap: 8,
  },
  recommendationEmptyTitle: {
    fontSize: 16,
    fontWeight: "600",
    color: "#122e21",
  },
  recommendationEmptySubtitle: {
    fontSize: 14,
    color: "#3f5c4b",
    textAlign: "center",
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
  prefLogicCard: {
    backgroundColor: "#f0f7f2",
    borderRadius: 18,
    padding: 16,
    borderWidth: 1,
    borderColor: "#d5e8dc",
    gap: 6,
  },
  prefLogicText: {
    fontSize: 13,
    color: "#2a4838",
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
  homeMealServingsButton: {
    width: 32,
    height: 32,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#d8e3db",
    backgroundColor: "#f4f7f5",
    alignItems: "center",
    justifyContent: "center",
  },
  homeMealServingsButtonText: {
    fontSize: 18,
    fontWeight: "700",
    color: "#0c3c26",
  },
  homeMealServingsValue: {
    minWidth: 40,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#d8e3db",
    backgroundColor: "#fff",
    alignItems: "center",
  },
  homeMealServingsValueText: {
    fontSize: 16,
    fontWeight: "700",
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
    borderColor: "#ff4d4f",
    backgroundColor: "#fff5f5",
    alignItems: "center",
    justifyContent: "center",
  },
  homeMealDislikeButtonActive: {
    backgroundColor: "#ff4d4f",
  },
  homeMealDislikeButtonIcon: {
    fontSize: 18,
    color: "#d93025",
  },
  homeMealDislikeButtonIconActive: {
    color: "#fff",
  },
  homeMealChooseButton: {
    marginLeft: 0,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: "#00a651",
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: "#fff",
  },
  homeMealChooseButtonActive: {
    backgroundColor: "#00a651",
  },
  homeMealChooseButtonText: {
    fontSize: 14,
    fontWeight: "600",
    color: "#00a651",
  },
  homeMealChooseButtonTextActive: {
    color: "#fff",
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
