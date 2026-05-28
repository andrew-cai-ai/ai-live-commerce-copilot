const STATUS_KEY = "aiLiveDirectorStatus";

function activeTabMatches(url) {
  try {
    const parsed = new URL(url || "");
    return parsed.hostname === "liveplatform.taobao.com"
      || parsed.hostname.endsWith(".taobao.com")
      || parsed.hostname.endsWith(".tmall.com");
  } catch (_error) {
    return false;
  }
}

function statusPatch(patch, callback) {
  if (typeof chrome === "undefined" || !chrome.storage || !chrome.storage.local) {
    if (callback) callback();
    return;
  }
  chrome.storage.local.get([STATUS_KEY], (result) => {
    const current = result && result[STATUS_KEY] ? result[STATUS_KEY] : {};
    chrome.storage.local.set({
      [STATUS_KEY]: {
        ...current,
        ...patch,
        updatedAt: Date.now()
      }
    }, callback);
  });
}

function inspectActiveTab(callback) {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0] ? tabs[0] : null;
    let host = "";
    try {
      host = new URL((tab && tab.url) || "").hostname;
    } catch (_error) {
      host = "";
    }
    const patch = {
      activeTabId: tab && tab.id ? tab.id : 0,
      activeTabUrl: (tab && tab.url) || "",
      activeTabHost: host || "--",
      activeTabMatches: activeTabMatches((tab && tab.url) || "")
    };
    statusPatch(patch, () => callback && callback(tab, patch));
  });
}

function injectActiveTab(sendResponse) {
  inspectActiveTab((tab, tabPatch) => {
    if (!tab || !tab.id) {
      const patch = { manualInjectStatus: "failed", lastError: "No active tab found." };
      statusPatch(patch, () => sendResponse({ ok: false, ...tabPatch, ...patch }));
      return;
    }
    if (!tabPatch.activeTabMatches) {
      const patch = {
        manualInjectStatus: "failed",
        lastError: "Current tab is not a Taobao/Tmall page."
      };
      statusPatch(patch, () => sendResponse({ ok: false, ...tabPatch, ...patch }));
      return;
    }
    chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      files: ["content.js"]
    }, () => {
      if (chrome.runtime.lastError) {
        const patch = {
          manualInjectStatus: "failed",
          lastError: chrome.runtime.lastError.message
        };
        statusPatch(patch, () => sendResponse({ ok: false, ...tabPatch, ...patch }));
        return;
      }
      const patch = {
        manualInjectStatus: "success",
        manualInjectedAt: Date.now(),
        lastError: ""
      };
      statusPatch(patch, () => sendResponse({ ok: true, ...tabPatch, ...patch }));
    });
  });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !message.type) {
    return false;
  }
  if (message.type === "AI_LIVE_DIRECTOR_INSPECT_TAB") {
    inspectActiveTab((_tab, patch) => sendResponse({ ok: true, ...patch }));
    return true;
  }
  if (message.type === "AI_LIVE_DIRECTOR_INJECT_ACTIVE_TAB") {
    injectActiveTab(sendResponse);
    return true;
  }
  return false;
});

chrome.runtime.onInstalled.addListener(() => {
  statusPatch({
    extensionInstalledAt: Date.now(),
    manualInjectStatus: "",
    lastError: ""
  });
});
