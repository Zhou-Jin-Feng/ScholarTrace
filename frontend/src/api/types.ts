/** Public transport shapes of the implemented delivery API. */

export type TaskStatus =
  | "created"
  | "waiting_approval"
  | "queued"
  | "running"
  | "completed"
  | "degraded"
  | "rejected"
  | "cancelling"
  | "cancelled"
  | "failed"
  | "interrupted";

export type TaskPhase =
  | "plan"
  | "search"
  | "evidence"
  | "citations"
  | "verification"
  | "synthesis"
  | "done";

/** Demo is the existing deterministic path; real requires an approved executor. */
export type ExecutionMode = "demo" | "real";

/** Who produced a plan. `fixture` must stay visible in the UI. */
export type PlanOrigin = "fixture" | "api-strong" | "manual";

/** Verifier provenance. fixture output is never evidence of real semantic quality (ADR-016). */
export type VerifierKind = "fixture" | "model" | "human" | "deterministic";

export type VerificationStatus =
  | "supported"
  | "partially_supported"
  | "unsupported"
  | "conflicted"
  | "unverified";

export type EvidenceLevel = "fulltext" | "abstract" | "metadata";

export interface TaskListItem {
  task_id: string;
  title: string;
  status: TaskStatus;
  phase: TaskPhase;
  execution_mode: ExecutionMode;
  created_at: string;
  updated_at: string;
  thread_id: string;
  question: string;
  demo_mode: "success" | "degraded" | "production_unavailable";
  event_count: number;
  artifact_count: number;
  metrics: Record<string, unknown>;
  degradations: string[];
}

export interface VerificationSummary {
  supported: number;
  partially_supported: number;
  conflicted: number;
  excluded_unsupported: number;
  verifier_kind: VerifierKind;
}

export interface TaskListPage {
  schema_version: "1.0";
  items: TaskListItem[];
  /** Opaque `(created_at, task_id)` cursor. Never an OFFSET. */
  next_cursor: string | null;
  total_known: number;
}

export interface BudgetPlan {
  max_cny: number;
  max_api_calls: number;
  max_wall_clock_seconds: number;
  estimate_source: string;
  /** Always false until a real provider bill is reconciled. */
  is_actual_bill: boolean;
}

export interface PlanDetails {
  title: string;
  objective: string;
  inclusion_criteria: string[];
  retrieval_cutoff: string;
  subquestions: {
    subquestion_id: string;
    question: string;
    evidence_required: "fulltext" | "abstract" | "metadata";
    priority: "critical" | "high" | "normal";
  }[];
}

export interface ResearchPlanView {
  schema_version: "1.0";
  task_id: string;
  plan_version: number;
  /** Approval must echo this digest; a mismatch is 409 plan_version_stale. */
  plan_digest: string;
  generated_by: PlanOrigin;
  generated_at: string;
  approval_state: "waiting_approval" | "approved" | "rejected" | "superseded";
  sub_questions: string[];
  source_scope: {
    providers: string[];
    year_from: number;
    year_to: number;
    /** M2 constrains this to 3..5; out of range is 422, never silently clamped. */
    min_papers: number;
    max_papers: number;
  };
  exclusions: string[];
  budget_plan: BudgetPlan;
  supersedes_version: number | null;
  details?: PlanDetails | null;
}

export interface PhaseLimits {
  max_cny: string;
  max_remote_calls: number;
  max_local_calls: number;
  max_external_requests: number;
}

export interface PlanningAuthorization extends PhaseLimits {
  policy_sha256: string;
  deadline_at: string;
  max_wall_clock_seconds: number;
  generation: number;
  planning: PhaseLimits;
}

export interface ApprovalPayload {
  action: "approve" | "modify" | "reject";
  plan_version: number;
  plan_digest: string;
  modified_plan?: Pick<ResearchPlanView,
    "sub_questions" | "source_scope" | "exclusions" | "budget_plan" | "details">;
  reason?: string;
  execution_authorization?: PhaseLimits & { policy_sha256: string; generation: number };
}

export interface ClaimView {
  claim_id: string;
  text: string;
  sub_question: string | null;
  verification: {
    status: VerificationStatus;
    verifier_kind: VerifierKind;
    /** Required whenever an included claim is not fully supported. */
    marker: string | null;
    validated: boolean;
  };
  evidence_ids: string[];
  included_in_report: boolean;
}

