'use strict';

const input = document.getElementById('input');
const startBtn = document.getElementById('start');
const stopBtn = document.getElementById('stop');
const progressEl = document.getElementById('progress');

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
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  return tab;
}

startBtn.addEventListener('click', async () => {
  let payload;
  try {
    payload = JSON.parse(input.value || '{}');
  } catch (e) {
    log('Invalid JSON.');
    return;
  }
  if (!payload || payload.retailer !== 'woolworths_co_za' || !Array.isArray(payload.items)) {
    log('Expected { retailer: "woolworths_co_za", items: [...] }');
    return;
  }
  resetLogs();
  setStatus('Starting…');
  const tab = await getActiveTab();
  chrome.runtime.sendMessage({ type: 'START_FILL', payload, tabId: tab?.id || null });
});

stopBtn.addEventListener('click', async () => {
  chrome.runtime.sendMessage({ type: 'STOP_FILL' });
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === 'POPUP_LOG') {
    appendLog(msg.text || '');
  }
  if (msg?.type === 'POPUP_STATUS') {
    const { ok = 0, failed = 0, total = 0, elapsedMs = 0 } = msg.data || {};
    setStatus(`OK ${ok}/${total} • Failed ${failed} • ${Math.round(elapsedMs)} ms`);
  }
});
