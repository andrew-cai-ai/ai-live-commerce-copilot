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

function refresh() {
  chrome.storage.local.get([STATUS_KEY], (result) => {
    render(result && result[STATUS_KEY] ? result[STATUS_KEY] : {});
  });
}

document.getElementById("clear-status").addEventListener("click", () => {
  chrome.storage.local.remove([STATUS_KEY], refresh);
});

refresh();
setInterval(refresh, 1000);
