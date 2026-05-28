const STATUS_KEY = "aiLiveDirectorStatus";
let manualInjectOverride = null;
let memoryStatus = {};

document.addEventListener("DOMContentLoaded", () => {
  setText("popup-js", "loaded", "good");
});

function setText(id, text, className = "") {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = text;
  node.className = className;
}

function yesNo(value) {
  return value ? ["yes", "good"] : ["no", "bad"];
}

function timeText(value) {
  if (!value) return "--";
  return new Date(value).toLocaleTimeString("zh-CN", { hour12: false });
}

function render(status) {
  const content = yesNo(status.contentScriptInjected);
  const hook = yesNo(status.pageHookInjected || status.pageHookScriptLoaded);
  const captured = yesNo(status.capturedTargetApi);
  const parsed = yesNo(status.lastParseSuccess);
  const sent = yesNo(status.lastSendSuccess);

  setText("content-script", content[0], content[1]);
  setText("content-heartbeat", timeText(status.contentHeartbeatAt), status.contentHeartbeatAt ? "good" : "");
  setText("page-hook", hook[0], hook[1]);
  setText("captured-api", captured[0], captured[1]);
  setText("parse-success", parsed[0], parsed[1]);
  setText("send-success", sent[0], sent[1]);
  setText("last-captured", timeText(status.lastCapturedAt));
  setText("last-sent", timeText(status.lastSentAt));
  setText("live-id", status.liveId || "--");
  setText("metric-keys", Array.isArray(status.metricKeys) && status.metricKeys.length ? status.metricKeys.join(", ") : "--");
  setText("event-count", String(status.eventCount ?? "--"));
  setText("endpoint", status.lastEndpoint || "--");
  setText("active-tab", status.activeTabHost || "--", status.activeTabMatches ? "good" : status.activeTabHost ? "warn" : "");
  const manualStatus = manualInjectOverride || {
    status: status.manualInjectStatus || "--",
    error: status.lastError || "",
    className: status.manualInjectStatus === "success" ? "good" : status.manualInjectStatus === "failed" ? "bad" : ""
  };
  setText("manual-inject", manualStatus.status, manualStatus.className || "");
  setText("error", manualStatus.error || status.lastError || "--", manualStatus.error || status.lastError ? "bad" : "");

  const hint = document.getElementById("hint");
  if (!status.contentScriptInjected) {
    hint.textContent = "还没有注入页面。请确认当前页是 liveplatform.taobao.com，并刷新淘宝后台。";
    return;
  }
  if (!status.capturedTargetApi) {
    hint.textContent = "插件已注入，但还没抓到目标 mtop 接口。请进入直播中控数据页，或刷新正在直播的数据页面。";
    return;
  }
  if (!status.lastParseSuccess) {
    hint.textContent = "已经抓到目标接口，但响应解析失败。需要看 Taobao 实际返回格式。";
    return;
  }
  if (!status.lastSendSuccess) {
    hint.textContent = "已经抓到并解析成功，但没有发到本地 app。请确认 .venv/bin/uvicorn main:app --reload --port 8000 正在运行。";
    return;
  }
  hint.textContent = "链路已通。回到本地报告页，Payload source 应显示 extension。";
}

function updateStatus(patch, callback) {
  memoryStatus = { ...memoryStatus, ...patch, updatedAt: Date.now() };
  if (!hasChromeStorage()) {
    if (callback) callback();
    return;
  }
  chrome.storage.local.get([STATUS_KEY], (result) => {
    const current = result && result[STATUS_KEY] ? result[STATUS_KEY] : {};
    const next = { ...current, ...memoryStatus };
    chrome.storage.local.set({ [STATUS_KEY]: next }, callback);
  });
}

function hasChromeStorage() {
  return typeof chrome !== "undefined" && chrome.storage && chrome.storage.local;
}

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

function inspectActiveTab() {
  inspectActiveTabDirect("");
}

function inspectActiveTabDirect(errorMessage) {
  if (!chrome.tabs || !chrome.tabs.query) {
    updateStatus({ lastError: "chrome.tabs API is unavailable. Check extension permissions." }, refresh);
    return;
  }
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0] ? tabs[0] : {};
    let host = "";
    try {
      host = new URL(tab.url || "").hostname;
    } catch (_error) {
      host = "";
    }
    updateStatus({
      activeTabId: tab.id || 0,
      activeTabUrl: tab.url || "",
      activeTabHost: host || "--",
      activeTabMatches: activeTabMatches(tab.url || ""),
      backgroundFallbackUsed: true,
      lastError: errorMessage || ""
    }, refresh);
  });
}

