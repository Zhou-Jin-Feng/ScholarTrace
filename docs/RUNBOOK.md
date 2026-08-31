# ScholarTrace M6 运行手册

## 1. 本地启动

环境要求：Python 3.11、uv、Node.js 20+ 和 npm。M6 的确定性演示不需要 API Key、DocuMind、ScholarGraph 或 Ollama。

启动 API：

```powershell
uv sync --all-groups
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000
```

默认任务数据写入 `artifacts/m6-delivery`。部署到 Compose 时由 `SCHOLARTRACE_DATA_DIR` 指向持久卷；本地也可在启动前设置该变量以切换目录。

另开终端启动前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。Vite 会把 `/api` 请求代理到 `127.0.0.1:8000`。

## 2. Compose 启动

```powershell
docker compose up --build
```

前端地址为 `http://127.0.0.1:5173`，API 健康检查为 `http://127.0.0.1:8000/api/v1/health/live`。Compose 的数据卷只保存任务元数据、事件和导出工件；不会下载论文或保存模型原始回答。

停止：

```powershell
docker compose down
```

不要使用 `docker compose down -v`，除非明确要删除本地任务数据卷。

## 3. API 最小流程

创建任务：

```powershell
$task = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/research/tasks `
  -Headers @{"Idempotency-Key" = "manual-m6-demo-1"} `
  -ContentType "application/json" `
  -Body '{"question":"How can retrieval evidence improve RAG reports?","demo_mode":"success"}'

$task.task_id
```

审批并运行确定性演示：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri ("http://127.0.0.1:8000/api/v1/research/tasks/{0}/approve" -f $task.task_id) `
  -ContentType "application/json" `
  -Body '{"action":"approve","reason":"manual demo approval"}'
```

报告格式：`markdown`、`html`、`pdf`、`json`。事件使用 `Last-Event-ID` 支持已持久事件补发。

## 4. M6 评测与 Demo

```powershell
uv run python scripts/run_m6_evaluation.py
uv run python scripts/run_m6_demo.py
uv run python scripts/run_m6_scholargraph_capacity.py --repeat-count 3
```

评测和 Demo 只写脱敏 JSON 到 `evaluation/reports/`；临时 SQLite 位于已忽略的 `artifacts/`。容量命令要求冻结 ScholarGraph `1.2.0` 已在 `127.0.0.1:8002` ready，按并发 1 顺序执行三次 Basic，不访问付费模型，不保存问题或答案。矩阵中的 `limited` 或 `missing` 表示质量证据缺口；B3/B4 的 `measured` 表示已完成人工盲审，不表示 B4 获得收益。

## 5. 故障排查

| 现象 | 检查 | 处理 |
|---|---|---|
| API 端口不可达 | `GET /api/v1/health/live` | 检查 uvicorn/Compose 日志和端口占用 |
| 前端显示 API 错误 | 浏览器 Network、API health | 先启动 API，再启动 Vite；确认代理端口 8000 |
| 任务重复创建 | 是否复用了不同请求的 Idempotency-Key | 同一 Key 只能对应同一请求内容 |
| 报告 404 | 任务是否已审批并产生终态 | 先调用 approve；仅 waiting_approval 没有导出 |
| ScholarGraph 503 | readiness、版本和服务日志 | 上层回退 B3；不要在 Consumer 内无限重试昂贵 query |
| Ollama 冷启动 503 | DocuMind `health/ready` 的 retrieval 组件 | 预热 `qwen3-embedding`，再进行有界检索 |

## 6. 数据和安全

- 不把 `.env`、API Key、论文全文、原始模型回答、Prompt 或内部路径放入 Git；
- 导出报告只包含任务元数据、事件、工件哈希和安全限制说明；
- 生产 `api-strong` 仍需单独配置、价格快照和人工批准；
- `production_unavailable` 演示模式会显式降级，不会静默调用本地模型。
