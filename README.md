# q005 水产养殖服务

本项目是水产养殖管理后端，维护塘口、养殖批次、投苗、投喂、水质、用药、成本、销售与周期分析数据。业务数据保存在 SQLite 文件中，HTTP 接口由 FastAPI 提供。

## 测试命令

```bash
python3 -m unittest discover -s tests -v
```

## 编译与构建命令

```bash
python3 -m compileall -q backend/app
```

## 启动命令

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动后可访问 `/health` 检查服务状态。开发环境不得提交真实账号、连接凭据或生产数据。

## 品种身份链路

塘口、批次、投苗记录上的品种不再是互不关联的自由文本，而是一条可追溯的稳定身份链路：

- **稳定标识 + 版本化名称**：每个品种有全局稳定 `code`（如 `SP-0001`）和一串不可变名称版本，同一身份同时只有一个生效版本。列表、周期分析、追溯、跨场区比较都以该身份归并。
- **可审阅别名**：简称、旧名称、带空格/全角的写法经归一化（NFKC、去空白、忽略大小写）后作为别名管理，经人工提议/批准才生效；**系统不会自动合并真正不同的品系**，合并且只允许显式操作，并支持再拆分。
- **历史口径冻结**：批次与已发生投苗在创建时快照 `species_version_id`，之后品种文本冻结（接口返回 409）；名称更正只产生新版本、改变后续展示，不回写历史。
- **塘口轮养**：塘口更换品种追加 `pond_species_history` 记录，不覆盖历史。
- **已签署分析**：周期分析可签署为快照（`POST /api/analysis/cycle/{id}/sign/`），名称更正后用 `/api/analysis/cycle/signed/{id}/` 仍按签署时原口径重现。
- **未决队列与决策留痕**：无法识别的旧文本生成候选并入队；人工映射/驳回、合并、拆分、撤回都写入 `species_mapping_decisions`。映射可撤回，撤回后记录重回未决且回填被解除。别名裁定用乐观锁（`expected_lock_version`），并发下只有一个版本生效（冲突返回 409）。
- **旧库迁移**：服务启动时幂等地给旧表补身份列并扫描旧文本；精确命中目录的自动结案并回填，其余生成候选入未决队列。

主要接口（均在 `/api/species` 前缀下）：

| 用途 | 接口 |
| --- | --- |
| 录入提示同义写法 | `POST /resolve/` |
| 品种目录 | `POST/GET /identities/`、`GET /identities/{id}/versions/` |
| 名称更正（只改后续展示） | `POST /identities/{id}/correct-name/` |
| 别名提议/裁定 | `POST /identities/{id}/aliases/`、`POST /aliases/{id}/adjudicate/` |
| 显式合并/拆分 | `POST /merge/`、`POST /split/` |
| 旧文本扫描/未决队列 | `POST /legacy/scan/`、`GET /legacy/` |
| 映射裁定/撤回 | `POST /legacy/{id}/decide/`、`POST /legacy/{id}/withdraw/` |
| 决策依据流水 | `GET /decisions/` |

塘口、批次、投苗的列表与详情响应里新增 `species_identity` 视图，明确返回：

- `resolution_status`：`current`（当前标准名）/ `historical_name`（沿用历史名称）/ `pending`、`rejected`（尚未裁定）；
- `identity_code`、`current_standard_name`（当前标准名）、`historical_name`（历史名称）、`raw_name`（录入原文）。

周期分析与追溯使用同一身份；另提供 `GET /api/analysis/compare-by-species/` 按稳定身份跨塘口聚合，同义写法不再被拆成多个品种。

