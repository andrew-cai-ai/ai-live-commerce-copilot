(() => {
  const TARGET_API = "mtop.taobao.tblive.portal.live.user.assistant.data.get";
  const SEND_INTERVAL_MS = 5000;
  let latestMetrics = null;
  let lastSentAt = 0;
  const metricsByLiveId = new Map();

  function toNumber(value) {
    if (value === null || value === undefined || value === "") return 0;
    if (typeof value === "number") return Number.isFinite(value) ? value : 0;
    const text = String(value).replace(/,/g, "").replace(/%/g, "").replace(/[¥￥元]/g, "").trim();
    const parsed = Number(text);
    if (!Number.isFinite(parsed)) return 0;
    return String(value).includes("%") ? parsed / 100 : parsed;
  }

  function normalizeRate(value) {
    const numeric = toNumber(value);
    return Math.max(0, Math.min(1, numeric > 1 ? numeric / 100 : numeric));
  }

  function findValue(node, key) {
    if (!node || typeof node !== "object") return undefined;
    if (Object.prototype.hasOwnProperty.call(node, key)) return node[key];
    for (const value of Object.values(node)) {
      const found = findValue(value, key);
      if (found !== undefined) return found;
    }
    return undefined;
  }

  function unwrapPayload(payload) {
    if (!payload || typeof payload !== "object") return {};
    if (payload.data && typeof payload.data === "object") {
      if (payload.data.data && typeof payload.data.data === "object") return payload.data.data;
      if (payload.data.result && typeof payload.data.result === "object") return payload.data.result;
      return payload.data;
    }
    return payload;
  }

  function liveIdFromPayload(payload, data) {
    return String(
      findValue(payload, "liveId")
      || findValue(payload, "live_id")
      || findValue(data, "liveId")
      || findValue(data, "live_id")
      || "default_live"
    );
  }

  function parseJsonMaybe(text) {
    try {
      return JSON.parse(text);
    } catch (_error) {
      const match = String(text || "").match(/\{[\s\S]*\}/);
      if (!match) return null;
      try {
        return JSON.parse(match[0]);
      } catch (_innerError) {
        return null;
      }
    }
  }

  function pick(source, ...keys) {
    if (!source || typeof source !== "object") return undefined;
    for (const key of keys) {
      if (source[key] !== undefined) return source[key];
    }
    const lowered = Object.fromEntries(Object.entries(source).map(([key, value]) => [String(key).toLowerCase(), value]));
    for (const key of keys) {
      const value = lowered[String(key).toLowerCase()];
      if (value !== undefined) return value;
    }
    return undefined;
  }

  function parseProductEvents(raw) {
    const list = Array.isArray(raw)
      ? raw
      : raw && typeof raw === "object"
      ? raw.list || raw.items || raw.data || raw.result || raw.products || []
      : [];
    if (!Array.isArray(list)) return [];
    return list.filter((item) => item && typeof item === "object").slice(0, 20).map((item) => ({
      imageUrl: String(pick(item, "imageUrl", "image_url", "picUrl") || ""),
      price: toNumber(pick(item, "price")),
      payBuyerCnt: Math.round(toNumber(pick(item, "payBuyerCnt", "pay_buyer_cnt"))),
      startTime: String(pick(item, "startTime", "start_time") || ""),
      startTimeFormat: String(pick(item, "startTimeFormat", "start_time_format") || ""),
      title: String(pick(item, "title", "itemTitle", "productTitle") || ""),
      status: String(pick(item, "status") || "")
    }));
  }

  function normalizeMtopPayload(rawPayload) {
    const data = unwrapPayload(rawPayload);
    const foundTotalStats = findValue(data, "totalStats");
    const totalStats = foundTotalStats || {};
    const foundDataRegion = findValue(data, "dataRegion");
    const dataRegion = foundDataRegion || {};
    const liveId = liveIdFromPayload(rawPayload, data);
    return {
      source: "chrome_extension",
      captured_api: TARGET_API,
      captured_at: Date.now(),
      liveId,
      online_uv: foundTotalStats ? toNumber(pick(totalStats, "online_uv", "onlineUv")) : undefined,
      pv: foundTotalStats ? toNumber(pick(totalStats, "pv")) : undefined,
      uv: foundTotalStats ? toNumber(pick(totalStats, "uv")) : undefined,
      stay_time_pu: foundTotalStats ? toNumber(pick(totalStats, "stay_time_pu", "stayTimePu")) : undefined,
      pay_byr_rate: foundTotalStats ? normalizeRate(pick(totalStats, "pay_byr_rate", "payByrRate")) : undefined,
      pay_buyer_cnt: foundTotalStats ? toNumber(pick(totalStats, "pay_buyer_cnt", "payBuyerCnt")) : undefined,
      pay_item_qty: foundTotalStats ? toNumber(pick(totalStats, "pay_item_qty", "payItemQty")) : undefined,
      pay_amt: foundTotalStats ? toNumber(pick(totalStats, "pay_amt", "payAmt")) : undefined,
      heat_score: foundTotalStats ? toNumber(pick(totalStats, "heat_score", "heatScore")) : undefined,
      ipv_uv_rate: foundTotalStats ? normalizeRate(pick(totalStats, "ipv_uv_rate", "ipvUvRate")) : undefined,
      comment_uv: foundTotalStats ? toNumber(pick(totalStats, "comment_uv", "commentUv")) : undefined,
      refund_amt: foundTotalStats ? toNumber(pick(totalStats, "refund_amt", "refundAmt")) : undefined,
      atn_uv: foundTotalStats ? toNumber(pick(totalStats, "atn_uv", "atnUv")) : undefined,
      dataRegion: foundDataRegion ? {
        look_uv_td_d_live: toNumber(pick(dataRegion, "look_uv_td_d_live")),
        look_time_td_avg_d_live: toNumber(pick(dataRegion, "look_time_td_avg_d_live")),
        pay_amt_td_d_live: toNumber(pick(dataRegion, "pay_amt_td_d_live")),
        look_uv_5min_d_live: toNumber(pick(dataRegion, "look_uv_5min_d_live")),
        look_time_5min_avg_d_live: toNumber(pick(dataRegion, "look_time_5min_avg_d_live")),
        pay_amt_5min_d_live: toNumber(pick(dataRegion, "pay_amt_5min_d_live")),
        pay_amt_5min_d_shop: toNumber(pick(dataRegion, "pay_amt_5min_d_shop"))
      } : {},
      interactSecKill: parseProductEvents(findValue(data, "interactSecKill"))
    };
  }

  function captureResponse(url, responseText) {
    if (!String(url).includes(TARGET_API)) return;
    const payload = parseJsonMaybe(responseText);
    if (!payload) return;
    const current = normalizeMtopPayload(payload);
    const liveId = current.liveId || "default_live";
    const previous = metricsByLiveId.get(liveId) || {};
    const merged = mergeMetrics(previous, current);
    metricsByLiveId.set(liveId, merged);
    latestMetrics = merged;
  }

  function mergeMetrics(previous, current) {
    const merged = { ...previous };
    Object.entries(current).forEach(([key, value]) => {
      if (value !== undefined) merged[key] = value;
    });
    merged.dataRegion = { ...(previous.dataRegion || {}), ...(current.dataRegion || {}) };
    return merged;
  }

  const originalFetch = window.fetch;
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url || "";
      if (String(url).includes(TARGET_API)) {
        response.clone().text().then((text) => captureResponse(url, text)).catch(() => {});
      }
    } catch (_error) {}
    return response;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
    this.__liveDirectorUrl = url;
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function patchedSend(...args) {
    this.addEventListener("load", () => {
      try {
        captureResponse(this.__liveDirectorUrl || "", this.responseText || "");
      } catch (_error) {}
    });
    return originalSend.apply(this, args);
  };

  window.setInterval(() => {
    if (!latestMetrics || Date.now() - lastSentAt < SEND_INTERVAL_MS - 250) return;
    lastSentAt = Date.now();
    window.postMessage({ type: "AI_LIVE_DIRECTOR_METRICS", payload: latestMetrics }, "*");
  }, 1000);
})();
