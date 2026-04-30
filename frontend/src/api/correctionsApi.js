const CORRECTIONS_PATH = "/api/corrections";
const DEFAULT_API_ORIGIN = "http://127.0.0.1:8000";
const STATIC_POST_UNSUPPORTED = new Set([405, 501]);

function uniqueUrls(urls) {
  return [...new Set(urls)];
}

function resolveSameOriginUrl(locationHref) {
  return new URL(CORRECTIONS_PATH, locationHref).href;
}

function resolveFallbackUrl() {
  return new URL(CORRECTIONS_PATH, DEFAULT_API_ORIGIN).href;
}

function getConfiguredApiBase() {
  if (typeof window === "undefined") {
    return "";
  }
  return String(window.MAS_CORRECTIONS_API_BASE || "").trim();
}

export function buildCorrectionsEndpoints({ locationHref } = {}) {
  const href = locationHref
    || (typeof window !== "undefined" ? window.location.href : DEFAULT_API_ORIGIN);
  const configuredBase = getConfiguredApiBase();
  const urls = [];

  if (configuredBase) {
    urls.push(new URL(CORRECTIONS_PATH, configuredBase).href);
  }

  urls.push(resolveSameOriginUrl(href));
  urls.push(resolveFallbackUrl());
  return uniqueUrls(urls);
}

export async function submitCorrections(payload, options = {}) {
  const fetchImpl = options.fetchImpl || fetch;
  const endpoints = buildCorrectionsEndpoints({
    locationHref: options.locationHref,
  });
  let lastResult = {};
  let lastStatus = 0;

  for (const endpoint of endpoints) {
    const response = await fetchImpl(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    lastStatus = response.status;
    lastResult = await response.json().catch(() => ({}));

    if (response.ok) {
      return lastResult;
    }

    if (!STATIC_POST_UNSUPPORTED.has(response.status)) {
      const message = lastResult.error || response.status;
      throw new Error(String(message));
    }
  }

  throw new Error(
    `POST /api/corrections 不可用（${lastResult.error || lastStatus}）。请使用 python scripts/server.py 启动审核台。`,
  );
}
