'use strict';

const CART_ENDPOINT = 'https://www.woolworths.co.za/server/cartAddItems';

function getCookie(name) {
  return document.cookie
    .split(';')
    .map(v => v.trim())
    .find(v => v.startsWith(`${name}=`))
    ?.split('=')[1] || '';
}

function decodeValue(value) {
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

function parseDeliveryString(str) {
  if (!str) return {};
  const parts = str.split('|');
  const deliveryType = parts[0] || 'Standard';
  const placeId = sanitizePlaceId(parts[1] || '');
  return { deliveryType, placeId };
}

function resolveDeliveryContext(context) {
  let deliveryType = context?.deliveryType || '';
  let placeId = context?.placeId || '';
  let storeId = context?.storeId || '';
  if (!placeId || !deliveryType) {
    const userDelivery = parseDeliveryString(decodeValue(getCookie('userDelivery')));
    deliveryType = deliveryType || userDelivery.deliveryType;
    placeId = placeId || userDelivery.placeId;
  }
  if (!placeId || !deliveryType) {
    const locationCookie = parseDeliveryString(decodeValue(getCookie('location')));
    deliveryType = deliveryType || locationCookie.deliveryType;
    placeId = placeId || locationCookie.placeId;
  }
  if (!storeId) {
    storeId = getCookie('storeId') || context?.storeId || '';
  }
  return {
    deliveryType: deliveryType || 'Standard',
    placeId: placeId || '',
    storeId: storeId || ''
  };
}

function extractProductId(item) {
  if (item.sku) return String(item.sku);
  if (item.productId) return String(item.productId);
  if (item.url) {
    const matchA = item.url.match(/A-(\d{6,})/i);
    if (matchA) return matchA[1];
    const matchDigits = item.url.match(/(\d{6,})[^\d]*$/);
    if (matchDigits) return matchDigits[1];
  }
  return null;
}

function buildCartPayload(item, context) {
  const { deliveryType, placeId, storeId } = resolveDeliveryContext(context);
  const productId = extractProductId(item);
  if (!productId) {
    return { ok: false, reason: 'no_product_id' };
  }
  if (!placeId) {
    return { ok: false, reason: 'missing_place_id' };
  }
  const quantity = Math.max(1, Number(item.qty) || 1);
  const payload = {
    deliveryType,
    fromDeliverySelectionPopup: 'true',
    items: [
      {
        productId,
        catalogRefId: productId,
        quantity,
        itemListName: item.itemListName || 'Extension'
      }
    ]
  };
  if (placeId) {
    payload.address = { placeId };
  }
  if (storeId) {
    payload.storeId = storeId;
  }
  return { ok: true, payload };
}

async function tryAddViaXHR(item, context) {
  const build = buildCartPayload(item, context);
  if (!build.ok) {
    return { status: 'failed', reason: build.reason };
  }
  try {
    const response = await fetch(CART_ENDPOINT, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-requested-by': 'Woolworths Online'
      },
      credentials: 'include',
      body: JSON.stringify(build.payload)
    });
    const rawText = await response.text();
    if (!response.ok) {
      let parsedReason = `http_${response.status}`;
      try {
        const data = JSON.parse(rawText || '{}');
        if (data?.errorMessages?.length) {
          parsedReason = data.errorMessages.join(',');
        } else if (data?.message) {
          parsedReason = data.message;
        }
      } catch (_) {
        if (rawText) {
          parsedReason = `${parsedReason}:${rawText.slice(0, 120)}`;
        }
      }
      return { status: 'failed', reason: parsedReason };
    }
    let data = {};
    try {
      data = rawText ? JSON.parse(rawText) : {};
    } catch (_) {
      data = {};
    }
    if (data?.errorMessages?.length) {
      return { status: 'failed', reason: data.errorMessages.join(',') };
    }
    return { status: 'ok' };
  } catch (err) {
    return { status: 'failed', reason: 'xhr_exception' };
  }
}

function waitFor(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function navigateTo(url) {
  if (!url) return;
  if (location.href !== url) {
    location.assign(url);
    await new Promise(resolve => {
      const done = () => resolve();
      window.addEventListener('load', done, { once: true });
      setTimeout(done, 8000);
    });
  }
}

function findAddToCartButton() {
  const candidates = Array.from(document.querySelectorAll('button, [role="button"]'));
  const btn = candidates.find(el => /add to cart|add/i.test(el.textContent || ''));
  return btn || null;
}

async function tryAddViaDOM(item) {
  try {
    if (!item.url) return { status: 'failed', reason: 'no_url' };
    await navigateTo(item.url);
    const qtyInput = document.querySelector('input[type="number"], input[name*="qty" i]');
    if (qtyInput && item.qty && Number.isFinite(Number(item.qty))) {
      qtyInput.value = String(item.qty);
      qtyInput.dispatchEvent(new Event('input', { bubbles: true }));
      await waitFor(200);
    }
    const btn = findAddToCartButton();
    if (!btn) return { status: 'failed', reason: 'no_add_button' };
    btn.click();
    await waitFor(1500);
    return { status: 'ok' };
  } catch (e) {
    return { status: 'failed', reason: 'dom_exception' };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg?.type === 'CONTENT_ADD_ITEM') {
      const item = msg.item || {};
      const context = msg.context || {};
      let res = await tryAddViaXHR(item, context);
      if (res.status !== 'ok') {
        res = await tryAddViaDOM(item);
      }
      sendResponse(res);
    }
  })();
  return true;
});
