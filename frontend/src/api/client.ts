/**
 * Typed transport for the research workspace API.
 *
 * Request shaping, bearer authentication and FastAPI error normalisation.
 * The client consumes the local delivery API, not model provider endpoints.
 */

import type { ApiErrorCode } from "./types";

const API_BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | "unknown";
  readonly requestId: string | null;

  constructor(
    status: number,
    code: ApiErrorCode | "unknown",
    message: string,
    requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }

  /** A stale plan means the user reviewed an older version; re-read, never auto-approve. */
  get isStalePlan(): boolean {
    return this.status === 409 && this.code === "plan_version_stale";
  }

  /** A gate, not a fault: the paid profile simply is not approved yet. */
  get isPaidProfileGate(): boolean {
    return this.code === "paid_profile_not_approved";
  }
}

export interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

/**
 * Bearer token accessor. The token is held by the caller, never persisted to
 * localStorage and never appended to a URL except on the two GET endpoints
 * (events, report) that need it for EventSource/browser download compatibility.
 */
export type TokenProvider = () => string | null;

export async function apiRequest<T>(
  path: string,
  tokenProvider: TokenProvider,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenProvider();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });

  const requestId = response.headers.get("X-Request-ID");
  if (!response.ok) {
    let code: ApiErrorCode | "unknown" = "unknown";
    let message = `Request failed with status ${response.status}`;
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === "object") {
        const outer = payload as Record<string, unknown>;
        const detail = outer.detail;
        const nested = detail && typeof detail === "object" && !Array.isArray(detail)
          ? detail as Record<string, unknown> : outer;
        if (typeof nested.code === "string") code = nested.code as ApiErrorCode;
        if (typeof detail === "string") message = detail;
        else if (typeof nested.detail === "string") message = nested.detail;
        else if (Array.isArray(detail)) {
          const issues = detail.filter(item => item && typeof item.msg === "string")
            .map(item => `${Array.isArray(item.loc) ? item.loc.join(".") + ": " : ""}${item.msg}`);
          if (issues.length) message = issues.join("; ");
        }
      }
    } catch {
      // Non-JSON error body: keep the generic message rather than guessing.
    }
    throw new ApiError(response.status, code, message, requestId);
  }
  return (await response.json()) as T;
}
