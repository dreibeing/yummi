'use strict';

const input = document.getElementById('input');
const startBtn = document.getElementById('start');
const stopBtn = document.getElementById('stop');
const loadBtn = document.getElementById('load');
const progressEl = document.getElementById('progress');

const SERVER_BASE = 'http://localhost:4010';
const RETAILER_ID = 'woolworths_co_za';

let statusLine = '';
const logLines = [];

function render() {
  const lines = [];
  if (statusLine) lines.push(statusLine);
  if (logLines.length) lines.push(...logLines);
  progressEl.textContent = lines.join('\n');
}

function setStatus(msg) {
  statusLine = msg;
  render();
}

function appendLog(msg) {
  logLines.push(msg);
  render();
}

function resetLogs() {
  logLines.length = 0;
  render();
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function startFill(payload, options = {}) {
  const normalized = {
    retailer: payload?.retailer || RETAILER_ID,
    items: Array.isArray(payload?.items) ? payload.items : []
  };
  const tab = await getActiveTab();
  chrome.runtime.sendMessage({
    type: 'START_FILL',
    payload: {
      ...normalized,
      orderId: options.orderId || null
    },
    tabId: tab?.id || null
  });
}

function isValidPayload(payload) {
  return (
    payload &&
    payload.retailer === RETAILER_ID &&
    Array.isArray(payload.items)
  );
}

if (startBtn) {
  startBtn.addEventListener('click', async () => {
    let payload;
    try {
      payload = JSON.parse(input.value || '{}');
    } catch (error) {
      appendLog('Invalid JSON.');
      return;
    }
    if (!isValidPayload(payload)) {
      appendLog('Expected { retailer: "woolworths_co_za", items: [...] }');
      return;
    }
    resetLogs();
    setStatus('Starting…');
    await startFill(payload);
  });
}

if (stopBtn) {
  stopBtn.addEventListener('click', async () => {
    chrome.runtime.sendMessage({ type: 'STOP_FILL' });
  });
}

if (loadBtn) {
  loadBtn.addEventListener('click', async () => {
    resetLogs();
    setStatus('Checking queued orders...');
    try {
      const resp = await fetch(`${SERVER_BASE}/orders/next?workerId=extension_popup`);
      if (resp.status === 204) {
        setStatus('No queued orders available.');
        appendLog('No queued orders available.');
        return;
      }
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const items = Array.isArray(data.items) ? data.items : [];
      const payload = { retailer: RETAILER_ID, items };
      input.value = JSON.stringify(payload, null, 2);
      appendLog(`Loaded order ${data.orderId} (${items.length} items). Starting fill...`);
      setStatus('Starting queued order...');
      await startFill(payload, { orderId: data.orderId });
    } catch (error) {
      appendLog(`Failed to load queued order: ${error?.message || error}`);
      setStatus('Failed to load queued order.');
    }
  });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === 'POPUP_LOG') {
    appendLog(msg.text || '');
  }
  if (msg?.type === 'POPUP_STATUS') {
    const { ok = 0, failed = 0, total = 0, elapsedMs = 0 } = msg.data || {};
    setStatus(`OK ${ok}/${total} • Failed ${failed} • ${Math.round(elapsedMs)} ms`);
  }
});
