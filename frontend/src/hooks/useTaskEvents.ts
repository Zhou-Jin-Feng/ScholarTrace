/** Resumable named-event transport. Timeline, retries and terminal state are
 * independent of whether report rendering succeeds. */

import { useCallback, useEffect, useRef, useState } from "react";

export type StreamStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error";

export interface TaskEvent {
  sequence: number;
  kind: string;
  node: string;
  created_at: string;
  payload: Record<string, unknown>;
}

/**
 * Kinds after which no further events arrive, verified against the kinds the
 * backend actually emits (`service.py`) — not guessed from status names.
 *
 * Deliberately excludes `task_failed`, `task_cancelled` and `plan_rejected`:
 * those are followed by `exports_ready`, so treating them as terminal would
 * close the stream before the run's own exports arrived.
 *
 * `task_interrupted` pauses for explicit recovery and is not a terminal event.
 */
const TERMINAL_KINDS = new Set([
  // Normal close: last event on every terminal path.
  "exports_ready",
  // Failure close: emitted instead when export rendering raises.
  "task_terminal_no_exports",
]);

/** Exponential backoff, capped. Never an unbounded retry loop. */
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];
const MAX_ATTEMPTS = BACKOFF_MS.length;

// EventSource.onmessage only receives unnamed events. Keep delivery/workflow
// named kinds explicit; extend this contract when a new producer is wired.
export const EVENT_KINDS = [
  "task_created", "plan_cost_acknowledged", "plan_generated", "plan_approved",
  "plan_modified", "plan_rejected", "plan_created", "approval_required",
  "budget_reserved", "budget_settled", "budget_reservation_reconciled",
  "queue_rejected", "search_completed", "evidence_completed", "citations_completed",
  "verification_completed", "synthesis_completed", "fallback_to_b3",
  "workflow_degraded", "workflow_finished", "real_execution_unavailable",
  "task_cancel_requested", "task_cancelled", "task_failed", "search_round_completed",
  "search_stopped", "search_timed_out", "budget_exhausted",
  "task_interrupted", "task_resume_requested", "retrieval_completed",
  ...TERMINAL_KINDS,
];

export interface UseTaskEventsResult {
  events: TaskEvent[];
  status: StreamStatus;
  /** Last sequence we durably received; the resume point. */
  lastSequence: number | null;
  attempt: number;
  nextRetryMs: number | null;
  isTerminal: boolean;
  retryNow: () => void;
}

export function useTaskEvents(
  taskId: string | null,
  tokenProvider: () => string | null,
): UseTaskEventsResult {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [attempt, setAttempt] = useState(0);
  const [isTerminal, setIsTerminal] = useState(false);
  const lastSequenceRef = useRef<number | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const timerRef = useRef<number | null>(null);
  const tokenRef = useRef(tokenProvider);
  tokenRef.current = tokenProvider;
  const terminalRef = useRef(false);

  const teardown = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const connect = useCallback(
    (attemptIndex: number) => {
      if (!taskId || terminalRef.current) return;
      teardown();
      setStatus(attemptIndex === 0 ? "connecting" : "reconnecting");

      // EventSource cannot set headers, so these two GETs accept a query token
      // (INTERFACE.md §5.1). Deploy over TLS with query-string logging off.
      const params = new URLSearchParams({ follow: "true" });
      const token = tokenRef.current();
      if (token) params.set("access_token", token);
      const resume = lastSequenceRef.current;
      if (resume !== null) params.set("last_event_id", `event:${resume}`);

      const source = new EventSource(
        `/api/v1/research/tasks/${taskId}/events?${params.toString()}`,
      );
      sourceRef.current = source;

      source.onopen = () => {
        if (sourceRef.current !== source) return;
        setStatus("open");
      };

      const receive = (message: MessageEvent) => {
        if (sourceRef.current !== source) return;
        let event: TaskEvent;
        try {
          event = JSON.parse(message.data) as TaskEvent;
        } catch {
          // A malformed frame must not kill the stream or the timeline.
          return;
        }
        if (!event || !Number.isSafeInteger(event.sequence) || event.sequence < 1 ||
            typeof event.kind !== "string" || typeof event.node !== "string" ||
            typeof event.created_at !== "string" || !event.payload ||
            typeof event.payload !== "object" || Array.isArray(event.payload)) return;
        // Replay after reconnect can repeat frames; sequence is monotonic.
        if (lastSequenceRef.current !== null && event.sequence <= lastSequenceRef.current) {
          return;
        }
        lastSequenceRef.current = event.sequence;
        setEvents((previous) => [...previous, event]);
        if (TERMINAL_KINDS.has(event.kind)) {
          terminalRef.current = true;
          setIsTerminal(true);
          setStatus("closed");
          teardown();
        }
      };
      source.onmessage = receive;
      EVENT_KINDS.forEach(kind => source.addEventListener(kind, receive));
      source.addEventListener("stream_end", (message: MessageEvent) => {
        if (sourceRef.current !== source) return;
        try {
          if (JSON.parse(message.data)?.reason !== "terminal") return;
        } catch { return; }
        terminalRef.current = true;
        setIsTerminal(true);
        setStatus("closed");
        teardown();
      });

      source.onerror = () => {
        if (sourceRef.current !== source) return;
        teardown();
        // Existing events are deliberately kept: the task is still running
        // server-side, and clearing them would misreport a transport blip.
        const next = attemptIndex + 1;
        if (next > MAX_ATTEMPTS) {
          setStatus("error");
          return;
        }
        setAttempt(next);
        setStatus("reconnecting");
        timerRef.current = window.setTimeout(
          () => connect(next),
          BACKOFF_MS[Math.min(attemptIndex, BACKOFF_MS.length - 1)],
        );
      };
    },
    [taskId, teardown],
  );

  useEffect(() => {
    setEvents([]);
    setIsTerminal(false);
    setAttempt(0);
    terminalRef.current = false;
    lastSequenceRef.current = null;
    if (!taskId) {
      teardown();
      setStatus("idle");
      return;
    }
    connect(0);
    return teardown;
  }, [taskId, connect, teardown]);

  const retryNow = useCallback(() => {
    setAttempt(0);
    connect(0);
  }, [connect]);

  return {
    events,
    status,
    lastSequence: lastSequenceRef.current,
    attempt,
    nextRetryMs:
      status === "reconnecting" ? BACKOFF_MS[Math.min(attempt - 1, BACKOFF_MS.length - 1)] : null,
    isTerminal,
    retryNow,
  };
}
