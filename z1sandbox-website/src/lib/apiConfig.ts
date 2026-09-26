/**
 * Centralized API & Backend Configuration Helper.
 * All API URLs and backend endpoints are dynamically derived from environment variables
 * without hardcoding any specific IP addresses or domain names.
 */

/**
 * Returns the base API URL configured in the environment.
 * Priority: window._env_.VITE_API_BASE_URL -> import.meta.env.VITE_API_BASE_URL -> ""
 */
export function getApiBaseUrl(): string {
  let raw = (window as any)._env_?.VITE_API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "";
  if (!raw || typeof raw !== "string") return "";
  return raw
    .trim()
    .replace(/^http:\/\/http:\/\//, "http://")
    .replace(/^https:\/\/https:\/\//, "https://")
    .replace(/\/+$/, "");
}

/**
 * Resolves a backend URL.
 * - If the URL is already absolute (starts with http:// or https://), it is returned trimmed.
 * - If it is relative (e.g. /api/v1/01sbx), it is prepended with the configured API base URL.
 * - If no API base URL is configured, the relative path is preserved for same-origin proxying.
 */
export function resolveBackendUrl(pathOrUrl: string, explicitBase?: string): string {
  if (!pathOrUrl) return "";
  const trimmed = pathOrUrl.trim();
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimmed.replace(/\/+$/, "");
  }
  const base = (explicitBase !== undefined ? explicitBase : getApiBaseUrl()).replace(/\/+$/, "");
  const cleanPath = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  return base ? `${base}${cleanPath}` : cleanPath;
}

/**
 * Returns the health endpoint URL.
 */
export function getHealthUrl(): string {
  const base = getApiBaseUrl();
  return base ? `${base}/health` : "/health";
}

/**
 * Returns the Grafana dashboard URL.
 */
export function getGrafanaUrl(): string {
  const base = getApiBaseUrl();
  return `${base}/grafana/d/codeinspector-main/codeinspector-system-dashboard?orgId=1&kiosk`;
}

/**
 * Loads default and configured backends dynamically from VITE_DASHBOARD_BACKENDS_JSON,
 * resolving all relative baseUrls and documentationUrls through resolveBackendUrl().
 */
export function getDefaultBackends(): any[] {
  const envJson = (window as any)._env_?.VITE_DASHBOARD_BACKENDS_JSON || import.meta.env.VITE_DASHBOARD_BACKENDS_JSON;
  if (envJson) {
    try {
      let raw = typeof envJson === "string" ? envJson.trim() : envJson;
      if (typeof raw === "string" && raw.startsWith("'") && raw.endsWith("'")) {
        raw = raw.substring(1, raw.length - 1);
      }
      const data = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (Array.isArray(data) && data.length > 0) {
        return data.map((b: any) => {
          const defaultBase = b.baseUrl || `/api/v1/${b.id.toLowerCase()}`;
          const resolvedBase = resolveBackendUrl(defaultBase);
          return {
            ...b,
            baseUrl: resolvedBase,
            documentationUrl: b.documentationUrl ? resolveBackendUrl(b.documentationUrl) : `${resolvedBase}/docs`,
            isSubscribed: b.id === "Z1_SANDBOX"
          };
        });
      }
    } catch (e) {
      console.warn("[apiConfig] Failed to parse VITE_DASHBOARD_BACKENDS_JSON from environment:", e);
    }
  }

  // Clean dynamic fallback without hardcoded IPs or domains
  const defaultBase = resolveBackendUrl("/api/v1/01sbx");
  return [
    {
      id: "Z1_SANDBOX",
      name: "01 Sandbox",
      description: "Production-grade hardened cluster for secure code execution.",
      icon: "terminal",
      color: "indigo",
      baseUrl: defaultBase,
      documentationUrl: `${defaultBase}/docs`,
      isSubscribed: true
    }
  ];
}
