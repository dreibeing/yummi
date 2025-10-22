export const createExtensionRuntimeScript = (config) =>
  String.raw`
(function () {
  const CART_ENDPOINT = 'https://www.woolworths.co.za/server/cartAddItems';

  function getCookie(name) {
    var target = name + '=';
    var parts = (document.cookie || '').split(';');
    for (var i = 0; i < parts.length; i += 1) {
      var part = parts[i].trim();
      if (part.indexOf(target) === 0) {
        return part.slice(target.length);
      }
    }
    return '';
  }

  function decodeValue(value) {
    try {
      return decodeURIComponent(value);
    } catch (error) {
      return value;
    }
  }

  function sanitizePlaceId(pid) {
    if (!pid) return '';
    return pid.replace(/^"+|"+$/g, '');
  }

  function parseDeliveryString(str) {
    if (!str) return {};
    var parts = str.split('|');
    var deliveryType = parts[0] || 'Standard';
    var placeId = sanitizePlaceId(parts[1] || '');
    return { deliveryType: deliveryType, placeId: placeId };
  }

  function resolveDeliveryContext(overrideContext) {
    var override = overrideContext || {};
    var deliveryType = override.deliveryType || '';
    var placeId = override.placeId || '';
    var storeId = override.storeId || '';
    if (!placeId || !deliveryType) {
      var userDelivery = parseDeliveryString(
        decodeValue(getCookie('userDelivery'))
      );
      if (!deliveryType) deliveryType = userDelivery.deliveryType || '';
      if (!placeId) placeId = userDelivery.placeId || '';
    }
    if (!placeId || !deliveryType) {
      var locationCookie = parseDeliveryString(
        decodeValue(getCookie('location'))
      );
      if (!deliveryType) deliveryType = locationCookie.deliveryType || '';
      if (!placeId) placeId = locationCookie.placeId || '';
    }
    if (!storeId) {
      storeId = getCookie('storeId') || override.storeId || '';
    }
    return {
      deliveryType: deliveryType || 'Standard',
      placeId: placeId || '',
      storeId: storeId || '',
    };
  }

  function extractProductId(item) {
    if (!item) return null;
    if (item.productId) return String(item.productId);
    if (item.catalogRefId) return String(item.catalogRefId);
    if (item.raw) {
      if (item.raw.productId) return String(item.raw.productId);
      if (item.raw.catalogRefId) return String(item.raw.catalogRefId);
      if (item.raw.sku) return String(item.raw.sku);
    }
    if (item.sku) return String(item.sku);
    if (item.url) {
      var matchA = item.url.match(/A-(\\d{6,})/i);
      if (matchA) return matchA[1];
      var matchDigits = item.url.match(/(\\d{6,})[^\\d]*$/);
      if (matchDigits) return matchDigits[1];
    }
    return null;
  }

  function buildCartPayload(item, context) {
    var resolved = context || resolveDeliveryContext({});
    var productId = extractProductId(item);
    if (!productId) {
      return { ok: false, reason: 'no_product_id' };
    }
    if (!resolved.placeId) {
      return { ok: false, reason: 'missing_place_id' };
    }
    var quantity = Math.max(1, Number(item && item.qty) || 1);
    var catalogRefId =
      (item && item.catalogRefId) ||
      (item && item.raw && (item.raw.catalogRefId || item.raw.productId)) ||
      productId;
    if (catalogRefId !== null && catalogRefId !== undefined) {
      catalogRefId = String(catalogRefId);
      if (productId && catalogRefId !== productId) {
        catalogRefId = productId;
      }
    }
    var payload = {
      deliveryType: resolved.deliveryType,
      fromDeliverySelectionPopup: 'true',
      items: [
        {
          productId: productId,
          catalogRefId: catalogRefId,
          quantity: quantity,
          itemListName:
            (item && item.itemListName) ||
            (item && item.raw && item.raw.itemListName) ||
            'Extension',
        },
      ],
    };
    if (resolved.placeId) {
      payload.address = { placeId: resolved.placeId };
    }
  if (resolved.storeId) {
    payload.storeId = resolved.storeId;
  }
  return { ok: true, payload: payload };
}

  function summarizePayload(payload) {
    if (!payload || typeof payload !== 'object') return null;
    try {
      var firstItem =
        Array.isArray(payload.items) && payload.items.length
          ? payload.items[0]
          : null;
      var placeIdTag =
        payload.address && payload.address.placeId
          ? String(payload.address.placeId).slice(0, 6) + '...'
          : 'missing';
      var storeIdTag = payload.storeId ? String(payload.storeId) : 'n/a';
      var parts = [
        'deliveryType=' + (payload.deliveryType || 'n/a'),
        'placeId=' + placeIdTag,
        'storeId=' + storeIdTag,
      ];
      if (firstItem) {
        parts.push('productId=' + (firstItem.productId || 'n/a'));
        parts.push('catalogRefId=' + (firstItem.catalogRefId || 'n/a'));
        parts.push(
          'qty=' +
            (firstItem.quantity !== undefined ? firstItem.quantity : 'n/a')
        );
      }
      return parts.join(', ');
    } catch (error) {
      return null;
    }
  }

  function formatFormException(entry) {
    if (entry === null || entry === undefined) return '';
    if (typeof entry === 'string') return entry;
    try {
      return JSON.stringify(entry);
    } catch (error) {
      return String(entry);
    }
  }

  function summarizeResponseBody(data) {
    if (!data || typeof data !== 'object') {
      if (data === null || data === undefined) return 'null';
      return String(data).slice(0, 160);
    }
    var keys = Object.keys(data);
    if (!keys.length) return 'empty_object';
    return keys
      .slice(0, 6)
      .map(function (key) {
        var value = data[key];
        if (value === null || value === undefined) return key + ':null';
        if (typeof value === 'string') return key + ':' + value.slice(0, 40);
        if (typeof value === 'number' || typeof value === 'boolean') {
          return key + ':' + String(value);
        }
        if (Array.isArray(value)) {
          if (key && key.toLowerCase() === 'formexceptions') {
            var first =
              value.length && value[0] !== null && value[0] !== undefined
                ? formatFormException(value[0]).slice(0, 120)
                : '';
            return (
              key +
              ':[len=' +
              value.length +
              (first ? '; first=' + first : '') +
              ']'
            );
          }
          return key + ':[len=' + value.length + ']';
        }
        return key + ':{...}';
      })
      .join(', ');
  }

  async function tryAddViaXHR(item, context) {
    var build = buildCartPayload(item, context);
    if (!build.ok) {
      return { status: 'failed', reason: build.reason };
    }
    var payloadSummary = summarizePayload(build.payload);
    var metaBase = {
      payloadSummary: payloadSummary,
    };
    try {
      var response = await fetch(CART_ENDPOINT, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-requested-by': 'Woolworths Online',
          'x-requested-with': 'XMLHttpRequest',
          'accept': 'application/json, text/javascript, */*; q=0.01',
        },
        credentials: 'include',
        body: JSON.stringify(build.payload),
      });
      var rawText = await response.text();
      var meta = {
        payloadSummary: payloadSummary,
        statusCode: response.status,
        responseSummary: null,
      };
      if (!response.ok) {
        var parsedReason = 'http_' + response.status;
        try {
          var data = JSON.parse(rawText || '{}');
          if (data && data.errorMessages && data.errorMessages.length) {
            parsedReason = data.errorMessages.join(',');
          } else if (data && data.message) {
            parsedReason = data.message;
          }
        } catch (error) {
          if (rawText) {
            parsedReason = parsedReason + ':' + rawText.slice(0, 120);
          }
        }
        meta.responseSummary = rawText ? rawText.slice(0, 160) : 'no_body';
        return { status: 'failed', reason: parsedReason, meta: meta };
      }
      if (!rawText) {
        meta.responseSummary = 'empty_response';
        return { status: 'failed', reason: 'empty_response', meta: meta };
      }
      var dataObj;
      try {
        dataObj = JSON.parse(rawText);
      } catch (error) {
        meta.responseSummary = rawText.slice(0, 160);
        return { status: 'failed', reason: 'invalid_json', meta: meta };
      }
      meta.responseSummary = summarizeResponseBody(dataObj);
      if (dataObj && dataObj.errorMessages && dataObj.errorMessages.length) {
        return {
          status: 'failed',
          reason: dataObj.errorMessages.join(','),
          meta: meta,
        };
      }
      var formExceptions =
        Array.isArray(dataObj && dataObj.formExceptions)
          ? dataObj.formExceptions
          : Array.isArray(dataObj && dataObj.formexceptions)
          ? dataObj.formexceptions
          : [];
      if (formExceptions.length) {
        var formMessage = formatFormException(formExceptions[0]);
        if (formMessage) {
          meta.formExceptionMessage = formMessage;
        }
        return {
          status: 'failed',
          reason: formMessage ? 'form_exception:' + formMessage : 'form_exception',
          meta: meta,
        };
      }
      var hasBasketId =
        dataObj && typeof dataObj.basketId === 'string' && dataObj.basketId.trim().length > 0;
      var hasGroupSubtotal =
        dataObj && dataObj.groupSubTotal && typeof dataObj.groupSubTotal === 'object';
      var hasItemsArray =
        dataObj && Array.isArray(dataObj.items) && dataObj.items.length > 0;
      var statusField =
        (dataObj && dataObj.status) ||
        (dataObj && dataObj.result && dataObj.result.status) ||
        '';
      statusField = String(statusField || '').toUpperCase();
      meta.statusField = statusField;
      var success =
        dataObj.success === true ||
        statusField === 'SUCCESS' ||
        statusField === 'OK' ||
        typeof (dataObj.cartData && dataObj.cartData.cartTotal) === 'number' ||
        typeof (dataObj.cartSummary && dataObj.cartSummary.total) === 'number' ||
        hasBasketId ||
        hasGroupSubtotal ||
        hasItemsArray;
      meta.successFlag = success;
      if (!success) {
        return {
          status: 'failed',
          reason: 'no_success_flag',
          meta: meta,
        };
      }
      return { status: 'ok', meta: meta };
    } catch (error) {
      return {
        status: 'failed',
        reason: error && error.message ? error.message : 'xhr_exception',
        meta: {
          ...metaBase,
          statusCode: null,
          responseSummary: 'xhr_exception',
        },
      };
    }
  }

  function wait(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function detectLoggedInFromDom() {
    try {
      var elements = Array.prototype.slice.call(
        document.querySelectorAll('a, button, span, div')
      );
      return elements.some(function (el) {
        var text = (el.textContent || '').toLowerCase();
        if (!text) return false;
        return (
          text.indexOf('sign out') !== -1 ||
          text.indexOf('log out') !== -1 ||
          text.indexOf('hi ') !== -1 ||
          text.indexOf('my account') !== -1
        );
      });
    } catch (error) {
      return false;
    }
  }

  function isLoggedIn() {
    var cookies = document.cookie || '';
    if (cookies.indexOf('userDelivery=') !== -1) return true;
    if (cookies.indexOf('location=') !== -1) return true;
    if (cookies.indexOf('AUTHENTICATION=') !== -1) return true;
    if (cookies.indexOf('userLoginState=LOGGED_IN') !== -1) return true;
    return detectLoggedInFromDom();
  }

  function hasPlaceId(context) {
    return (
      context &&
      typeof context.placeId === 'string' &&
      context.placeId.trim().length > 0
    );
  }

  async function addItem(item, context) {
    var resolvedContext = resolveDeliveryContext(context || {});
    return await tryAddViaXHR(item, resolvedContext);
  }

  window.__thinSliceExtension = {
    addItem: addItem,
    resolveContext: function () {
      return resolveDeliveryContext({});
    },
    isLoggedIn: isLoggedIn,
    hasPlaceId: hasPlaceId,
    wait: wait,
  };
})();

(function () {
  var CONFIG = ${JSON.stringify(config)};
  var RN = window.ReactNativeWebView;
  function post(type, payload) {
    try {
      if (RN && RN.postMessage) {
        RN.postMessage(JSON.stringify({ type: type, payload: payload }));
      }
    } catch (error) {
      console.warn('postMessage failed', error);
    }
  }

  var extension = window.__thinSliceExtension;
  if (!extension) {
    post('runner:error', { reason: 'extension_init_failed' });
    return;
  }

  var queue = Array.isArray(CONFIG.items) ? CONFIG.items : [];
  var orderId = CONFIG.orderId || null;

  post('runner:init', { total: queue.length, orderId: orderId });

  function formatTitle(item, index) {
    if (!item) return 'Item ' + (index + 1);
    return item.title || item.name || item.key || 'Item ' + (index + 1);
  }

  async function waitForLogin(timeoutMs) {
    var max = typeof timeoutMs === 'number' ? timeoutMs : 180000;
    var start = Date.now();
    var attempts = 0;
    while (Date.now() - start < max) {
      attempts += 1;
      var logged = extension.isLoggedIn();
      post('runner:login-check', { logged: logged, attempts: attempts });
      if (logged) {
        post('runner:login', { detected: true });
        return;
      }
      await extension.wait(1000);
    }
    throw new Error('login_timeout');
  }

  async function waitForAddress(timeoutMs) {
    var max = typeof timeoutMs === 'number' ? timeoutMs : 240000;
    var start = Date.now();
    post('runner:address_required', {});
    while (Date.now() - start < max) {
      var context = extension.resolveContext();
      if (extension.hasPlaceId(context)) {
        post('runner:address_ready', {});
        return context;
      }
      await extension.wait(1000);
    }
    throw new Error('address_timeout');
  }

  (async function run() {
    try {
      await waitForLogin();
      var context = await waitForAddress();
      if (!queue.length) {
        post('runner:complete', { ok: 0, failed: 0, orderId: orderId });
        return;
      }
      post('runner:start', { orderId: orderId });
      var ok = 0;
      var failed = 0;
      for (var i = 0; i < queue.length; i += 1) {
        var item = queue[i];
        post('runner:item:start', {
          index: i,
          title: formatTitle(item, i),
        });
        context = extension.resolveContext();
        var result = await extension.addItem(item, context);
        if (result.status === 'ok') {
          ok += 1;
        } else {
          failed += 1;
        }
        post('runner:item:done', {
          index: i,
          status: result.status,
          reason: result.reason || null,
          title: formatTitle(item, i),
        });
        if (result && result.meta) {
          if (result.meta.payloadSummary) {
            post('runner:log', {
              message: 'Payload summary: ' + result.meta.payloadSummary,
            });
          }
          if (result.meta.responseSummary || result.meta.statusField) {
            var codeLabel =
              typeof result.meta.statusCode === 'number'
                ? result.meta.statusCode
                : 'n/a';
            var statusLabel = result.meta.statusField
              ? ' statusField=' + result.meta.statusField
              : '';
            var responseLabel =
              result.meta.responseSummary || 'no_response_summary';
            post('runner:log', {
              message:
                'Response (' +
                codeLabel +
                '): ' +
                responseLabel +
                statusLabel,
            });
          }
          if (result.meta.formExceptionMessage) {
            post('runner:log', {
              message:
                'Form exception: ' + result.meta.formExceptionMessage.slice(0, 160),
            });
          }
        }
        await extension.wait(400 + Math.floor(Math.random() * 300));
      }
      post('runner:complete', { ok: ok, failed: failed, orderId: orderId });
      if (CONFIG.finalUrl !== false) {
        setTimeout(function () {
          window.location.href =
            CONFIG.finalUrl || 'https://www.woolworths.co.za/check-out/cart';
        }, 1200);
      }
    } catch (error) {
      post('runner:error', {
        reason: error && error.message ? error.message : String(error),
      });
    }
  })();
})();
`;

