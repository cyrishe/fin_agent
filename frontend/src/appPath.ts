const configuredBase = String(import.meta.env.BASE_URL || "/").trim();

export const APP_BASE_PATH = configuredBase === "/"
  ? ""
  : `/${configuredBase.replace(/^\/+|\/+$/g, "")}`;

export function appPath(path: string): string {
  const normalized = `/${String(path || "").replace(/^\/+/, "")}`;
  if (!APP_BASE_PATH || normalized === APP_BASE_PATH || normalized.startsWith(`${APP_BASE_PATH}/`)) {
    return normalized;
  }
  return `${APP_BASE_PATH}${normalized}`;
}

export function stripAppBase(pathname: string): string {
  const normalized = (`/${String(pathname || "").replace(/^\/+/, "")}`.replace(/\/+$/, "") || "/");
  if (!APP_BASE_PATH) return normalized;
  if (normalized === APP_BASE_PATH) return "/";
  if (normalized.startsWith(`${APP_BASE_PATH}/`)) {
    return normalized.slice(APP_BASE_PATH.length) || "/";
  }
  return normalized;
}
