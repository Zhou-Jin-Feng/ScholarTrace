import type { BudgetView, TaskListItem } from "../../api/types";
import type { TaskEvent, UseTaskEventsResult } from "../../hooks/useTaskEvents";
import { dateLabel, Empty } from "../../components/ui";

export function BudgetSummary({ budget }: { budget: BudgetView | null }) {
  const reservation = budget?.reservation;
  const accounting = budget?.journal_accounting;
  const pending = budget?.projection_state === "pending";
  const uncertain = Boolean(accounting?.uncertain_effects || reservation?.reconciliation_required);
  return (
    <div className="panel budget-panel">
      <span className="eyebrow">COST CONTROL</span>
      <h2>预算与计量</h2>
      <div className="budget-grid">
        <div>
          <span>预留上限</span>
          <strong>
            {reservation ? `¥${reservation.reserved.cny.toFixed(2)}` : "未预留"}
          </strong>
        </div>
        <div>
          <span>已知计量下界</span>
          <strong>
            {accounting
              ? `¥${accounting.known_cny.toFixed(4)}`
              : reservation
              ? `¥${reservation.recorded_usage.cny.toFixed(2)}`
              : "未记录"}
          </strong>
        </div>
        <div>
          <span>结算金额</span>
          <strong>
            {reservation?.settled
              ? `¥${reservation.settled.cny.toFixed(2)}`
              : "未结算"}
          </strong>
        </div>
      </div>
      <p className={uncertain || pending ? "notice" : "muted"}>
        {pending
          ? "用量待同步。上方显示调用账本已知金额，不代表完整账单；同步恢复不会重复调用。"
          : uncertain
          ? "外部结果未知，仍保留预留额度，需人工对账。已知为零不代表实际费用为零。"
          : "预留不等于消费；本地参考计量不是提供方账单。"}
      </p>
    </div>
  );
}
export function mergeTimeline(saved: TaskEvent[], live: TaskEvent[]) {
  return [
    ...new Map(
      [...saved, ...live].map((item) => [item.sequence, item]),
    ).values(),
  ].sort((a, b) => a.sequence - b.sequence);
}
export function RunOverview({
  task,
  events,
  stream,
  budget,
}: {
  task: TaskListItem;
  events: TaskEvent[];
  stream: UseTaskEventsResult;
  budget: BudgetView | null;
}) {
  return (
    <div className="overview">
      <BudgetSummary budget={budget} />
      {task.degradations.length > 0 && (
        <div className="notice">
          <div>
            <strong>运行限制与降级</strong>
            <ul>
              {task.degradations.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      <section className="panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">PERSISTED ACTIVITY</span>
            <h2>研究时间线</h2>
          </div>
          <span className="pill">事件流 · {stream.status}</span>
        </div>
        <p className="muted">
          展示已持久化事件，不估算虚假进度百分比。离线研究的阶段记录可能在流水线返回后集中同步，不代表逐阶段即时推送。
        </p>
        {["error", "reconnecting"].includes(stream.status) && (
          <div className="notice">
            <span>
              连接中断不会取消任务。重试 {stream.attempt}/5
              {stream.nextRetryMs
                ? `，下次 ${stream.nextRetryMs / 1000}s`
                : "，自动重试已停止"}
              。
            </span>
            <button className="secondary" onClick={stream.retryNow}>
              重新连接
            </button>
          </div>
        )}
        {events.length ? (
          <ol className="timeline">
            {events.map((item) => (
              <li key={item.sequence}>
                <span className="timeline-dot" />
                <div>
                  <strong>{item.kind.replaceAll("_", " ")}</strong>
                  <p>
                    {item.node} · #{item.sequence}
                  </p>
                </div>
                <time dateTime={item.created_at}>
                  {dateLabel(
                    typeof item.payload.occurred_at === "string"
                      ? item.payload.occurred_at
                      : item.created_at,
                  )}
                </time>
              </li>
            ))}
          </ol>
        ) : (
          <Empty title="尚无持久化事件" />
        )}
      </section>
    </div>
  );
}
