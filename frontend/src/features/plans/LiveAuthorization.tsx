import { useCallback, useEffect, useRef, useState } from "react";
import type { TokenProvider } from "../../api/client";
import { approvePlan, estimatePlan, generatePlan } from "../../api/endpoints";
import type { PhaseLimits, PlanningAuthorization, ResearchPlanView } from "../../api/types";
import { ErrorNotice, Loading, messageOf } from "../../components/ui";
import { useResource } from "../../hooks/useResource";

const emptyLimits = (): PhaseLimits => ({
  max_cny: "0", max_remote_calls: 0, max_local_calls: 0, max_external_requests: 0,
});

function Limits({ value, onChange, label, planning = false }: {
  value: PhaseLimits; onChange: (value: PhaseLimits) => void; label: string; planning?: boolean;
}) {
  return <fieldset><legend>{label}</legend>
    <label>金额上限 / 元<input required type="number" min="0" step="0.000001"
      value={value.max_cny} onChange={e => onChange({ ...value, max_cny: e.target.value })} /></label>
    {(["max_remote_calls", "max_local_calls", "max_external_requests"] as const)
      .filter(key => !planning || key === "max_remote_calls").map(key => <label key={key}>
        {{ max_remote_calls: "远程模型次数", max_local_calls: "本地模型次数",
          max_external_requests: "外部请求次数" }[key]}
        <input required type="number" min="0" step="1" value={value[key]}
          onChange={e => onChange({ ...value, [key]: Number(e.target.value) })} />
      </label>)}
  </fieldset>;
}

export function LiveAuthorization({ token, taskId, plan, busy, run }: {
  token: TokenProvider; taskId: string; plan?: ResearchPlanView; busy: boolean;
  run: (work: () => Promise<unknown>) => void;
}) {
  const load = useCallback((signal: AbortSignal) => estimatePlan(token, taskId, signal), [token, taskId]);
  const { data, error, loading } = useResource(load, 0);
  const [limits, setLimits] = useState(emptyLimits);
  const [planning, setPlanning] = useState(emptyLimits);
  const [seconds, setSeconds] = useState(900);
  const [ack, setAck] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const submitted = useRef<PlanningAuthorization>();
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    if (!plan && data?.planning_authorization) {
      const saved = data.planning_authorization;
      submitted.current = saved;
      setLimits(saved);
      setPlanning(saved.planning);
      setSeconds(saved.max_wall_clock_seconds);
      setLocked(true);
      setAck(false);
    }
  }, [data, plan]);
  if (loading) return <Loading />;
  if (error) return <ErrorNotice error={error} />;
  if (!data?.production_composed || !data.runtime_policy || !data.policy_sha256)
    return <p role="status">生产配置未就绪，无法授权真实调用。</p>;
  const policy = data.runtime_policy;
  const original = data.planning_authorization;
  const incompatible = !!original && original.policy_sha256 !== data.policy_sha256;
  const missingOriginal = !plan && data.has_task_authorization && !original;
  const expired = !!original && Date.parse(original.deadline_at) <= Date.now();
  function changed(work: () => void) { work(); setAck(false); }
  return <form className="cost-gate" onSubmit={event => {
    event.preventDefault();
    if (!ack || busy || incompatible || missingOriginal || expired) return;
    setLocalError(null);
    try {
      for (const value of [limits, ...(!plan ? [planning] : [])]) {
        if (!/^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$/.test(value.max_cny)
          || ![value.max_remote_calls, value.max_local_calls, value.max_external_requests]
            .every(v => Number.isSafeInteger(v) && v >= 0)) throw new Error("请填写有效的非负额度。");
      }
      if (plan) {
        if (Number(limits.max_cny) > plan.budget_plan.max_cny
          || limits.max_remote_calls > plan.budget_plan.max_api_calls)
          throw new Error("执行额度不能高于已审计划。");
        run(() => approvePlan(token, taskId, { action: "approve", plan_version: plan.plan_version,
          plan_digest: plan.plan_digest, execution_authorization: {
            ...limits, policy_sha256: data!.policy_sha256!, generation: 1,
          } }));
      } else {
        if (!Number.isInteger(seconds) || seconds < 1 || seconds > 86400
          || Number(planning.max_cny) > Number(limits.max_cny)
          || planning.max_remote_calls > limits.max_remote_calls)
          throw new Error("规划额度和时长必须位于任务总授权范围内。");
        const authorization = submitted.current ?? {
          ...limits, policy_sha256: data!.policy_sha256!, generation: 1, planning,
          max_wall_clock_seconds: seconds,
          deadline_at: new Date(Date.now() + seconds * 1000).toISOString(),
        };
        submitted.current = authorization;
        setLocked(true);
        run(() => generatePlan(token, taskId, Number(authorization.planning.max_cny),
          undefined, authorization));
      }
    } catch (reason) { setLocalError(messageOf(reason)); }
  }}>
    <h3>{plan ? "确认执行额度与数据范围" : "确认任务总额度与规划授权"}</h3>
    <p>远程模型：{policy.remote.model} · {policy.remote.model_version}</p>
    <p>接收地址：{policy.remote.endpoint}</p>
    <p>每百万输入/输出 token 价格：¥{policy.remote.input_cny_per_million} / ¥{policy.remote.output_cny_per_million}（非账单）</p>
    <p>本地分析：{policy.local?.model} · {policy.local?.endpoint}</p>
    <p>文档服务：{policy.documind_url}</p>
    <p>数据范围：{policy.data_fields.join("、")}</p>
    <p>检索来源：{policy.allowed_search_providers.join("、")}；最多{policy.max_papers}篇。</p>
    <fieldset disabled={busy || locked}>
      <Limits value={limits} onChange={v => changed(() => setLimits(v))}
        label={plan ? "本次执行阶段上限" : "整个任务累计上限（包含规划）"} />
      {!plan && <><Limits value={planning} planning onChange={v => changed(() => setPlanning(v))}
        label="规划阶段上限" />
        <label>任务有效时长 / 秒<input required type="number" min="1" max="86400" value={seconds}
          onChange={e => changed(() => setSeconds(Number(e.target.value)))} /></label></>}
    </fieldset>
    {locked && <p>本次授权已固定；重试沿用原截止时间和额度，不续期、不补充额度。</p>}
    {original && <p>原任务截止时间：{original.deadline_at}；累计金额上限：¥{original.max_cny}。</p>}
    {(incompatible || missingOriginal || expired) && <p role="alert">
      原授权已过期、配置已变化或缺少可恢复记录。不能创建替代授权，请核对任务状态。
    </p>}
    <label><input type="checkbox" checked={ack} disabled={busy}
      onChange={e => setAck(e.target.checked)} />我已审阅以上服务、数据范围和额度，授权本阶段调用。</label>
    <ErrorNotice error={localError} />
    <button className="primary" disabled={busy || !ack || incompatible || missingOriginal || expired}>
      {plan ? "授权并执行此版本" : "授权并生成计划"}
    </button>
  </form>;
}
