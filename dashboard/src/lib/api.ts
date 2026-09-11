// Every call goes to /api/* on this origin; next.config.ts rewrites it to FastAPI.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export function apiUrl(path: string): string {
  return `/api${path}`
}

type ApiInit = Omit<RequestInit, "body"> & { json?: unknown; body?: BodyInit }

export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const { json, headers, body, ...rest } = init
  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      credentials: "same-origin",
      ...rest,
      headers: json === undefined ? headers : { "Content-Type": "application/json", ...headers },
      body: json === undefined ? body : JSON.stringify(json),
    })
  } catch {
    throw new ApiError(0, "Can't reach the server — check your connection")
  }
  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status})`
    try {
      message = formatDetail((await response.json()).detail) ?? message
    } catch {
      // not JSON — keep the generic message
    }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

// FastAPI sends a string for handled errors and a list of {msg, loc} for validation errors.
function formatDetail(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : String(item)))
      .join("; ")
  }
  return undefined
}

export function post<T>(path: string, json?: unknown): Promise<T> {
  return api<T>(path, { method: "POST", json })
}

export function put<T>(path: string, json: unknown): Promise<T> {
  return api<T>(path, { method: "PUT", json })
}

export function patch<T>(path: string, json: unknown): Promise<T> {
  return api<T>(path, { method: "PATCH", json })
}

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ""
}
