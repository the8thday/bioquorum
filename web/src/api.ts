function apiPath(path: string) {
  if (typeof location === "undefined" || !path.startsWith("/api/") || ["/api/projects", "/api/workbench-settings"].includes(path)) return path;
  return location.pathname.replace(/\/$/, "") + path;
}

export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiPath(path), body === undefined ? {signal} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export const sessionPath = (id: string) => apiPath("/api/sessions/" + encodeURIComponent(id));
export const figurePath = (base: string, hash: string) => base + "/figures/" + encodeURIComponent(hash);
