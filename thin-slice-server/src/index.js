const express = require("express");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
const seedrandom = require("seedrandom");
const { v4: uuidv4 } = require("uuid");

const PORT = Number(process.env.PORT) || 4010;
const DEFAULT_COUNT = 100;
const MAX_COUNT = 250;
const CATALOG_PATH = path.resolve(__dirname, "../../resolver/catalog.json");
const ORDERS_PATH = path.resolve(__dirname, "../orders.json");
const RUNNER_LOG_PATH = path.resolve(__dirname, "../runner-log.txt");

let catalogItems = [];
let orders = [];
let isSavingOrders = false;

function loadCatalog() {
  try {
    const raw = fs.readFileSync(CATALOG_PATH, "utf-8");
    const parsed = JSON.parse(raw);
    const entries = Object.entries(parsed || {});
    catalogItems = entries
      .map(([key, value]) => {
        if (typeof value !== "object" || value === null) {
          return null;
        }
        const productId = value.productId ?? null;
        const catalogRefId = value.catalogRefId ?? null;
        const detailUrl =
          value.detailUrl ??
          value.detailURL ??
          value.url ??
          value.productUrl ??
          value.link ??
          value.href ??
          (productId
            ? `https://www.woolworths.co.za/prod/_/A-${productId}`
            : catalogRefId
            ? `https://www.woolworths.co.za/prod/_/A-${catalogRefId}`
            : null);
        return {
          key,
          productId,
          catalogRefId,
          title: value.name ?? key,
          qty: 1,
          url: detailUrl,
          detailUrl,
          price: value.price ?? value.pricePerUnit ?? null,
          imageUrl:
            value.image ??
            value.imageUrl ??
            value.thumbnail ??
            value.primaryImage ??
            null,
          metadata: value,
        };
      })
      .filter(Boolean);
    console.log(
      `[thin-slice-server] Loaded ${catalogItems.length} catalog entries`
    );
  } catch (error) {
    console.error(
      `[thin-slice-server] Failed to load catalog from ${CATALOG_PATH}:`,
      error.message
    );
    catalogItems = [];
  }
}

function loadOrders() {
  try {
    if (!fs.existsSync(ORDERS_PATH)) {
      orders = [];
      return;
    }
    const raw = fs.readFileSync(ORDERS_PATH, "utf-8");
    if (!raw) {
      orders = [];
      return;
    }
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      orders = parsed;
    } else {
      orders = [];
    }
  } catch (error) {
    console.error(
      `[thin-slice-server] Failed to load orders from ${ORDERS_PATH}:`,
      error.message
    );
    orders = [];
  }
}

function persistOrders() {
  if (isSavingOrders) {
    return;
  }
  isSavingOrders = true;
  fs.promises
    .writeFile(ORDERS_PATH, JSON.stringify(orders, null, 2), "utf-8")
    .catch((error) => {
      console.error(
        `[thin-slice-server] Failed to persist orders to ${ORDERS_PATH}:`,
        error.message
      );
    })
    .finally(() => {
      isSavingOrders = false;
    });
}

