// Typed-ish API client. One place for the token and error handling.

const TOKEN_KEY = "forecast.token";
const USER_KEY = "forecast.user";

export const session = {
  get token() { return localStorage.getItem(TOKEN_KEY); },
  get user() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); } catch { return null; }
  },
  save(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
  can(...roles) {
    const mine = this.user?.roles || [];
    return mine.includes("admin") || roles.some((r) => mine.includes(r));
  },
};

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(method, path, { body, formData, raw } = {}) {
  const headers = {};
  if (session.token) headers.authorization = `Bearer ${session.token}`;
  if (body !== undefined) headers["content-type"] = "application/json";

  const res = await fetch(path, {
    method,
    headers,
    body: formData ? formData : body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401 && !path.includes("/auth/login")) {
    session.clear();
    window.dispatchEvent(new CustomEvent("session-expired"));
    throw new ApiError("Oturum süresi doldu, yeniden giriş yapın", 401, null);
  }
  if (raw) {
    if (!res.ok) throw new ApiError("Dosya üretilemedi", res.status, null);
    return res.blob();
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const detail = data?.detail ?? data?.message ?? `İstek başarısız (${res.status})`;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status, data);
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, body) => request("POST", p, { body }),
  del: (p) => request("DELETE", p),
  blob: (p, body) => request("POST", p, { body, raw: true }),
  upload: (p, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("POST", p, { formData: fd });
  },

  login: (email, password) => request("POST", "/api/v1/auth/login", { body: { email, password } }),
  demoUsers: () => request("GET", "/api/v1/auth/demo-users"),

  cockpitOverview: (payload) => request("POST", "/api/v1/cockpit/overview", { body: payload }),
  cycle: () => request("GET", "/api/v1/time/cycle"),
  calendarEvents: (year) => request("GET", `/api/v1/time/calendar-events?year=${year}`),
  dimensions: () => request("GET", "/api/v1/dimensions"),
  tree: (dim, hier) => request("GET", `/api/v1/dimensions/${dim}/tree${hier ? `?hierarchy=${hier}` : ""}`),
  validate: (dim) => request("GET", `/api/v1/dimensions/${dim}/validate`),
  layers: () => request("GET", "/api/v1/layers"),
  measures: () => request("GET", "/api/v1/measures"),
  reasonCodes: () => request("GET", "/api/v1/reason-codes"),
  models: () => request("GET", "/api/v1/models"),
  drivers: () => request("GET", "/api/v1/drivers"),
  driverSets: () => request("GET", "/api/v1/driver-sets"),
  driverSetValues: (id, code) =>
    request("GET", `/api/v1/driver-sets/${id}/values${code ? `?driver_code=${code}` : ""}`),
  setAssumption: (id, payload) => request("POST", `/api/v1/driver-sets/${id}/assumption`, { body: payload }),
  createDriverSet: (payload) => request("POST", "/api/v1/driver-sets", { body: payload }),
  groups: () => request("GET", "/api/v1/groups"),
  createGroup: (payload) => request("POST", "/api/v1/groups", { body: payload }),
  savedViews: () => request("GET", "/api/v1/saved-views"),
  saveView: (payload) => request("POST", "/api/v1/saved-views", { body: payload }),

  gridQuery: (payload) => request("POST", "/api/v1/grid/query", { body: payload }),
  explain: (payload) => request("POST", "/api/v1/grid/explain", { body: payload }),
  override: (payload) => request("POST", "/api/v1/override", { body: payload }),
  revertOverride: (id) => request("POST", `/api/v1/override/${id}/revert`),
  copyLayer: (payload) => request("POST", "/api/v1/layers/copy", { body: payload }),
  insights: () => request("GET", "/api/v1/insights"),
  addInsight: (payload) => request("POST", "/api/v1/insights", { body: payload }),

  scenarios: () => request("GET", "/api/v1/scenarios"),
  createScenario: (payload) => request("POST", "/api/v1/scenarios", { body: payload }),
  copyScenario: (id, name) => request("POST", `/api/v1/scenarios/${id}/copy?name=${encodeURIComponent(name)}`),
  addRule: (id, payload) => request("POST", `/api/v1/scenarios/${id}/rules`, { body: payload }),
  deleteRule: (id) => request("DELETE", `/api/v1/scenarios/rules/${id}`),
  reorderRules: (id, ids) => request("POST", `/api/v1/scenarios/${id}/rules/reorder`, { body: { rule_ids: ids } }),
  run: (ids) => request("POST", "/api/v1/runs", { body: { scenario_ids: ids } }),
  runs: () => request("GET", "/api/v1/runs"),
  runStatus: (id) => request("GET", `/api/v1/runs/${id}`),
  versions: () => request("GET", "/api/v1/versions"),
  saveVersion: (id, payload) => request("POST", `/api/v1/versions/${id}/save`, { body: payload }),
  discardVersion: (id) => request("DELETE", `/api/v1/versions/${id}`),
  compare: (ids, layer = "consensus") =>
    request("GET", `/api/v1/versions/compare?ids=${ids.join(",")}&layer=${layer}`),
  buckets: (grain = "month") => request("GET", `/api/v1/buckets?grain=${grain}`),

  accuracy: (q) => request("GET", `/api/v1/analysis/accuracy?${new URLSearchParams(q)}`),
  fva: (lag) => request("GET", `/api/v1/analysis/fva?lag=${lag}`),
  variance: (q) => request("GET", `/api/v1/analysis/variance?${new URLSearchParams(q)}`),
  budgetGap: (versionId) => request("GET", `/api/v1/analysis/budget-gap?version_id=${versionId}`),
  alerts: (status = "open") => request("GET", `/api/v1/analysis/alerts?status=${status}`),
  setAlertStatus: (id, status) => request("POST", `/api/v1/analysis/alerts/${id}/status?status=${status}`),

  reports: () => request("GET", "/api/v1/reports"),
  runReport: (code, params) => request("POST", "/api/v1/reports/run", { body: { code, params } }),
  exportReport: (code, params, fmt) =>
    request("POST", `/api/v1/reports/export?fmt=${fmt}`, { body: { code, params }, raw: true }),
  exportGrid: (payload) => request("POST", "/api/v1/export/grid", { body: payload, raw: true }),
  importPreview: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("POST", "/api/v1/import/grid/preview", { formData: fd });
  },
  importApply: (payload, reasonCodeId, note) =>
    request("POST", "/api/v1/import/grid/apply", { body: { payload, reason_code_id: reasonCodeId, note } }),
  writeConsensus: (versionId) => request("POST", `/api/v1/store/write-consensus?version_id=${versionId}`),
  biViews: () => request("GET", "/api/v1/bi/views"),
  forecastApi: (q) => request("GET", `/api/v1/forecast?${new URLSearchParams(q)}`),
  scheduled: () => request("GET", "/api/v1/reports/scheduled"),
  schedule: (payload) => request("POST", "/api/v1/reports/scheduled", { body: payload }),

  approvalTrail: (id) => request("GET", `/api/v1/approvals/${id}`),
  approve: (payload) => request("POST", "/api/v1/approvals", { body: payload }),
  advanceCycle: () => request("POST", "/api/v1/time/cycle/advance"),

  sector: () => request("GET", "/api/v1/sector"),
  connections: () => request("GET", "/api/v1/admin/connections"),
  testConnection: () => request("POST", "/api/v1/admin/connections/test"),
  syncModels: () => request("POST", "/api/v1/admin/models/sync"),
  users: () => request("GET", "/api/v1/admin/users"),
  audit: (limit = 100) => request("GET", `/api/v1/admin/audit?limit=${limit}`),
  consensusRule: () => request("GET", "/api/v1/admin/consensus-rule"),
  setConsensusRule: (rule) => request("POST", "/api/v1/admin/consensus-rule", { body: { rule } }),
  purgeDrafts: () => request("POST", "/api/v1/admin/maintenance/purge-drafts"),
  modelPackages: () => request("GET", "/api/v1/admin/model-packages"),

  assistantTools: () => request("GET", "/api/v1/assistant/tools"),
  assistantPrompt: () => request("GET", "/api/v1/assistant/system-prompt"),
  assistantChat: (message, context) =>
    request("POST", "/api/v1/assistant/chat", { body: { message, context } }),
};
