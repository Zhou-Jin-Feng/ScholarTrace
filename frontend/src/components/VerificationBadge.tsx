/**
 * Verification status badge.
 *
 * Encodes status three ways — symbol, text and colour — so status never depends
 * on colour alone (T03 §7). Two contract rules are enforced here rather than
 * left to callers:
 *
 *   - a fixture verifier can never render as "已核验" (ADR-016);
 *   - an included claim that is not fully supported must carry a marker.
 */

import type { VerificationStatus, VerifierKind } from "../api/types";

export interface VerificationBadgeProps {
  status: VerificationStatus;
  verifierKind: VerifierKind;
  marker?: string | null;
}

interface Presentation {
  symbol: string;
  label: string;
  tone: "ok" | "partial" | "bad" | "unverified";
}

function present(status: VerificationStatus, verifierKind: VerifierKind): Presentation {
  // A fixture or absent verifier proves engineering flow, not semantic support.
  if (verifierKind !== "model" && verifierKind !== "human") {
    return { symbol: "⚠", label: "未核验", tone: "unverified" };
  }
  switch (status) {
    case "supported":
      return { symbol: "✓", label: "已核验", tone: "ok" };
    case "partially_supported":
      return { symbol: "◐", label: "部分支持", tone: "partial" };
    case "conflicted":
      return { symbol: "✗", label: "证据冲突", tone: "bad" };
    case "unsupported":
      return { symbol: "✗", label: "不支持", tone: "bad" };
    case "unverified":
    default:
      return { symbol: "⚠", label: "未核验", tone: "unverified" };
  }
}

export function VerificationBadge({ status, verifierKind, marker }: VerificationBadgeProps) {
  const { symbol, label, tone } = present(status, verifierKind);
  const suffix = marker ? `:${marker}` : "";
  const detail =
    verifierKind === "fixture"
      ? "由 fixture verifier 产出,不代表真实语义核验"
      : verifierKind === "deterministic"
        ? "独立语义核验未运行"
        : undefined;

  return (
    <span
      className={`badge badge-${tone}`}
      title={detail}
      aria-label={`核验状态:${label}${suffix}${detail ? `。${detail}` : ""}`}
    >
      <span aria-hidden="true">{symbol}</span> {label}
      {suffix}
    </span>
  );
}
