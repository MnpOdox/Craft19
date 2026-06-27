const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { URL } = require("node:url");

const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, "public");
const CONFIG_PATH = path.join(ROOT, "config.json");
const EXAMPLE_CONFIG_PATH = path.join(ROOT, "config.example.json");

function loadConfig() {
  const configPath = fs.existsSync(CONFIG_PATH) ? CONFIG_PATH : EXAMPLE_CONFIG_PATH;
  return JSON.parse(fs.readFileSync(configPath, "utf8"));
}

const config = loadConfig();
const sessions = new Map();
const cache = new Map();
const CACHE_TTL = Number(config.cacheTtlMs || 300000);

function sendJson(res, status, data, extraHeaders = {}) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", ...extraHeaders });
  res.end(JSON.stringify(data));
}

function sendText(res, status, body, contentType = "text/plain; charset=utf-8") {
  res.writeHead(status, { "Content-Type": contentType });
  res.end(body);
}

function parseCookies(req) {
  const raw = req.headers.cookie || "";
  const cookies = {};
  raw.split(";").forEach((part) => {
    const [key, ...rest] = part.trim().split("=");
    if (!key) return;
    cookies[key] = decodeURIComponent(rest.join("="));
  });
  return cookies;
}

function createSession(user) {
  const sessionId = crypto.randomBytes(24).toString("hex");
  sessions.set(sessionId, {
    username: user.username,
    regions: user.regions || [],
    createdAt: Date.now(),
  });
  return sessionId;
}

function getSession(req) {
  const cookies = parseCookies(req);
  const sessionId = cookies.dashboard_sid;
  return sessionId ? sessions.get(sessionId) : null;
}

function clearExpiredCache() {
  const now = Date.now();
  for (const [key, entry] of cache.entries()) {
    if (entry.expiresAt <= now) {
      cache.delete(key);
    }
  }
}

function normalizeBody(body) {
  if (!body) return {};
  try {
    return JSON.parse(body);
  } catch (error) {
    return {};
  }
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk.toString("utf8");
      if (raw.length > 2 * 1024 * 1024) {
        reject(new Error("Request body too large"));
      }
    });
    req.on("end", () => resolve(raw));
    req.on("error", reject);
  });
}

function safePage(pageName) {
  return ["overview", "sales", "purchases", "stock", "finance", "expenses"].includes(pageName)
    ? pageName
    : "overview";
}

function getSourceForRegion(region) {
  return config.sources[region] || null;
}

function getCompanyIds(source, companyKey) {
  if (!source) return [];
  if (!companyKey || companyKey === "all") {
    return source.companies.map((company) => company.companyId);
  }
  return source.companies.filter((company) => company.key === companyKey).map((company) => company.companyId);
}

function validateScope(session, region) {
  return session && Array.isArray(session.regions) && session.regions.includes(region);
}

function buildCacheKey(page, region, company, query) {
  return JSON.stringify({ page, region, company, query });
}

