import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Plus, RefreshCw, Search } from "lucide-react";
import type { TokenProvider } from "../../api/client";
import { listTasks } from "../../api/endpoints";
import type { TaskListItem, TaskStatus } from "../../api/types";
import {
  dateLabel,
  Empty,
  ErrorNotice,
  Loading,
  messageOf,
  Status,
  statusLabels,
} from "../../components/ui";

export function TaskHistory({
  token,
  revision,
  selected,
  onSelect,
  onCreate,
}: {
  token: TokenProvider;
  revision: number;
  selected: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<TaskListItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController>();
  const load = useCallback(
    async (next?: string) => {
      controller.current?.abort();
      const request = new AbortController();
      controller.current = request;
      setBusy(true);
      setError(null);
      try {
        const page = await listTasks(
          token,
          {
            status: (filter as TaskStatus) || undefined,
            cursor: next,
            limit: 12,
          },
          request.signal,
        );
        if (request.signal.aborted) return;
        setItems((old) =>
          next
            ? [
                ...old,
                ...page.items.filter(
                  (item) => !old.some((x) => x.task_id === item.task_id),
                ),
              ]
            : page.items,
        );
        setCursor(page.next_cursor);
        setTotal(page.total_known);
      } catch (reason) {
        if (!request.signal.aborted) setError(messageOf(reason));
      } finally {
        if (!request.signal.aborted) setBusy(false);
      }
    },
    [token, filter],
  );
  useEffect(() => {
    setItems([]);
    setCursor(null);
    setTotal(null);
    void load();
    return () => controller.current?.abort();
  }, [load, revision]);
  const visible = items.filter((item) =>
    `${item.title} ${item.question}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <section className="history-panel" aria-label="任务历史">
      <div className="section-heading">
        <div>
          <span className="eyebrow">YOUR RESEARCH</span>
          <h2>
            研究任务 <small>{total ?? "—"}</small>
          </h2>
        </div>
        <button
          className="icon-button"
          aria-label="刷新任务历史"
          onClick={() => void load()}
          disabled={busy}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      <button className="primary full" onClick={onCreate}>
        <Plus size={17} /> 新建研究
      </button>
      <label className="search-field">
        <Search size={15} />
        <input
          aria-label="搜索已加载任务"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索已加载任务"
        />
      </label>
      <label className="sr-only" htmlFor="status-filter">
        任务状态筛选
      </label>
      <select
        id="status-filter"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      >
        <option value="">全部状态</option>
        {Object.entries(statusLabels).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <ErrorNotice error={error} />
      <div className="task-list">
        {visible.map((item) => (
          <button
            key={item.task_id}
            className={`task-card ${selected === item.task_id ? "selected" : ""}`}
            aria-current={selected === item.task_id ? "page" : undefined}
            onClick={() => onSelect(item.task_id)}
          >
            <div className="task-card-meta">
              <Status value={item.status} />
              <span>{item.execution_mode === "demo" ? "DEMO" : "REAL"}</span>
            </div>
            <strong>{item.title}</strong>
            <p>{item.question}</p>
            <div className="task-card-footer">
              <time>{dateLabel(item.created_at)}</time>
              <ChevronRight size={14} />
            </div>
          </button>
        ))}
      </div>
      {busy && <Loading />}
      {!busy && !visible.length && (
        <Empty title={query ? "未找到匹配任务" : "暂无研究任务"}>
          创建一项研究，从明确问题开始。
        </Empty>
      )}
      {cursor && (
        <button
          className="secondary full"
          disabled={busy}
          onClick={() => void load(cursor)}
        >
          加载更多
        </button>
      )}
    </section>
  );
}
