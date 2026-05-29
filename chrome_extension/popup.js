let manualInjectOverride = null;
let memoryStatus = {};
const CONFIG_KEY = "aiLiveDirectorConfig";

document.addEventListener("DOMContentLoaded", () => {
  setText("popup-js", "loaded", "good");
  loadWorkspaceConfig();
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
  setText("host-id", status.hostId || status.liveId || "--");
  setText("workspace-label", status.workspaceId || "--", status.workspaceId ? "good" : "");
  setText("live-id", status.liveId || "--");
  setText("extension-version", status.extensionVersion || "--");
  const sections = status.payloadSections || {};
  const totalStats = yesNo(sections.totalStats);
  const dataRegion = yesNo(sections.dataRegion);
  const interactSecKill = yesNo(sections.interactSecKill);
  setText("section-totalStats", totalStats[0], totalStats[1]);
  setText("section-dataRegion", dataRegion[0], dataRegion[1]);
  setText("section-interactSecKill", interactSecKill[0], interactSecKill[1]);
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
  renderSteps(status);

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

function loadWorkspaceConfig() {
  if (!chrome.storage || !chrome.storage.local) {
    setText("workspace-label", "--", "warn");
    return;
  }
  chrome.storage.local.get([CONFIG_KEY], (result) => {
    const config = (result && result[CONFIG_KEY]) || {};
    const workspaceId = String(config.workspace_id || "").trim();
    const ingestToken = String(config.ingest_token || "").trim();
    const input = document.getElementById("workspace-id");
    if (input) input.value = workspaceId;
    const apiBaseInput = document.getElementById("api-base-url");
    if (apiBaseInput) apiBaseInput.value = String(config.api_base_url || "").trim();
    const tokenInput = document.getElementById("ingest-token");
    if (tokenInput) tokenInput.value = ingestToken;
    setText("workspace-label", workspaceId || "default", workspaceId ? "good" : "");
    updateStatus({ workspaceId });
  });
}

function saveWorkspaceConfig() {
  const input = document.getElementById("workspace-id");
  const workspaceId = String((input && input.value) || "").trim();
  if (!chrome.storage || !chrome.storage.local) {
    updateStatus({ lastError: "chrome.storage is unavailable. Reload extension and check permissions." }, refresh);
    return;
  }
  chrome.storage.local.get([CONFIG_KEY], (result) => {
    const current = (result && result[CONFIG_KEY]) || {};
    chrome.storage.local.set({ [CONFIG_KEY]: { ...current, workspace_id: workspaceId, binding_code: workspaceId } }, () => {
      setText("workspace-label", workspaceId || "default", workspaceId ? "good" : "");
      updateStatus({ workspaceId, lastError: "" }, refresh);
    });
  });
}

function saveApiBaseUrl() {
  const input = document.getElementById("api-base-url");
  const apiBaseUrl = String((input && input.value) || "").trim();
  if (!chrome.storage || !chrome.storage.local) {
    updateStatus({ lastError: "chrome.storage is unavailable. Reload extension and check permissions." }, refresh);
    return;
  }
  chrome.storage.local.get([CONFIG_KEY], (result) => {
    const current = (result && result[CONFIG_KEY]) || {};
    chrome.storage.local.set({ [CONFIG_KEY]: { ...current, api_base_url: apiBaseUrl } }, () => {
      updateStatus({ apiBaseUrl: apiBaseUrl || "", lastError: "" }, refresh);
    });
  });
}

function saveIngestToken() {
  const input = document.getElementById("ingest-token");
  const ingestToken = String((input && input.value) || "").trim();
  if (!chrome.storage || !chrome.storage.local) {
    updateStatus({ lastError: "chrome.storage is unavailable. Reload extension and check permissions." }, refresh);
    return;
  }
  chrome.storage.local.get([CONFIG_KEY], (result) => {
    const current = (result && result[CONFIG_KEY]) || {};
    chrome.storage.local.set({ [CONFIG_KEY]: { ...current, ingest_token: ingestToken } }, () => {
      updateStatus({ ingestTokenConfigured: Boolean(ingestToken), lastError: "" }, refresh);
    });
  });
}

function setStep(id, state, text) {
  const node = document.getElementById(id);
  if (!node) return;
  node.className = "step " + (state || "");
  const textNode = document.getElementById(id + "-text");
  if (textNode && text) textNode.textContent = text;
}

function renderSteps(status) {
  setStep(
    "step-page",
    status.activeTabMatches ? "done" : "active",
    status.activeTabMatches ? "已在淘宝/天猫直播相关页面" : "请切到 liveplatform.taobao.com 页面"
  );
  setStep(
    "step-inject",
    status.contentScriptInjected && (status.pageHookInjected || status.pageHookScriptLoaded) ? "done" : status.activeTabMatches ? "active" : "",
    status.contentScriptInjected ? "采集脚本已注入" : "点击“手动注入当前页面”"
  );
  setStep(
    "step-capture",
    status.capturedTargetApi ? "done" : status.contentScriptInjected ? "active" : "",
    status.capturedTargetApi ? "已捕获 Taobao mtop 实时接口" : "进入实时直播中控页，等待接口刷新"
  );
  setStep(
    "step-send",
    status.lastSendSuccess ? "done" : status.lastParseSuccess ? "active" : "",
    status.lastSendSuccess ? "已发送到云端/本地系统" : status.lastParseSuccess ? "已解析，正在发送" : "等待解析成功"
  );
}

function updateStatus(patch, callback) {
  memoryStatus = { ...memoryStatus, ...patch, updatedAt: Date.now() };
  if (!chrome.runtime || !chrome.runtime.sendMessage) {
    if (callback) callback();
    return;
  }
  chrome.runtime.sendMessage({
    type: "AI_LIVE_DIRECTOR_STATUS_PATCH",
    patch: memoryStatus
  }, () => {
    if (callback) callback();
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
      target: { tabId: tab.id, allFrames: true },
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
  if (!chrome.runtime || !chrome.runtime.sendMessage) {
    render(memoryStatus);
    return;
  }
  chrome.runtime.sendMessage({ type: "AI_LIVE_DIRECTOR_GET_STATUS" }, (response) => {
    if (chrome.runtime.lastError || !response || !response.state) {
      render(memoryStatus);
      return;
    }
    memoryStatus = { ...memoryStatus, ...response.state };
    render(memoryStatus);
  });
}

document.getElementById("inject-now").addEventListener("click", injectCurrentTab);
document.getElementById("save-workspace").addEventListener("click", saveWorkspaceConfig);
document.getElementById("save-api-base").addEventListener("click", saveApiBaseUrl);
document.getElementById("save-token").addEventListener("click", saveIngestToken);

document.getElementById("clear-status").addEventListener("click", () => {
  memoryStatus = {};
  updateStatus({}, refresh);
});

inspectActiveTab();
refresh();
setInterval(refresh, 1000);