async function fetchDashboardPage(page, region, company, query, forceRefresh = false) {
  clearExpiredCache();
  const source = getSourceForRegion(region);
  if (!source) {
    throw new Error(`Unknown region: ${region}`);
  }

  const payload = {
    period: query.period || "this_month",
    date_from: query.date_from || null,
    date_to: query.date_to || null,
    company_ids: getCompanyIds(source, company),
  };

  const cacheKey = buildCacheKey(page, region, company, payload);
  if (!forceRefresh && cache.has(cacheKey)) {
    return cache.get(cacheKey).value;
  }

  const response = await fetch(`${source.baseUrl}/api/dashboard/${page}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [source.apiKeyHeader || "X-DASHBOARD-KEY"]: source.apiKey || "",
    },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) {
    const message = result.error || `Failed to load ${page} data`;
    throw new Error(message);
  }
  const enriched = {
    ...result,
    requestedScope: {
      region,
      company: company || (region === "india" ? "all" : "uae"),
      companyLabel:
        company && company !== "all"
          ? (source.companies.find((entry) => entry.key === company) || {}).label || company
          : source.name,
    },
  };
  cache.set(cacheKey, {
    value: enriched,
    expiresAt: Date.now() + CACHE_TTL,
  });
  return enriched;
}

function appMetadata(session) {
  const regions = Object.entries(config.sources)
    .filter(([region]) => validateScope(session, region))
    .map(([region, source]) => ({
      key: region,
      label: source.name,
      companies: source.companies.map((company) => ({
        key: company.key,
        label: company.label,
      })),
    }));

  return {
    pages: [
      { key: "overview", label: "Overview" },
      { key: "sales", label: "Sales" },
      { key: "purchases", label: "Purchases" },
      { key: "stock", label: "Stock" },
      { key: "finance", label: "Finance" },
      { key: "expenses", label: "Expenses" },
    ],
    regions,
  };
}

function serveStatic(req, res, pathname) {
  const targetPath = pathname === "/" ? path.join(PUBLIC_DIR, "index.html") : path.join(PUBLIC_DIR, pathname);
  const normalized = path.normalize(targetPath);
  if (!normalized.startsWith(PUBLIC_DIR) || !fs.existsSync(normalized) || fs.statSync(normalized).isDirectory()) {
    sendText(res, 404, "Not found");
    return;
  }
  const ext = path.extname(normalized).toLowerCase();
  const contentTypes = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
  };
  sendText(res, 200, fs.readFileSync(normalized), contentTypes[ext] || "application/octet-stream");
}

const server = http.createServer(async (req, res) => {
  try {
    const currentUrl = new URL(req.url, "http://localhost");
    const pathname = currentUrl.pathname;
    const session = getSession(req);

    if (pathname === "/api/login" && req.method === "POST") {
      const rawBody = await readRequestBody(req);
      const body = normalizeBody(rawBody);
      const user = (config.users || []).find(
        (entry) => entry.username === body.username && entry.password === body.password,
      );
      if (!user) {
        sendJson(res, 401, { ok: false, error: "Invalid username or password." });
        return;
      }
      const sessionId = createSession(user);
      sendJson(
        res,
        200,
        { ok: true, session: { username: user.username, regions: user.regions || [] } },
        { "Set-Cookie": `dashboard_sid=${encodeURIComponent(sessionId)}; Path=/; HttpOnly; SameSite=Lax` },
      );
      return;
    }

    if (pathname === "/api/logout" && req.method === "POST") {
      const cookies = parseCookies(req);
      if (cookies.dashboard_sid) {
        sessions.delete(cookies.dashboard_sid);
      }
      sendJson(
        res,
        200,
        { ok: true },
        { "Set-Cookie": "dashboard_sid=; Path=/; HttpOnly; Max-Age=0; SameSite=Lax" },
      );
      return;
    }

    if (pathname === "/api/session" && req.method === "GET") {
      if (!session) {
        sendJson(res, 401, { ok: false, error: "Not authenticated." });
        return;
      }
      sendJson(res, 200, {
        ok: true,
        session: {
          username: session.username,
          regions: session.regions,
        },
        app: appMetadata(session),
      });
      return;
    }

    if (pathname.startsWith("/api/dashboard/") && req.method === "GET") {
      if (!session) {
        sendJson(res, 401, { ok: false, error: "Authentication required." });
        return;
      }
      const page = safePage(pathname.split("/").pop());
      const region = currentUrl.searchParams.get("region") || "india";
      const company = currentUrl.searchParams.get("company") || (region === "india" ? "all" : "uae");
      const refresh = currentUrl.searchParams.get("refresh") === "1";
      if (!validateScope(session, region)) {
        sendJson(res, 403, { ok: false, error: "You are not allowed to view this region." });
        return;
      }
      const result = await fetchDashboardPage(
        page,
        region,
        company,
        {
          period: currentUrl.searchParams.get("period"),
          date_from: currentUrl.searchParams.get("date_from"),
          date_to: currentUrl.searchParams.get("date_to"),
        },
        refresh,
      );
      sendJson(res, 200, result);
      return;
    }

    serveStatic(req, res, pathname);
  } catch (error) {
    sendJson(res, 500, { ok: false, error: error.message || "Unexpected server error." });
  }
});

const port = Number(process.env.PORT || 8787);
server.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`Dashboard Hub listening on http://127.0.0.1:${port}`);
});
