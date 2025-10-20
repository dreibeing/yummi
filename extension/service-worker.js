'use strict';

// State in storage for resilience across popup closes.
const STORAGE_KEYS = {
  QUEUE: 'queue',
  INDEX: 'index',
  RUNNING: 'running',
  RESULTS: 'results',
  START_TS: 'startTs',
  CONTEXT: 'context'
};

const DOMAIN = 'https://www.woolworths.co.za';

function now() { return performance.now(); }

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function getState(keys) {
  const out = await chrome.storage.local.get(keys);
  return out;
}

async function setState(obj) {
  await chrome.storage.local.set(obj);
}

function jitter(min, max) {
  return Math.random() * (max - min) + min;
}

function decodeValue(value) {
  if (!value) return '';
  try {
    return decodeURIComponent(value);
  } catch (_) {
    return value;
  }
}

function sanitizePlaceId(pid) {
  if (!pid) return '';
  return pid.replace(/^"+|"+$/g, '');
}

async function getCookieValue(name) {
  try {
    const cookie = await chrome.cookies.get({ url: DOMAIN, name });
    return cookie?.value || '';
  } catch (_) {
    return '';
  }
}

function parseDeliveryString(str) {
  if (!str) return {};
  const parts = str.split('|');
  const deliveryType = parts[0] || 'Standard';
  const placeId = sanitizePlaceId(parts[1] || '');
  return { deliveryType, placeId };
}

async function buildContext() {
  const userDeliveryRaw = decodeValue(await getCookieValue('userDelivery'));
  let { deliveryType, placeId } = parseDeliveryString(userDeliveryRaw);
  if (!placeId) {
    const locationRaw = decodeValue(await getCookieValue('location'));
    const parsed = parseDeliveryString(locationRaw);
    deliveryType = deliveryType || parsed.deliveryType;
    placeId = placeId || parsed.placeId;
  }
  const storeId = await getCookieValue('storeId');
  return {
    deliveryType: deliveryType || 'Standard',
    placeId: placeId || '',
    storeId: storeId || ''
  };
}

async function sendPopup(type, payload) {
  try {
    chrome.runtime.sendMessage({ type, ...(payload ? payload : {}) });
  } catch (e) {
    // Ignore if popup not open.
  }
}

async function logToPopup(text) {
  await sendPopup('POPUP_LOG', { text });
}

async function updateStatus() {
  const { results = [], queue = [], startTs = 0 } = await getState(['results', 'queue', 'startTs']);
  const ok = results.filter(r => r.status === 'ok').length;
  const failed = results.filter(r => r.status === 'failed').length;
  const total = queue.length || 0;
  const elapsedMs = startTs ? (now() - startTs) : 0;
  await sendPopup('POPUP_STATUS', { data: { ok, failed, total, elapsedMs } });
}

async function ensureWoolworthsTab(tabIdHint) {
  if (tabIdHint) {
    try {
      const tab = await chrome.tabs.get(tabIdHint);
      if (tab && tab.url && tab.url.includes('woolworths.co.za')) return tab.id;
    } catch (_) {}
  }
  // Find existing tab on domain
  const tabs = await chrome.tabs.query({ url: '*://www.woolworths.co.za/*' });
  if (tabs && tabs.length) return tabs[0].id;
  // Open a new tab
  const tab = await chrome.tabs.create({ url: 'https://www.woolworths.co.za/' });
  return tab.id;
}

async function addItemViaContent(tabId, item, context) {
  return new Promise((resolve) => {
    const msg = { type: 'CONTENT_ADD_ITEM', item, context };
    chrome.tabs.sendMessage(tabId, msg, (resp) => {
      resolve(resp || { status: 'failed', reason: 'no_response' });
    });
  });
}

async function processBatch(tabId, items, context) {
  // Process up to N concurrently with jitter between starts
  const MAX_CONC = 3;
  let i = 0;
  const results = [];
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      const item = items[idx];
      await sleep(jitter(300, 800));
      const started = now();
      let res;
      try {
        res = await addItemViaContent(tabId, item, context);
      } catch (e) {
        res = { status: 'failed', reason: 'exception' };
      }
      const duration_ms = now() - started;
      const status = res?.status || 'failed';
      const reason = res?.reason;
      results.push({ ...item, status, reason, duration_ms });
      if (status !== 'ok') {
        await logToPopup(`Item ${item.title || item.sku || item.idx}: ${status}${reason ? ` (${reason})` : ''}`);
      }
      await updateStatus();
    }
  }
  await Promise.all(Array.from({ length: Math.min(MAX_CONC, items.length) }, worker));
  return results;
}

async function runQueue(tabId, context) {
  const { queue = [] } = await getState(['queue']);
  await logToPopup(`Processing ${queue.length} items...`);
  let cursor = 0;
  const allResults = [];
  while (cursor < queue.length) {
    const batch = queue.slice(cursor, cursor + 5);
    const res = await processBatch(tabId, batch, context);
    allResults.push(...res);
    cursor += batch.length;
    await setState({ results: allResults });
    await sleep(jitter(500, 1000));
  }
  await logToPopup('Done. Opening cart...');
  await updateStatus();
  await chrome.tabs.update(tabId, { url: 'https://www.woolworths.co.za/check-out/cart' });
  await setState({ running: false, [STORAGE_KEYS.CONTEXT]: {} });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg?.type) {
      case 'START_FILL': {
        const { payload, tabId } = msg;
        const queue = (payload?.items || []).map((x, i) => ({
          idx: i,
          title: x.title || '',
          url: x.url || '',
          sku: x.sku || '',
          qty: Math.max(1, x.qty || 1)
        }));
        const context = await buildContext();
        await setState({ queue, index: 0, running: true, results: [], startTs: now(), [STORAGE_KEYS.CONTEXT]: context });
        await logToPopup(`Context delivery=${context.deliveryType} placeId=${context.placeId ? 'set' : 'missing'} store=${context.storeId || 'n/a'}`);
        await updateStatus();
        const tid = await ensureWoolworthsTab(tabId);
        runQueue(tid, context);
        sendResponse({ ok: true, context });
        break;
      }
      case 'STOP_FILL': {
        await setState({ running: false, [STORAGE_KEYS.CONTEXT]: {} });
        await logToPopup('Stopped.');
        await updateStatus();
        sendResponse({ ok: true });
        break;
      }
      default:
        break;
    }
  })();
  return true;
});
