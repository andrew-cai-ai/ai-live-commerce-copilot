(() => {
  const LOCAL_ENDPOINT = "http://localhost:8000/live-metrics";

  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("page_hook.js");
  script.onload = () => script.remove();
  (document.documentElement || document.head).appendChild(script);

  window.addEventListener("message", async (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.type !== "AI_LIVE_DIRECTOR_METRICS") return;
    try {
      await fetch(LOCAL_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(event.data.payload || {})
      });
    } catch (_error) {
      // Local app may not be running yet. Do not collect or persist anything.
    }
  });
})();
