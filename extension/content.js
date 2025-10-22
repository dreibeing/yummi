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
  if (item.productId) return String(item.productId);
  if (item.catalogRefId) return String(item.catalogRefId);
  if (item.raw) {
    if (item.raw.productId) return String(item.raw.productId);
    if (item.raw.catalogRefId) return String(item.raw.catalogRefId);
    if (item.raw.sku) return String(item.raw.sku);
  }
  if (item.sku) return String(item.sku);
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
  let catalogRefId =
    item.catalogRefId ||
    (item.raw && (item.raw.catalogRefId || item.raw.productId)) ||
    productId;
  if (catalogRefId != null) {
    catalogRefId = String(catalogRefId);
    if (productId && catalogRefId !== productId) {
      // Prefer the current productId when catalogRefId is stale/mismatched.
      catalogRefId = productId;
    }
  }
  const payload = {
    deliveryType,
    fromDeliverySelectionPopup: 'true',
    items: [
      {
        productId,
        catalogRefId,
        quantity,
        itemListName:
          item.itemListName ||
          (item.raw && item.raw.itemListName) ||
          'Extension'
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
        'x-requested-by': 'Woolworths Online',
        'x-requested-with': 'XMLHttpRequest',
        'accept': 'application/json, text/javascript, */*; q=0.01'
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
    const formExceptions =
      Array.isArray(data?.formExceptions)
        ? data.formExceptions
        : Array.isArray(data?.formexceptions)
        ? data.formexceptions
        : [];
    if (formExceptions.length) {
      const firstException = formExceptions[0];
      let message = '';
      if (typeof firstException === 'string') {
        message = firstException;
      } else {
        try {
          message = JSON.stringify(firstException);
        } catch (error) {
          message = String(firstException);
        }
      }
      return { status: 'failed', reason: `form_exception:${message}` };
    }
    const hasBasketId =
      typeof data?.basketId === 'string' && data.basketId.trim().length > 0;
    const hasGroupSubtotal =
      data?.groupSubTotal && typeof data.groupSubTotal === 'object';
    const hasItemsArray = Array.isArray(data?.items) && data.items.length > 0;
    const statusField =
      (data && data.status) ||
      (data && data.result && data.result.status) ||
      '';
    const normalizedStatus = String(statusField || '').toUpperCase();
    const success =
      data?.success === true ||
      normalizedStatus === 'SUCCESS' ||
      normalizedStatus === 'OK' ||
      typeof (data?.cartData && data.cartData.cartTotal) === 'number' ||
      typeof (data?.cartSummary && data.cartSummary.total) === 'number' ||
      hasBasketId ||
      hasGroupSubtotal ||
      hasItemsArray;
    if (!success) {
      return { status: 'failed', reason: 'no_success_flag' };
    }
    return { status: 'ok' };
  } catch (err) {
    return { status: 'failed', reason: 'xhr_exception' };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg?.type === 'CONTENT_ADD_ITEM') {
      const item = msg.item || {};
      const context = msg.context || {};
      const res = await tryAddViaXHR(item, context);
      sendResponse(res);
    }
  })();
  return true;
});




