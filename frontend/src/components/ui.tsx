import type { ReactNode } from "react";
import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";

export const statusLabels: Record<string, string> = {
  created: "已创建",
  waiting_approval: "待审批",
  queued: "排队中",
  running: "研究中",
  completed: "已完成",
  degraded: "降级完成",
  rejected: "已拒绝",
  cancelling: "取消中",
  cancelled: "已取消",
  failed: "失败",
  interrupted: "已中断",
};
export function Status({ value }: { value: string }) {
  return (
    <span className={`pill status-${value}`}>
      {statusLabels[value] ?? value}
    </span>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <Inbox size={28} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading() {
  return (
    <p className="loading" role="status">
      <LoaderCircle className="spin" size={16} /> 正在读取…
    </p>
  );
}
export function ErrorNotice({ error }: { error: string | null }) {
  return error ? (
    <div className="error-notice" role="alert">
      <AlertCircle size={17} />
      <span>{error}</span>
    </div>
  ) : null;
}
export const messageOf = (error: unknown) =>
  error instanceof Error ? error.message : "读取失败，请重试";
export const dateLabel = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
