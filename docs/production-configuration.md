# 显式生产配置

生产入口组装检索、DocuMind、本地分析和远程规划/核验/合成适配器。启动服务不授予任务调用权限；每个真实任务仍须确认规划授权，再审阅具体计划并确认执行授权。费用使用配置单价估算，不是提供方账单。

## 启动

创建私有 JSON 配置文件，设置以下环境变量后，使用单进程启动：

```text
SCHOLARTRACE_RUNTIME_CONFIG=<配置文件的绝对路径>
SCHOLARTRACE_MODEL_API_KEY=<由进程环境注入的模型密钥>
```

```shell
uvicorn scholartrace.api.production:create_production_app --factory --host 127.0.0.1 --port 8001 --workers 1
```

服务只读取指定文件，不自动加载 `.env`。相对 `data_dir` 以配置文件所在目录为基准；建议使用专用数据目录。不要让两个进程共享此目录。原 `scholartrace.api.app:app` 入口继续用于默认 Demo 装配。

以下是配置结构示例，域名、模型、价格和日期均为占位值，必须替换成实际核对的设置，不能直接用于真实研究。密钥不属于 JSON 配置。

```json
{
  "data_dir": "runtime-data",
  "year_from": 2020,
  "retrieval_cutoff": "2026-09-18",
  "plan_limits": {
    "max_fulltext_papers": 3,
    "max_cost_cny": 10,
    "max_api_calls": 16,
    "max_duration_seconds": 900
  },
  "policy": {
    "remote": {
      "endpoint": "https://provider.example/v1/chat/completions",
      "protocol": "chat_completions",
      "model": "replace-with-model-id",
      "model_version": "replace-with-version",
      "input_cny_per_million": "1",
      "output_cny_per_million": "2",
      "price_observed_at": "2026-09-18T00:00:00Z",
      "max_input_tokens": 50000,
      "max_output_tokens": 4096
    },
    "local": {
      "endpoint": "http://127.0.0.1:11434/api/chat",
      "protocol": "ollama",
      "model": "replace-with-local-model",
      "model_version": "replace-with-local-version",
      "input_cny_per_million": "0",
      "output_cny_per_million": "0",
      "price_observed_at": "2026-09-18T00:00:00Z",
      "max_input_tokens": 50000,
      "max_output_tokens": 4096
    },
    "documind_url": "http://127.0.0.1:8000",
    "documind_version": "3.0.0",
    "allowed_search_providers": ["arxiv"],
    "max_papers": 3,
    "max_rounds": 1,
    "data_fields": [
      "question", "paper_metadata", "selected_pdf", "retrieved_chunks",
      "claims", "evidence_quotes", "verification_results"
    ]
  }
}
```

`policy` 声明部署能够处理的服务和数据范围，任务授权绑定其内容指纹。规划时的任务总额度必须覆盖 `plan_limits`；规划与执行还各有阶段额度、调用次数和截止时间，重新读取或重试不会补充额度。数据范围、模型或价格变化需要重新审阅，不能沿用原任务授权。

`loopback` 是默认访问模式。私有网络部署需同时设置 `SCHOLARTRACE_DEPLOYMENT_MODE=trusted_private` 和独立的 `SCHOLARTRACE_AUTH_TOKEN`；此令牌是任务 API 的访问凭据，与模型密钥不同。具体边界见现有部署和安全文档。

检索在返回元数据上再次检查日期。完整日期按包含截止日处理，只有年份时仅接受整年均在截止日前的记录；arXiv 后续版本还需可证明的版本日期。来源记录保留日期筛除数量。缺少足够可访问全文时任务不会用范围外文献凑数。

当前启动与授权拒绝测试证明配置入口可以组装且不会自行派发请求；完整浏览器生产流程与真实研究质量须按相应验证记录判断，不能由启动成功推断。
