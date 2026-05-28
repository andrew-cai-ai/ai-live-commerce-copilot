(() => {
  if (window.__aiLiveDirectorContentInjected) {
    return;
  }
  window.__aiLiveDirectorContentInjected = true;

  const LOCAL_ENDPOINTS = [
    "http://localhost:8000/api/live-ingest",
    "http://127.0.0.1:8000/api/live-ingest"
  ];
  const STATUS_KEY = "aiLiveDirectorStatus";

  function now() {
    return Date.now();
  }

  function updateStatus(patch) {
    const nextPatch = {
      ...patch,
      updatedAt: now(),
      pageUrl: location.href
    };
    if (!chrome.storage || !chrome.storage.local) {
      return;
    }
    chrome.storage.local.get([STATUS_KEY], (result) => {
      const current = result && result[STATUS_KEY] ? result[STATUS_KEY] : {};
      chrome.storage.local.set({
        [STATUS_KEY]: {
          ...current,
          ...nextPatch
        }
      });
    });
  }

  updateStatus({
    contentScriptInjected: true,
    contentScriptInjectedAt: now(),
    pageHookInjected: false,
    capturedTargetApi: false,
    lastParseSuccess: false,
    lastSendSuccess: false,
    lastError: ""
  });

  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("page_hook.js");
  script.onload = () => {
    updateStatus({ pageHookScriptLoaded: true, pageHookScriptLoadedAt: now() });
    script.remove();
  };
  script.onerror = () => {
    updateStatus({ pageHookScriptLoaded: false, lastError: "Failed to inject page_hook.js" });
  };
  (document.documentElement || document.head).appendChild(script);

  window.addEventListener("message", async (event) => {
    if (event.source !== window) return;
    if (!event.data || !event.data.type) return;

    if (event.data.type === "AI_LIVE_DIRECTOR_STATUS") {
      updateStatus(event.data.payload || {});
      return;
    }

    if (event.data.type !== "AI_LIVE_DIRECTOR_METRICS") return;
    const payload = event.data.payload || {};
    updateStatus({
      lastPayloadReadyAt: now(),
      liveId: payload.liveId || "",
      metricKeys: Object.keys(payload.metrics || {}),
      eventCount: Array.isArray(payload.events) ? payload.events.length : 0
    });

    let lastError = "";
    for (const endpoint of LOCAL_ENDPOINTS) {
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (response.ok) {
          updateStatus({
            lastSendSuccess: true,
            lastSentAt: now(),
            lastEndpoint: endpoint,
            lastHttpStatus: response.status,
            lastError: ""
          });
          return;
        }
        lastError = `POST ${endpoint} returned HTTP ${response.status}`;
      } catch (error) {
        lastError = `POST ${endpoint} failed: ${error.message}`;
      }
    }

    updateStatus({
      lastSendSuccess: false,
      lastHttpStatus: 0,
      lastError: lastError || "Local app is not reachable."
    });
  });
})();
