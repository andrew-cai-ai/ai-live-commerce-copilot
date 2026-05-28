(() => {
  const LOCAL_ENDPOINTS = [
    "http://localhost:8000/api/live-ingest",
    "http://127.0.0.1:8000/api/live-ingest"
  ];

  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("page_hook.js");
  script.onload = () => script.remove();
  (document.documentElement || document.head).appendChild(script);

  window.addEventListener("message", async (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.type !== "AI_LIVE_DIRECTOR_METRICS") return;
    const payload = event.data.payload || {};
    try {
      for (const endpoint of LOCAL_ENDPOINTS) {
        try {
          const response = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
          if (response.ok) return;
        } catch (_error) {
          // Try the next local loopback endpoint.
        }
      }
    } catch (_error) {
      // Local app may not be running yet. Do not collect or persist anything.
    }
  });
})();
