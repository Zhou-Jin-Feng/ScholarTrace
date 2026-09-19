require("./register.cjs");
const { test } = require("node:test");
const assert = require("node:assert/strict");
const React = require("react");
const { create, act } = require("react-test-renderer");
const { PlanPanel } = require("../src/features/plans/PlanPanel.tsx");
const {
  BudgetSummary,
  mergeTimeline,
} = require("../src/features/tasks/RunOverview.tsx");
const { Markdown } = require("../src/features/reports/ReportPanel.tsx");
const { useResource } = require("../src/hooks/useResource.ts");
const token = () => null;
const task = { task_id: "task:test", status: "waiting_approval" };
const plan = {
  schema_version: "1.0",
  task_id: task.task_id,
  plan_version: 1,
  plan_digest: "a".repeat(64),
  generated_by: "fixture",
  approval_state: "waiting_approval",
  sub_questions: ["原子问题"],
  source_scope: {
    providers: ["arxiv"],
    year_from: 2020,
    year_to: 2026,
    min_papers: 3,
    max_papers: 5,
  },
  exclusions: [],
  budget_plan: {
    max_cny: 0,
    max_api_calls: 0,
    max_wall_clock_seconds: 60,
    estimate_source: "fixture",
    is_actual_bill: false,
  },
};
const text = (tree) => JSON.stringify(tree.toJSON());
const words = (c) =>
  typeof c === "string"
    ? c
    : Array.isArray(c)
      ? c.map(words).join("")
      : c?.props
        ? words(c.props.children)
        : "";
const button = (tree, label) =>
  tree.root
    .findAllByType("button")
    .find((b) => words(b.props.children).includes(label));
const response = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

test("two explicit gates: estimate before ack/generate, no automatic approval", async () => {
  let current = null,
    calls = [],
    renderer;
  const old = global.fetch;
  global.fetch = async (url, init = {}) => {
    calls.push([url, init]);
    if (url.endsWith("/estimate"))
      return response({
        estimated_cny: 0,
        estimate_source: "fixture",
        is_actual_bill: false,
      });
    if (url.endsWith("/acknowledge-cost")) return response({});
    if (url.endsWith("/generate")) {
      current = plan;
      return response(plan);
    }
    if (url.endsWith("/plan"))
      return current
        ? response(current)
        : response(
            { detail: { code: "plan_not_found", detail: "not generated" } },
            404,
          );
    throw Error(url);
  };
  try {
    await act(async () => {
      renderer = create(
        React.createElement(PlanPanel, { token, task, onChange: () => {} }),
      );
    });
    assert.equal(calls.length, 1);
    await act(async () => button(renderer, "查看规划费用估算").props.onClick());
    assert.equal(button(renderer, "确认并生成计划").props.disabled, true);
    await act(async () =>
      renderer.root
        .findByType("input")
        .props.onChange({ target: { checked: true } }),
    );
    await act(async () => button(renderer, "确认并生成计划").props.onClick());
    assert.ok(calls.find(([u]) => u.endsWith("/acknowledge-cost")));
    assert.ok(text(renderer).includes("Fixture · 演示计划"));
    assert.equal(calls.filter(([u]) => u.endsWith("/approve")).length, 0);
  } finally {
    if (renderer) act(() => renderer.unmount());
    global.fetch = old;
  }
});