function shuffleItems(items, count, seed) {
  const rng = seed ? seedrandom(seed) : () => Math.random();
  const copy = items.slice();
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const randomValue = rng();
    const j = Math.floor(randomValue * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy.slice(0, count);
}

function parseCount(raw) {
  const requested = Number.parseInt(raw, 10);
  if (Number.isNaN(requested) || requested <= 0) {
    return DEFAULT_COUNT;
  }
  return Math.min(requested, MAX_COUNT);
}

const app = express();
app.use(cors());
app.use(express.json({ limit: "1mb" }));

app.get("/health", (_req, res) => {
  const pendingOrders = orders.filter((order) => order.status === "pending")
    .length;
  res.json({
    ok: true,
    catalogSize: catalogItems.length,
    pendingOrders,
  });
});

app.get("/products/random", (req, res) => {
  if (!catalogItems.length) {
    return res
      .status(500)
      .json({ error: "Catalog unavailable. Check resolver/catalog.json." });
  }

  const count = parseCount(req.query.count);
  const seed = req.query.seed ? String(req.query.seed) : null;
  const selection = shuffleItems(catalogItems, count, seed);
  return res.json({
    count: selection.length,
    seed: seed ?? undefined,
    items: selection,
  });
});

app.post("/logs/runner/reset", (_req, res) => {
  try {
    fs.writeFileSync(RUNNER_LOG_PATH, "", "utf-8");
    res.json({ ok: true, path: RUNNER_LOG_PATH });
  } catch (error) {
    console.error(
      `[thin-slice-server] Failed to reset runner log:`,
      error.message
    );
    res.status(500).json({ error: "log_reset_failed" });
  }
});

app.post("/logs/runner/append", (req, res) => {
  const line =
    req.body && typeof req.body.line === "string" ? req.body.line : null;
  if (!line) {
    return res.status(400).json({ error: "invalid_line" });
  }
  try {
    fs.appendFileSync(RUNNER_LOG_PATH, `${line}\n`, "utf-8");
    res.json({ ok: true });
  } catch (error) {
    console.error(
      `[thin-slice-server] Failed to append runner log:`,
      error.message
    );
    res.status(500).json({ error: "log_append_failed" });
  }
});

app.post("/orders/place", (req, res) => {
  const payload = req.body ?? {};
  const items = Array.isArray(payload.items) ? payload.items : [];
  console.log(
    `[thin-slice-server] Received place order stub request for ${items.length} items`
  );
  if (!items.length) {
    return res.status(400).json({
      error: "no_items",
      message: "Order requires at least one item.",
    });
  }

  const orderId = uuidv4();
  const record = {
    id: orderId,
    status: "pending",
    createdAt: new Date().toISOString(),
    source: payload.metadata?.source ?? "thin-slice-app",
    items,
    metadata: {
      requestedAt: payload.metadata?.requestedAt ?? null,
      deviceId: payload.metadata?.deviceId ?? null,
    },
  };
  orders.push(record);
  persistOrders();

  res.json({
    status: "queued",
    orderId,
    redirectUrl: "https://www.woolworths.co.za/login",
    message:
      "Order hand-off queued. Log into Woolworths in the browser to complete checkout.",
  });
});

app.get("/orders/next", (req, res) => {
  const workerId = req.query.workerId ? String(req.query.workerId) : null;
  const nextOrder = orders.find((order) => order.status === "pending");
  if (!nextOrder) {
    return res.status(204).end();
  }

  nextOrder.status = "claimed";
  nextOrder.claimedAt = new Date().toISOString();
  nextOrder.claimedBy = workerId;
  persistOrders();

  res.json({
    orderId: nextOrder.id,
    items: nextOrder.items,
    metadata: nextOrder.metadata,
    createdAt: nextOrder.createdAt,
  });
});

app.post("/orders/:orderId/ack", (req, res) => {
  const { orderId } = req.params;
  const order = orders.find((item) => item.id === orderId);
  if (!order) {
    return res.status(404).json({ error: "order_not_found" });
  }

  const { status, error, processedItems } = req.body ?? {};
  if (status && !["completed", "failed"].includes(status)) {
    return res.status(400).json({ error: "invalid_status" });
  }

  order.status = status ?? "completed";
  order.completedAt = new Date().toISOString();
  order.result = {
    error: error ?? null,
    processedItems: processedItems ?? null,
  };
  persistOrders();

  res.json({ ok: true });
});

app.get("/orders/:orderId", (req, res) => {
  const { orderId } = req.params;
  const order = orders.find((item) => item.id === orderId);
  if (!order) {
    return res.status(404).json({ error: "order_not_found" });
  }
  res.json(order);
});

loadCatalog();
fs.watchFile(CATALOG_PATH, { interval: 5000 }, () => {
  console.log("[thin-slice-server] Detected catalog change. Reloading...");
  loadCatalog();
});
loadOrders();

if (require.main === module) {
  app.listen(PORT, () => {
    console.log(
      `[thin-slice-server] Listening on http://localhost:${PORT} (catalog: ${CATALOG_PATH})`
    );
  });
}

module.exports = {
  app,
  loadCatalog,
  getCatalogItems: () => catalogItems.slice(),
  shuffleItems,
};