export interface ClaimsResponse {
  schema_version: "1.0";
  task_id: string;
  claims: ClaimView[];
  excluded_count: number;
  exclusion_reasons: Record<string, number>;
}

export interface EvidenceView {
  schema_version: "1.0";
  evidence_id: string;
  task_id: string;
  claim_ids: string[];
  evidence_level: EvidenceLevel;
  paper: {
    paper_id: string;
    title: string;
    source: string;
    access_level: string;
    version: string | null;
  };
  binding: null | {
    document_key: string;
    index_id: string;
    source_sha256: string;
    documind_version: string;
  };
  chunk: {
    chunk_id: string | null;
    content_sha256: string | null;
    /** null when the source gives no page — never fabricated. */
    page_number: number | null;
    char_start: number | null;
    char_end: number | null;
  };
  /** Verbatim source text only. */
  excerpt: string;
  excerpt_is_verbatim: boolean;
  /** Model inference, kept strictly separate from `excerpt`. */
  inference_note: string | null;
}

export interface BudgetUsageView {
  cny: number;
  api_calls: number;
}

export interface BudgetView {
  schema_version: "1.0";
  task_id: string;
  reservation: null | {
    task_id: string;
    plan_version: number;
    plan_digest: string;
    reserved: BudgetUsageView & { wall_clock_seconds: number };
    estimate_source: string;
    reserved_at: string;
    settled: null | BudgetUsageView & { at: string; reason: string };
    state: "open" | "settled" | "reconciliation_required";
    recorded_usage: BudgetUsageView;
    reconciliation_required: boolean;
    is_actual_bill: boolean;
  };
  measured_usage: { external_cost_cny: number; api_calls: number; elapsed_seconds: number;
    queries: number; candidate_papers: number; fulltext_papers: number; rag_calls: number;
    llm_input_tokens: number; llm_output_tokens: number; model_calls: number; local_gpu_seconds: number } | null;
  journal_accounting?: null | {
    measured_cny: number; known_cny: number; held_cny: number; committed_cny: number;
    committed_calls: number; committed_local_calls: number; committed_external_requests: number;
    uncertain_effects: number; completed_effects: number; is_actual_bill: false;
  };
  projection_state?: "legacy" | "pending" | "synced" | "reconciliation_required";
  committed_cny_all_tasks?: number;
  note?: string;
  is_actual_bill: boolean;
}

export type DependencyState =
  | "draining"
  | "ready"
  | "degraded"
  | "disabled"
  | "unavailable"
  | "unknown"
  | "error";

/** One dependency's verdict. `reason` is safe to display: never a credential. */
export interface DependencyEntry {
  state: DependencyState;
  configured?: boolean;
  reason: string | null;
  remediation: string | null;
}

/**
 * Shape matches the implemented `GET /health/dependencies` rather than the
 * T04 sketch: queue keys mirror the pre-existing queue snapshot, and the four
 * dependencies are grouped so the UI can render them uniformly.
 */
export interface DependencyReport {
  schema_version: "1.0";
  api: DependencyState;
  queue: {
    accepting: boolean;
    queued: number;
    active: number;
    max_queue_size: number;
    max_workers: number;
    submitted: number;
  };
  dependencies: {
    documind: DependencyEntry;
    /** Absent local runtime reads as `unavailable`, never optimistically ready. */
    local_model: DependencyEntry;
    search_provider: DependencyEntry;
    /** `disabled` when the paid profile is unapproved: policy, not a fault. */
    api_strong: DependencyEntry;
  };
  demo_mode: DependencyEntry;
  real_mode: {
    state: DependencyState;
    blocking_dependencies: string[];
    pipeline_stages_wired: boolean;
  };
  overall: DependencyState;
  probe_policy: string;
}

/** Stable error codes from §5 of the T04 design. */
export type ApiErrorCode =
  | "invalid_cursor"
  | "unauthorized"
  | "task_not_found"
  | "evidence_not_found"
  | "plan_version_stale"
  | "plan_version_required"
  | "task_terminal"
  | "idempotency_key_conflict"
  | "paper_count_out_of_range"
  | "queue_full"
  | "dependency_unavailable"
  | "paid_profile_not_approved";
