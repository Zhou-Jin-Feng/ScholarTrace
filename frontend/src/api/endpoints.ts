/** Delivery and persisted research endpoint wrappers. */

import { apiRequest, type TokenProvider } from "./client";
import type { TaskEvent } from "../hooks/useTaskEvents";
import type {
  ApprovalPayload,
  BudgetView,
  ClaimsResponse,
  DependencyReport,
  EvidenceView,
  PlanningAuthorization,
  ResearchPlanView,
  TaskListItem,
  TaskListPage,
  TaskStatus,
} from "./types";

export interface ListTasksParams {
  status?: TaskStatus;
  cursor?: string;
  /** 1..100, default 20. */
  limit?: number;
}

export function listTasks(
  token: TokenProvider,
  params: ListTasksParams = {},
  signal?: AbortSignal,
): Promise<TaskListPage> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.cursor) query.set("cursor", params.cursor);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return apiRequest<TaskListPage>(`/research/tasks${suffix}`, token, { signal });
}

export function getTask(
  token: TokenProvider,
  taskId: string,
  signal?: AbortSignal,
): Promise<TaskListItem> {
  return apiRequest<TaskListItem>(`/research/tasks/${taskId}`, token, { signal });
}

export function getPlan(
  token: TokenProvider,
  taskId: string,
  signal?: AbortSignal,
): Promise<ResearchPlanView> {
  return apiRequest<ResearchPlanView>(`/research/tasks/${taskId}/plan`, token, { signal });
}

export function getPlanVersions(
  token: TokenProvider,
  taskId: string,
  signal?: AbortSignal,
): Promise<{ schema_version: "1.0"; versions: ResearchPlanView[] }> {
  return apiRequest(`/research/tasks/${taskId}/plan/versions`, token, { signal });
}

/**
 * Gate A: cost estimate for producing a plan. Generating a plan can itself
 * cost money, so this must be shown and acknowledged before `generatePlan`.
 */
export function estimatePlan(
  token: TokenProvider,
  taskId: string,
  signal?: AbortSignal,
): Promise<{
  schema_version: "1.0";
  estimated_cny: number | null;
  estimate_source: string;
  is_actual_bill: false;
  requires_acknowledgement: boolean;
  policy_sha256?: string | null;
  production_composed?: boolean;
  planning_authorization?: PlanningAuthorization | null;
  has_task_authorization?: boolean;
  runtime_policy?: {
    remote: { endpoint: string; model: string; model_version: string;
      input_cny_per_million: string; output_cny_per_million: string };
    local: { endpoint: string; model: string } | null;
    documind_url: string | null;
    data_fields: string[];
    allowed_search_providers: string[];
    max_papers: number;
  } | null;
}> {
  return apiRequest(`/research/tasks/${taskId}/plan/estimate`, token, {
    method: "POST",
    signal,
  });
}

/** Explicit user acknowledgement precedes generation; no automatic retries. */
export async function generatePlan(
  token: TokenProvider,
  taskId: string,
  acknowledgedMaxCny: number,
  signal?: AbortSignal,
  authorization?: PlanningAuthorization,
): Promise<ResearchPlanView> {
  await apiRequest(`/research/tasks/${taskId}/plan/acknowledge-cost`, token, {
    method: "POST", body: { acknowledged_max_cny: acknowledgedMaxCny,
      ...(authorization ? { authorization } : {}) }, signal,
  });
  return apiRequest<ResearchPlanView>(`/research/tasks/${taskId}/plan/generate`, token, {
    method: "POST", signal,
  });
}

/**
 * Gate B: execution approval, bound to a specific plan version and digest.
 * A digest mismatch yields 409 plan_version_stale and executes nothing.
 */
export function approvePlan(
  token: TokenProvider,
  taskId: string,
  payload: ApprovalPayload,
  signal?: AbortSignal,
): Promise<TaskListItem> {
  return apiRequest<TaskListItem>(`/research/tasks/${taskId}/approve`, token, {
    method: "POST",
    body: payload,
    signal,
  });
}

export function cancelTask(
  token: TokenProvider,
  taskId: string,
  signal?: AbortSignal,
): Promise<TaskListItem> {
  return apiRequest<TaskListItem>(`/research/tasks/${taskId}/cancel`, token, {
    method: "POST",
    signal,
  });
}

export function getClaims(
  token: TokenProvider,
  taskId: string,
  signal?: AbortSignal,
): Promise<ClaimsResponse> {
  return apiRequest<ClaimsResponse>(`/research/tasks/${taskId}/claims`, token, { signal });
}

/** Cross-task reads return 404 rather than leaking existence. */
export function getEvidence(
  token: TokenProvider,
  taskId: string,
  evidenceId: string,
  signal?: AbortSignal,
): Promise<EvidenceView> {
  return apiRequest<EvidenceView>(
    `/research/tasks/${taskId}/evidence/${encodeURIComponent(evidenceId)}`,
    token,
    { signal },
  );
}

export function getBudget(
  token: TokenProvider,
  taskId: string,
  signal?: AbortSignal,
): Promise<BudgetView> {
  return apiRequest<BudgetView>(`/research/tasks/${taskId}/budget`, token, { signal });
}

/** Read-only: no model loads, no secrets echoed back. */
export function getDependencies(
  token: TokenProvider,
  signal?: AbortSignal,
): Promise<DependencyReport> {
  return apiRequest<DependencyReport>("/health/dependencies", token, { signal });
}

export function createTask(token: TokenProvider, question: string, demoMode: TaskListItem["demo_mode"] | "real", key: string) {
  return apiRequest<TaskListItem>("/research/tasks", token, {
    method: "POST", idempotencyKey: key,
    body: { question, demo_mode: demoMode === "real" ? "success" : demoMode,
      execution_mode: demoMode === "real" ? "real" : "demo" },
  });
}
export function getTimeline(token: TokenProvider, taskId: string, signal?: AbortSignal) {
  return apiRequest<{ events: TaskEvent[] }>(`/research/tasks/${taskId}/timeline`, token, { signal });
}
export function resumeTask(token: TokenProvider, taskId: string, plan: ResearchPlanView) {
  return apiRequest<TaskListItem>(`/research/tasks/${taskId}/resume`, token, {
    method: "POST", body: { plan_version: plan.plan_version, plan_digest: plan.plan_digest },
  });
}

export function retryExports(token: TokenProvider, taskId: string) {
  return apiRequest<TaskListItem>(`/research/tasks/${taskId}/report/retry`, token, {
    method: "POST",
  });
}

/** Use a bearer fetch + local blob, not a credential-bearing download link. */
export async function fetchReport(token: TokenProvider, taskId: string, format: string, signal?: AbortSignal): Promise<Blob> {
  const value = token();
  const response = await fetch(`/api/v1/research/tasks/${encodeURIComponent(taskId)}/report?format=${format}`, {
    signal, headers: value ? { Authorization: `Bearer ${value}` } : {},
  });
  if (!response.ok) throw new Error(response.status === 404 ? "报告尚未生成" : `报告读取失败 (${response.status})`);
  return response.blob();
}