test("modify saves a pending revision with bound identity; stale approval rereads without resubmitting", async () => {
  let current = plan,
    writes = [],
    renderer;
  const old = global.fetch;
  global.fetch = async (url, init = {}) => {
    if (url.endsWith("/plan")) return response(current);
    if (url.endsWith("/approve")) {
      const body = JSON.parse(init.body);
      writes.push(body);
      if (body.action === "modify") {
        current = {
          ...plan,
          plan_version: 2,
          plan_digest: "b".repeat(64),
          sub_questions: body.modified_plan.sub_questions,
        };
        return response(task);
      }
      current = { ...current, plan_version: 3, plan_digest: "c".repeat(64) };
      return response(
        { detail: { code: "plan_version_stale", detail: "stale" } },
        409,
      );
    }
    throw Error(url);
  };
  try {
    await act(async () => {
      renderer = create(
        React.createElement(PlanPanel, { token, task, onChange: () => {} }),
      );
    });
    await act(async () => button(renderer, "修改计划").props.onClick());
    await act(async () =>
      renderer.root
        .findByProps({ id: "sub-questions" })
        .props.onChange({ target: { value: "修改后的问题" } }),
    );
    await act(async () =>
      renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }),
    );
    assert.equal(writes.length, 1);
    assert.equal(writes[0].action, "modify");
    assert.equal(writes[0].plan_digest, plan.plan_digest);
    assert.deepEqual(writes[0].modified_plan.sub_questions, ["修改后的问题"]);
    assert.ok(text(renderer).includes("尚未执行"));
    await act(async () => button(renderer, "批准此版本并执行").props.onClick());
    assert.equal(writes.length, 2);
    assert.equal(writes[1].plan_version, 2);
    assert.ok(text(renderer).includes("未自动批准"));
    assert.ok(text(renderer).includes("cccccccc"));
  } finally {
    if (renderer) act(() => renderer.unmount());
    global.fetch = old;
  }
});

test("budget keeps null and unknown distinct from zero and actual bills", () => {
  let renderer;
  act(() => {
    renderer = create(
      React.createElement(BudgetSummary, { budget: { reservation: null } }),
    );
  });
  assert.ok(text(renderer).includes("未预留"));
  assert.ok(text(renderer).includes("未结算"));
  act(() =>
    renderer.update(
      React.createElement(BudgetSummary, {
        budget: {
          reservation: {
            reserved: { cny: 10 },
            recorded_usage: { cny: 0 },
            settled: null,
            reconciliation_required: true,
          },
        },
      }),
    ),
  );
  assert.ok(text(renderer).includes("外部结果未知"));
  assert.ok(text(renderer).includes("¥0.00"));
  assert.ok(text(renderer).includes("未结算"));
  act(() => renderer.unmount());
});

test("budget displays authoritative journal cost while its ledger projection is pending", () => {
  let renderer;
  const budget = {
    reservation: {
      reserved: { cny: 10 }, recorded_usage: { cny: 0 },
      settled: null, reconciliation_required: false,
    },
    measured_usage: null,
    projection_state: "pending",
    journal_accounting: { known_cny: 0.1234, uncertain_effects: 0 },
  };
  act(() => { renderer = create(React.createElement(BudgetSummary, { budget })); });
  assert.ok(text(renderer).includes("¥0.1234"));
  assert.ok(text(renderer).includes("用量待同步"));
  assert.ok(text(renderer).includes("未结算"));
  act(() => renderer.update(React.createElement(BudgetSummary, {
    budget: { ...budget, projection_state: "reconciliation_required",
      journal_accounting: { known_cny: 0.1234, uncertain_effects: 1 } },
  })));
  assert.ok(text(renderer).includes("外部结果未知"));
  assert.ok(text(renderer).includes("¥0.1234"));
  act(() => renderer.unmount());
});

test("report treats injected HTML and remote images as inert text", () => {
  let renderer;
  act(() => {
    renderer = create(
      React.createElement(Markdown, {
        text: '# 中文报告\n<img src="https://remote.invalid/x" onerror="alert(1)">\n<script>bad()</script>\n![x](https://remote.invalid/paper)',
      }),
    );
  });
  assert.equal(renderer.root.findAllByType("img").length, 0);
  assert.equal(renderer.root.findAllByType("script").length, 0);
  assert.ok(text(renderer).includes("onerror"));
  act(() => renderer.unmount());
});

test("resource task switch aborts and ignores the late old result", async () => {
  let a, b, result, renderer, oldSignal;
  const first = (signal) => {
    oldSignal = signal;
    return new Promise((r) => (a = r));
  };
  const second = () => new Promise((r) => (b = r));
  function Harness({ load }) {
    result = useResource(load);
    return null;
  }
  await act(async () => {
    renderer = create(React.createElement(Harness, { load: first }));
  });
  await act(async () =>
    renderer.update(React.createElement(Harness, { load: second })),
  );
  assert.equal(oldSignal.aborted, true);
  await act(async () => {
    b("new-task");
  });
  await act(async () => {
    a("old-task");
  });
  assert.equal(result.data, "new-task");
  act(() => renderer.unmount());
});

