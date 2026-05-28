const STATUS_KEY = "aiLiveDirectorStatus";

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
  setText("manual-inject", status.manualInjectStatus || "--", status.manualInjectStatus === "success" ? "good" : status.manualInjectStatus === "failed" ? "bad" : "");
  setText("error", status.lastError || "--", status.lastError ? "bad" : "");

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
  chrome.storage.local.get([STATUS_KEY], (result) => {
    const current = result && result[STATUS_KEY] ? result[STATUS_KEY] : {};
    chrome.storage.local.set({ [STATUS_KEY]: { ...current, ...patch, updatedAt: Date.now() } }, callback);
  });
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
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0] ? tabs[0] : {};
    let host = "";
    try {
      host = new URL(tab.url || "").hostname;
    } catch (_error) {
      host = "";
    }
    updateStatus({
      activeTabUrl: tab.url || "",
      activeTabHost: host || "--",
      activeTabMatches: activeTabMatches(tab.url || "")
    }, refresh);
  });
}

function injectCurrentTab() {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0] ? tabs[0] : null;
    if (!tab || !tab.id) {
      updateStatus({ manualInjectStatus: "failed", lastError: "No active tab found." }, refresh);
      return;
    }
    if (!activeTabMatches(tab.url || "")) {
      updateStatus({
        manualInjectStatus: "failed",
        activeTabUrl: tab.url || "",
        lastError: "Current tab is not a Taobao live backend page."
      }, refresh);
      return;
    }
    chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      files: ["content.js"]
    }, () => {
      if (chrome.runtime.lastError) {
        updateStatus({
          manualInjectStatus: "failed",
          lastError: chrome.runtime.lastError.message
        }, refresh);
        return;
      }
      updateStatus({
        manualInjectStatus: "success",
        manualInjectedAt: Date.now(),
        lastError: ""
      }, refresh);
    });
  });
}

function refresh() {
  chrome.storage.local.get([STATUS_KEY], (result) => {
    render(result && result[STATUS_KEY] ? result[STATUS_KEY] : {});
  });
}

document.getElementById("inject-now").addEventListener("click", injectCurrentTab);

document.getElementById("clear-status").addEventListener("click", () => {
  chrome.storage.local.remove([STATUS_KEY], refresh);
});

inspectActiveTab();
refresh();
setInterval(refresh, 1000);
