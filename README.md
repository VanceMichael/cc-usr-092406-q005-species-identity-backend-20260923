# q005 水产养殖服务

本项目是水产养殖管理后端，维护塘口、养殖批次、投苗、投喂、水质、用药、成本、销售与周期分析数据。业务数据保存在 SQLite 文件中，HTTP 接口由 FastAPI 提供。

## 测试命令

```bash
pip install -r backend/requirements-dev.txt   # 含 TestClient 所需 httpx
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

同一种养殖品种可能被录成简称、旧称或带空格的写法。为避免塘口、批次、投苗各存一份互不关联的
文本，系统在文本之上维护一条稳定的品种身份链路：

- **规范品种（Species）**：终身不变的稳定标识 `code` + 当前标准名 `current_name`。
- **名称版本（SpeciesName）**：标准名/别名均有 proposed → active → superseded/rejected 生命周期。
  归一化（去空白、统一标点、小写）后的同一写法全局只允许有一个 active 版本，
  由部分唯一索引保证并发裁定只有一个生效。
- **候选映射与未决队列（SpeciesMapping）**：录不出生效名称的文本一律进入 pending 队列，
  模糊相似仅作为候选提示，**绝不自动合并**；裁定方式包括批准合并、驳回、撤回，全部留有
  审计事件（SpeciesEvent，含操作人与依据）。
- **业务快照**：`ponds/batches/stocking_records` 各自冻结创建时的 `species_code` 与原文。
  塘口允许轮养（PUT 更换品种即开新一轮）；批次与已发生投苗的品种版本不可被 PUT 改写。
- **名称更正**：标准名更名只改变后续展示规则，旧名保留为历史名/别名；周期分析可签署，
  签署后按签署时口径重现，不受后续更名、合并、拆分影响。

### 典型流程

```text
POST /api/species/                      # 建规范品种（code + 标准名）
GET  /api/species/suggest/?text=罗氏虾   # 录入前同义写法提示（只提示、不落库）
# 录入塘口/批次/投苗时自动解析：空白/标点/大小写变体自动采信；其余进未决队列
POST /api/species/scan/                 # 扫描旧文本，生成候选映射与未决队列并回填确定项
GET  /api/species/mappings/?status=pending
POST /api/species/mappings/{id}/approve # 人工裁定合并（仅裁定该文本，回填并留痕）
POST /api/species/mappings/{id}/reject  # 驳回（文本保持未裁定，再次出现可重新入队）
POST /api/species/mappings/{id}/withdraw# 撤回已批准合并（别名下线，历史快照保留）
POST /api/species/{id}/split            # 被误并的品系拆分为独立品种（按原文重新归属）
POST /api/species/{src}/merge/{dst}     # 显式整体合并两个已建档品种（人工确认同物种）
POST /api/species/{id}/rename           # 标准名更正（只改后续展示）
GET  /api/species/overview/             # 跨塘口按同一身份聚合，未裁定文本单列
POST /api/analysis/cycle/{id}/sign      # 签署周期分析
GET  /api/analysis/cycle/{id}/signed    # 按签署时原口径重现
```

塘口、批次、投苗、周期分析、追溯响应均携带统一的 `species_identity`：
当前标准名（current_name）、有效别名（aliases）、历史名称（historical_names）
以及未裁定状态（pending / mapping_id / candidates），同时保留 `recorded_species_name`
便于审阅当时原文。