test("persisted/live timeline is ordered and deduplicated without fabricated completion", () => {
  const rows = mergeTimeline(
    [
      { sequence: 2, kind: "search_completed" },
      { sequence: 1, kind: "task_created" },
    ],
    [
      { sequence: 2, kind: "search_completed" },
      { sequence: 3, kind: "task_interrupted" },
    ],
  );
  assert.deepEqual(
    rows.map((r) => r.sequence),
    [1, 2, 3],
  );
  assert.equal(rows.at(-1).kind, "task_interrupted");
});

test("report offers only included claims and evidence selection resolves actual source", async () => {
  const { ReportPanel } = require("../src/features/reports/ReportPanel.tsx");
  const {
    EvidencePanel,
  } = require("../src/features/evidence/EvidencePanel.tsx");
  const old = global.fetch;
  let renderer,
    chosen,
    paths = [];
  const included = {
    claim_id: "claim:yes",
    text: "中文可追溯论断",
    included_in_report: true,
    sub_question: null,
    evidence_ids: ["evidence:doc/a"],
    verification: {
      status: "partially_supported",
      verifier_kind: "fixture",
      marker: "[PARTIAL]",
      validated: true,
    },
  };
  global.fetch = async (url) => {
    paths.push(url);
    if (url.includes("/report?")) return new Response("# 中文报告");
    if (url.endsWith("/claims"))
      return response({
        claims: [
          included,
          {
            ...included,
            claim_id: "claim:no",
            text: "排除论断",
            included_in_report: false,
          },
        ],
        excluded_count: 1,
      });
    if (url.includes("/evidence/"))
      return response({
        paper: { title: "来源标题", source: "arxiv", version: null },
        evidence_level: "fulltext",
        excerpt: "真正的原文",
        excerpt_is_verbatim: true,
        inference_note: null,
        binding: null,
        chunk: {
          page_number: null,
          char_start: null,
          char_end: null,
          chunk_id: null,
          content_sha256: null,
        },
      });
    throw Error(url);
  };
  try {
    await act(async () => {
      renderer = create(
        React.createElement(ReportPanel, {
          taskId: "task:test",
          token,
          onClaim: (id) => (chosen = id),
        }),
      );
    });
    assert.ok(button(renderer, "中文可追溯论断"));
    assert.equal(button(renderer, "排除论断"), undefined);
    await act(async () => button(renderer, "中文可追溯论断").props.onClick());
    assert.equal(chosen, "claim:yes");
    await act(async () =>
      renderer.update(
        React.createElement(EvidencePanel, {
          taskId: "task:test",
          token,
          initialClaim: chosen,
        }),
      ),
    );
    assert.ok(
      paths.some((path) => path.endsWith("/evidence/evidence%3Adoc%2Fa")),
    );
    assert.ok(text(renderer).includes("真正的原文"));
    assert.ok(text(renderer).includes("未提供"));
    assert.ok(text(renderer).includes("已排除"));
  } finally {
    if (renderer) act(() => renderer.unmount());
    global.fetch = old;
  }
});

test("report fetch uses bearer header and does not place credentials in URL", async () => {
  const { fetchReport } = require("../src/api/endpoints.ts");
  const old = global.fetch;
  let call;
  global.fetch = async (url, init) => {
    call = { url, init };
    return new Response("%PDF-test");
  };
  try {
    const blob = await fetchReport(
      () => "synthetic-auth-token",
      "task:test",
      "pdf",
    );
    assert.equal(await blob.text(), "%PDF-test");
    assert.equal(
      call.init.headers.Authorization,
      "Bearer synthetic-auth-token",
    );
    assert.equal(call.url.includes("synthetic-auth-token"), false);
    assert.equal(call.url.includes("access_token"), false);
  } finally {
    global.fetch = old;
  }
});

