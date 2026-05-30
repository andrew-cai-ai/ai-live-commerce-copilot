(() => {
  const TARGET_API = "mtop.taobao.tblive.portal.live.user.assistant.data.get";
  const SEND_INTERVAL_MS = 5000;
  const DEFAULT_LIVE_ID = "default_live";
  const OBSERVED_API_LIMIT = 8;
  const metricsByLiveId = new Map();
  const observedApis = [];
  let latestPayload = null;
  let lastSentAt = 0;

  const VALUE_TYPE_MAP = {
    uv: "uv",
    pv: "pv",
    online_uv: "online_uv",
    heat_score: "heat_score",
    pay_amt: "pay_amt",
    pay_byr_rate: "pay_byr_rate",
    ipv_uv_rate: "ipv_uv_rate",
    stay_time_pu: "stay_time_pu",
    comment_uv: "comment_uv",
    pay_item_qty: "pay_item_qty",
    pay_buyer_cnt: "pay_buyer_cnt",
    look_uv_td_d_live: "look_uv_td_d_live",
    look_uv_5min_d_live: "look_uv_5min_d_live",
    pay_amt_td_d_live: "pay_amt_td_d_live",
    pay_amt_5min_d_live: "pay_amt_5min_d_live",
    look_time_td_avg_d_live: "look_time_td_avg_d_live",
    look_time_5min_avg_d_live: "look_time_5min_avg_d_live",
    pay_amt_td_d_shop: "pay_amt_td_d_shop",
    pay_amt_5min_d_shop: "pay_amt_5min_d_shop"
  };

  const DATA_REGION_FIELDS = new Set([
    "look_uv_td_d_live",
    "look_uv_5min_d_live",
    "pay_amt_td_d_live",
    "pay_amt_5min_d_live",
    "look_time_td_avg_d_live",
    "look_time_5min_avg_d_live",
    "pay_amt_td_d_shop",
    "pay_amt_5min_d_shop"
  ]);

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

  function normalizeMetricValue(field, value) {
    if (field === "pay_byr_rate" || field === "ipv_uv_rate") {
      return normalizeRate(value);
    }
    return toNumber(value);
  }

  function safeDecode(value) {
    try {
      return decodeURIComponent(String(value));
    } catch (_error) {
      return String(value || "");
    }
  }

  function normalizeTaobaoResponseText(input) {
    const text = String(input || "").trim();
    if (!text) return "";
    if (text.startsWith("{") || text.startsWith("[")) return text;
    const jsonp = text.match(/^[\w$]+\(([\s\S]*)\)\s*;?$/);
    if (jsonp) return jsonp[1];
    const objectMatch = text.match(/\{[\s\S]*\}/);
    return objectMatch ? objectMatch[0] : "";
  }

  function parseJsonMaybe(text) {
    const normalized = normalizeTaobaoResponseText(text);
    if (!normalized) return null;
    try {
      return JSON.parse(normalized);
    } catch (_error) {
      return null;
    }
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

  function findDict(node, key) {
    const value = findValue(node, key);
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
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

  function collectDataLists(node, lists = []) {
    if (!node || typeof node !== "object") return lists;
    if (Array.isArray(node)) {
      node.forEach((item) => collectDataLists(item, lists));
      return lists;
    }
    if (Array.isArray(node.dataList)) {
      lists.push(node.dataList);
    }
    Object.values(node).forEach((value) => collectDataLists(value, lists));
    return lists;
  }

  function parseEncodedMetricRow(row) {
    if (!row || typeof row !== "object") return null;
    const rawValue = row.value || row.dataValue || row.metricValue || "";
    if (!rawValue) return null;
    const parts = safeDecode(rawValue).split(",");
    const valueType = String(row.valueType || row.value_type || parts[1] || "").trim();
    const field = VALUE_TYPE_MAP[valueType];
    if (!field) return null;
    const numericSource = parts[3] !== undefined ? parts[3] : parts[2];
    return { field, value: normalizeMetricValue(field, numericSource) };
  }

  function extractEncodedMetrics(payload) {
    const metrics = {};
    for (const dataList of collectDataLists(payload)) {
      for (const section of dataList) {
        const rows = section && Array.isArray(section.data) ? section.data : [];
        for (const row of rows) {
          const parsed = parseEncodedMetricRow(row);
          if (parsed && metrics[parsed.field] === undefined) {
            metrics[parsed.field] = parsed.value;
          }
        }
      }
    }
    return metrics;
  }

  function parseProductEvents(raw) {
    const list = Array.isArray(raw)
      ? raw
      : raw && typeof raw === "object"
      ? raw.list || raw.items || raw.data || raw.result || raw.products || []
      : [];
    if (!Array.isArray(list)) return [];
    return list.filter((item) => item && typeof item === "object").slice(0, 20).map((item) => ({
      title: String(pick(item, "title", "itemTitle", "productTitle") || ""),
      price: toNumber(pick(item, "price")),
      payBuyerCnt: Math.round(toNumber(pick(item, "payBuyerCnt", "pay_buyer_cnt"))),
      startTime: String(pick(item, "startTime", "start_time") || ""),
      startTimeFormat: String(pick(item, "startTimeFormat", "start_time_format") || ""),
      status: String(pick(item, "status") || ""),
      imageUrl: String(pick(item, "imageUrl", "image_url", "picUrl") || "")
    }));
  }

  function liveIdFromPayload(rawPayload, data) {
    return String(
      findValue(rawPayload, "liveId")
      || findValue(rawPayload, "live_id")
      || findValue(data, "liveId")
      || findValue(data, "live_id")
      || liveIdFromUrl(location.href)
      || DEFAULT_LIVE_ID
    );
  }

  function liveIdFromUrl(url) {
    try {
      const parsed = new URL(String(url || ""), location.href);
      return parsed.searchParams.get("liveId")
        || parsed.searchParams.get("live_id")
        || parsed.searchParams.get("roomId")
        || parsed.searchParams.get("room_id")
        || "";
    } catch (_error) {
      const match = String(url || "").match(/[?&](?:liveId|live_id|roomId|room_id)=([^&#]+)/);
      return match ? safeDecode(match[1]) : "";
    }
  }

  function hostIdFromLiveId(liveId) {
    const value = String(liveId || DEFAULT_LIVE_ID).trim();
    return value || DEFAULT_LIVE_ID;
  }

  function assignKnownMetric(target, source, field, normalizer = toNumber) {
    const value = pick(source, field);
    if (value !== undefined) {
      target[field] = normalizer(value);
    }
  }

  function normalizeMtopPayload(rawPayload) {
    const data = unwrapPayload(rawPayload);
    const totalStats = findDict(data, "totalStats");
    const dataRegion = findDict(data, "dataRegion");
    const rawProductEvents = findValue(data, "interactSecKill");
    const encodedMetrics = extractEncodedMetrics(data);
    const liveId = liveIdFromPayload(rawPayload, data);
    const metrics = {};

    if (totalStats) {
      assignKnownMetric(metrics, totalStats, "uv");
      assignKnownMetric(metrics, totalStats, "pv");
      assignKnownMetric(metrics, totalStats, "online_uv");
      assignKnownMetric(metrics, totalStats, "heat_score");
      assignKnownMetric(metrics, totalStats, "pay_amt");
      assignKnownMetric(metrics, totalStats, "pay_byr_rate", normalizeRate);
      assignKnownMetric(metrics, totalStats, "ipv_uv_rate", normalizeRate);
      assignKnownMetric(metrics, totalStats, "stay_time_pu");
      assignKnownMetric(metrics, totalStats, "comment_uv");
      assignKnownMetric(metrics, totalStats, "pay_item_qty");
      assignKnownMetric(metrics, totalStats, "pay_buyer_cnt");
    }

    if (dataRegion) {
      metrics.dataRegion = {};
      DATA_REGION_FIELDS.forEach((field) => assignKnownMetric(metrics.dataRegion, dataRegion, field));
    }

    Object.entries(encodedMetrics).forEach(([field, value]) => {
      if (DATA_REGION_FIELDS.has(field)) {
        metrics.dataRegion = metrics.dataRegion || {};
        if (metrics.dataRegion[field] === undefined) metrics.dataRegion[field] = value;
      } else if (metrics[field] === undefined) {
        metrics[field] = value;
      }
    });

    return {
      source: "chrome_extension",
      extension_version: "page_hook",
      host_id: hostIdFromLiveId(liveId),
      room_id: liveId,
      liveId,
      timestamp: new Date().toISOString(),
      captured_api: TARGET_API,
      payload_sections: {
        totalStats: Boolean(totalStats) || Object.keys(encodedMetrics).some((field) => !DATA_REGION_FIELDS.has(field)),
        dataRegion: Boolean(dataRegion) || Object.keys(encodedMetrics).some((field) => DATA_REGION_FIELDS.has(field)),
        interactSecKill: Boolean(rawProductEvents)
      },
      metrics,
      events: parseProductEvents(rawProductEvents)
    };
  }

  function mergePayload(previous, current) {
    const previousMetrics = previous.metrics || {};
    const currentMetrics = current.metrics || {};
    return {
      source: "chrome_extension",
      extension_version: current.extension_version || previous.extension_version || "page_hook",
      host_id: current.host_id || previous.host_id || hostIdFromLiveId(current.liveId || previous.liveId),
      room_id: current.room_id || previous.room_id || current.liveId || previous.liveId || DEFAULT_LIVE_ID,
      liveId: current.liveId || previous.liveId || DEFAULT_LIVE_ID,
      timestamp: current.timestamp || new Date().toISOString(),
      captured_api: TARGET_API,
      payload_sections: {
        ...(previous.payload_sections || {}),
        ...(current.payload_sections || {})
      },
      metrics: {
        ...previousMetrics,
        ...currentMetrics,
        dataRegion: {
          ...(previousMetrics.dataRegion || {}),
          ...(currentMetrics.dataRegion || {})
        }
      },
      events: current.events && current.events.length ? current.events : previous.events || []
    };
  }

  function observedApiName(url) {
    const text = String(url || "");
    if (!/mtop|tblive|liveplatform|\/live\//i.test(text)) return "";
    try {
      const parsed = new URL(text, location.href);
      const api = parsed.searchParams.get("api");
      if (api) return api;
      const pathMatch = parsed.pathname.match(/(mtop\.[^/?]+)/i);
      if (pathMatch) return pathMatch[1];
      return parsed.hostname + parsed.pathname;
    } catch (_error) {
      const apiMatch = text.match(/[?&]api=([^&#]+)/i);
      if (apiMatch) return safeDecode(apiMatch[1]);
      const mtopMatch = text.match(/(mtop\.taobao\.[A-Za-z0-9_.-]+)/i);
      return mtopMatch ? mtopMatch[1] : text.slice(0, 120);
    }
  }

  function rememberObservedApi(url) {
    const api = observedApiName(url);
    if (!api) return;
    const existing = observedApis.indexOf(api);
    if (existing >= 0) observedApis.splice(existing, 1);
    observedApis.unshift(api);
    observedApis.splice(OBSERVED_API_LIMIT);
    window.postMessage({
      type: "AI_LIVE_DIRECTOR_STATUS",
      payload: {
        observedApis: observedApis.slice(),
        lastObservedApi: api,
        lastObservedUrl: String(url || "").slice(0, 220),
        liveId: liveIdFromUrl(location.href) || ""
      }
    }, "*");
  }

  function captureResponse(url, responseText) {
    rememberObservedApi(url);
    if (!String(url || "").includes(TARGET_API)) return;
    window.postMessage({
      type: "AI_LIVE_DIRECTOR_STATUS",
      payload: {
        capturedTargetApi: true,
        lastCapturedAt: Date.now(),
        lastCapturedUrl: String(url || "").slice(0, 180)
      }
    }, "*");
    const payload = parseJsonMaybe(responseText);
    if (!payload) {
      window.postMessage({
        type: "AI_LIVE_DIRECTOR_STATUS",
        payload: {
          lastParseSuccess: false,
          lastError: "Target API captured, but response JSON/JSONP parse failed."
        }
      }, "*");
      return;
    }
    const current = normalizeMtopPayload(payload);
    const liveId = current.liveId || DEFAULT_LIVE_ID;
    const merged = mergePayload(metricsByLiveId.get(liveId) || {}, current);
    metricsByLiveId.set(liveId, merged);
    latestPayload = merged;
    window.postMessage({
      type: "AI_LIVE_DIRECTOR_STATUS",
      payload: {
        lastParseSuccess: true,
        lastError: "",
      liveId,
        metricKeys: Object.keys(merged.metrics || {}),
        payloadSections: merged.payload_sections || {},
        eventCount: (merged.events || []).length
      }
    }, "*");
  }

  const originalFetch = window.fetch;
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url || "";
      rememberObservedApi(url);
      if (String(url).includes(TARGET_API)) {
        response.clone().text().then((text) => captureResponse(url, text)).catch(() => {});
      }
    } catch (_error) {}
    return response;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
    this.__aiLiveDirectorUrl = url;
    rememberObservedApi(url);
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function patchedSend(...args) {
    this.addEventListener("load", () => {
      try {
        captureResponse(this.__aiLiveDirectorUrl || "", this.responseText || "");
      } catch (_error) {}
    });
    return originalSend.apply(this, args);
  };

  window.setInterval(() => {
    if (!latestPayload || Date.now() - lastSentAt < SEND_INTERVAL_MS - 250) return;
    lastSentAt = Date.now();
    window.postMessage({ type: "AI_LIVE_DIRECTOR_METRICS", payload: latestPayload }, "*");
  }, 1000);

  window.postMessage({
    type: "AI_LIVE_DIRECTOR_STATUS",
    payload: {
      pageHookInjected: true,
      injectedAt: Date.now(),
      targetApi: TARGET_API,
      liveId: liveIdFromUrl(location.href) || ""
    }
  }, "*");
})();