function injectCurrentTab() {
  manualInjectOverride = { status: "running", error: "", className: "warn" };
  setText("manual-inject", "running", "warn");
  setText("error", "--", "");
  injectCurrentTabDirect("");
}

function injectCurrentTabDirect(reason) {
  let completed = false;
  window.setTimeout(() => {
    if (!completed) {
      manualInjectOverride = {
        status: "failed",
        error: "chrome.tabs.query timed out. Reopen popup on the Taobao tab and try again.",
        className: "bad"
      };
      setText("manual-inject", "failed", "bad");
      setText("error", "chrome.tabs.query timed out. Reopen popup on the Taobao tab and try again.", "bad");
      updateStatus({
        manualInjectStatus: "failed",
        lastError: "chrome.tabs.query timed out. Reopen popup on the Taobao tab and try again."
      });
    }
  }, 1500);
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    completed = true;
    const tab = tabs && tabs[0] ? tabs[0] : null;
    if (!tab || !tab.id) {
      manualInjectOverride = { status: "failed", error: "No active tab found.", className: "bad" };
      setText("manual-inject", "failed", "bad");
      setText("error", "No active tab found.", "bad");
      updateStatus({ manualInjectStatus: "failed", lastError: "No active tab found." }, refresh);
      return;
    }
    let host = "";
    try {
      host = new URL(tab.url || "").hostname;
    } catch (_error) {
      host = "";
    }
    if (!activeTabMatches(tab.url || "")) {
      manualInjectOverride = { status: "failed", error: "Current tab is not a Taobao/Tmall page.", className: "bad" };
      setText("active-tab", host || "--", "warn");
      setText("manual-inject", "failed", "bad");
      setText("error", "Current tab is not a Taobao/Tmall page.", "bad");
      updateStatus({
        activeTabId: tab.id,
        activeTabUrl: tab.url || "",
        activeTabHost: host || "--",
        activeTabMatches: false,
        manualInjectStatus: "failed",
        lastError: "Current tab is not a Taobao/Tmall page."
      }, refresh);
      return;
    }
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"]
    }, () => {
      if (chrome.runtime.lastError) {
        manualInjectOverride = { status: "failed", error: chrome.runtime.lastError.message, className: "bad" };
        setText("active-tab", host || "--", "good");
        setText("manual-inject", "failed", "bad");
        setText("error", chrome.runtime.lastError.message, "bad");
        updateStatus({
          activeTabId: tab.id,
          activeTabUrl: tab.url || "",
          activeTabHost: host || "--",
          activeTabMatches: true,
          manualInjectStatus: "failed",
          lastError: chrome.runtime.lastError.message
        }, refresh);
        return;
      }
      setText("active-tab", host || "--", "good");
      manualInjectOverride = { status: "success", error: reason || "", className: "good" };
      setText("manual-inject", "success", "good");
      setText("error", reason || "--", reason ? "warn" : "");
      updateStatus({
        activeTabId: tab.id,
        activeTabUrl: tab.url || "",
        activeTabHost: host || "--",
        activeTabMatches: true,
        manualInjectStatus: "success",
        manualInjectedAt: Date.now(),
        popupDirectInjectUsed: true,
        lastError: reason || ""
      }, refresh);
    });
  });
}

function refresh() {
  if (!hasChromeStorage()) {
    render(memoryStatus);
    return;
  }
  chrome.storage.local.get([STATUS_KEY], (result) => {
    memoryStatus = { ...memoryStatus, ...((result && result[STATUS_KEY]) || {}) };
    render(memoryStatus);
  });
}

document.getElementById("inject-now").addEventListener("click", injectCurrentTab);

document.getElementById("clear-status").addEventListener("click", () => {
  memoryStatus = {};
  if (!hasChromeStorage()) {
    refresh();
    return;
  }
  chrome.storage.local.remove([STATUS_KEY], refresh);
});

inspectActiveTab();
refresh();
setInterval(refresh, 1000);