for (const executionMode of ["demo", "real"]) {
test(`workspace cancellation and bounded resume: ${executionMode}`, async () => {
  const { TaskWorkspace } = require("../src/features/tasks/TaskWorkspace.tsx");
  const oldFetch = global.fetch,
    oldWindow = global.window,
    oldSource = global.EventSource;
  global.window = global;
  global.EventSource = class {
    addEventListener() {}
    close() {}
  };
  let status = "queued",
    calls = [],
    renderer;
  const summary = () => ({
    ...task,
    status,
    phase: "search",
    title: "合成恢复任务",
    question: "合成问题",
    execution_mode: executionMode,
    degradations: [],
    metrics: {},
  });
  global.fetch = async (url, init = {}) => {
    calls.push([url, init]);
    if (url.endsWith("/budget")) return response({ reservation: null });
    if (url.endsWith("/timeline")) return response({ events: [] });
    if (url.endsWith("/plan"))
      return response({ ...plan, approval_state: "approved" });
    if (url.endsWith("/cancel")) {
      status = "cancelling";
      return response(summary());
    }
    if (url.endsWith("/resume"))
      return response({ detail: "resume attempt limit reached" }, 409);
    return response(summary());
  };
  try {
    await act(async () => {
      renderer = create(
        React.createElement(TaskWorkspace, {
          token,
          taskId: "task:test",
          onChange() {},
        }),
      );
    });
    await act(async () => button(renderer, "取消任务").props.onClick());
    assert.equal(calls.filter(([url]) => url.endsWith("/cancel")).length, 1);
    assert.equal(button(renderer, "取消中…").props.disabled, true);
    act(() => renderer.unmount());
    status = "interrupted";
    await act(async () => {
      renderer = create(
        React.createElement(TaskWorkspace, {
          token,
          taskId: "task:test",
          onChange() {},
        }),
      );
    });
    await act(async () => button(renderer, "查看恢复条件").props.onClick());
    assert.equal(button(renderer, "按原批准计划尝试恢复").props.disabled, false);
    await act(async () =>
      button(renderer, "按原批准计划尝试恢复").props.onClick(),
    );
    const resumes = calls.filter(([url]) => url.endsWith("/resume"));
    assert.equal(resumes.length, 1);
    assert.equal(JSON.parse(resumes[0][1].body).plan_digest, plan.plan_digest);
    assert.ok(text(renderer).includes("resume attempt limit reached"));
    assert.equal(status, "interrupted");
  } finally {
    if (renderer) act(() => renderer.unmount());
    global.fetch = oldFetch;
    global.window = oldWindow;
    global.EventSource = oldSource;
  }
});


}

test("pending old plan mutation cannot refresh the next task or dispatch twice", async () => {
  const oldFetch = global.fetch;
  let finish, tree, changed = 0;
  const calls = [];
  global.fetch = async (url, init = {}) => {
    calls.push(url);
    if (url.endsWith('/approve')) return new Promise(resolve => { finish = resolve; });
    return response({ ...plan, task_id: url.includes('task:next') ? 'task:next' : task.task_id });
  };
  try {
    await act(async () => { tree = create(React.createElement(PlanPanel, {
      token, task, onChange: () => changed++,
    })); });
    act(() => {
      const approve = button(tree, '批准此版本并执行').props.onClick;
      approve(); approve();
    });
    assert.equal(calls.filter(u => u.endsWith('/approve')).length, 1);
    await act(async () => { tree.update(React.createElement(PlanPanel, {
      token, task: { ...task, task_id: 'task:next' }, onChange: () => changed++,
    })); });
    const before = calls.length;
    await act(async () => { finish(response({ status: 'queued' })); });
    assert.equal(changed, 0);
    assert.equal(calls.length, before);
    assert.equal(button(tree, '批准此版本并执行').props.disabled, false);
  } finally {
    if (tree) act(() => tree.unmount());
    global.fetch = oldFetch;
  }
});
