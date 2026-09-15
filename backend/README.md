# BIM Intelligence Backend

后端使用 Python、FastAPI 和 Uvicorn，负责 IFC 文件管理、STEP 数据解析、模型缓存、知识资料检索、请求上下文组装和 DeepSeek/OpenAI 兼容接口调用。

## 技术栈

| 技术 | 用途 |
| --- | --- |
| Python 3.12 | 运行环境 |
| FastAPI | REST API 和 SSE |
| Uvicorn | ASGI 服务 |
| Pydantic | 请求数据校验 |
| OpenAI Python SDK | 模型接口 |
| pypdf | PDF 文本提取 |
| scikit-learn | TF-IDF 检索 |
| python-multipart | 文件上传 |

## 目录结构

```text
backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── schemas.py
│   ├── ifc_parser.py
│   ├── model_service.py
│   ├── knowledge.py
│   └── llm.py
├── documents/
│   └── README.md
├── storage/
│   ├── cache/
│   ├── logs/
│   └── models/
├── config.json
├── config.example.json
├── .env.example
├── requirements.txt
└── README.md
```

## 模块职责

### `main.py`

FastAPI 入口，提供：

- 健康检查
- 模型列表和详情
- 构件查询
- 模型上传
- 知识库索引
- 普通聊天
- SSE 流式聊天

### `config.py`

负责：

- 读取 `config.json`
- 读取 `.env`
- 管理模型路径、上传目录和缓存目录
- 管理前端 CORS 来源
- 限制上传大小和上下文长度

### `ifc_parser.py`

使用标准 IFC STEP 文本结构解析：

- IFC Schema
- 项目信息
- 楼层
- 构件
- 属性集
- 数量
- 材质关联
- 单位和标高

解析结果中的每个构件会包含：

```json
{
  "id": 45070,
  "global_id": "...",
  "ifc_type": "IFCBUILDINGELEMENTPROXY",
  "label": "代理构件",
  "category": "其他构件",
  "name": "...",
  "storey": "Level 2",
  "materials": [],
  "properties": {},
  "quantities": {}
}
```

### `model_service.py`

负责：

- 扫描 `MySource`
- 扫描上传目录
- 保存上传文件
- 生成模型 ID
- 解析结果缓存
- 获取模型详情
- 分页和筛选构件

### `knowledge.py`

负责：

- 扫描 PDF、TXT、Markdown
- 提取 PDF 页面文本
- 对资料分块
- 使用字符级 TF-IDF 建立索引
- 根据问题检索相关资料

索引会在应用启动后后台建立。

### `llm.py`

使用 OpenAI Python SDK 调用：

- `chat.completions.create`
- `stream=True`

客户端兼容 DeepSeek、OpenAI 和其他 OpenAI 兼容服务。

## 环境安装

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 配置

后端优先读取 `config.json`：

```json
{
  "api_key": "sk-your-api-key",
  "api_base": "https://api.deepseek.com",
  "model": "deepseek-flash",
  "temperature": 0.2
}
```

也支持：

```json
{
  "deepseek": {
    "api_key": "sk-your-api-key",
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-flash"
  }
}
```

可通过环境变量指定其他配置文件：

```powershell
$env:DEEPSEEK_CONFIG_FILE = "D:\path\to\config.json"
```

未在 JSON 中设置的字段会回退到 `.env`：

```ini
DEEPSEEK_API_KEY=
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
AI_TEMPERATURE=0.2
```

修改 `config.json` 后必须重启后端。

## 启动

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

开发模式：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

API 文档：http://127.0.0.1:8000/docs

## IFC 提交流程

```text
POST /api/models/upload
  |
  +--> 检查扩展名和大小
  +--> 保存到 storage/models
  +--> IFC 文件立即解析
  +--> 写入 storage/cache
  +--> 返回模型 ID 和统计信息
```

支持：

- `.ifc`
- `.rvt`

RVT 只保存，不进行几何或属性解析。

## 聊天请求

前端会提交：

```json
{
  "question": "当前模型有哪些楼层？",
  "history": [],
  "model_id": "sample-rac_basic_sample_project-ifc",
  "model_context": {
    "project": {},
    "statistics": {},
    "storeys": [],
    "element_counts": [],
    "units": {}
  }
}
```

后端处理顺序：

1. 根据 `model_id` 读取权威模型解析结果
2. 搜索与问题相关的构件
3. 清洗并合并前端 `model_context`
4. 检索本地知识资料
5. 组装系统提示词
6. 调用模型接口
7. 通过 SSE 返回文本片段

如果前端提交的模型详情超过 40,000 字符，后端只保留核心摘要。

## API 列表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务、模型和 AI 配置状态 |
| GET | `/api/models` | 获取模型列表 |
| GET | `/api/models/{id}` | 获取模型解析结果 |
| GET | `/api/models/{id}/elements` | 查询构件 |
| GET | `/api/models/{id}/content` | 获取原始文件 |
| POST | `/api/models/upload` | 提交 IFC/RVT |
| GET | `/api/knowledge/status` | 知识库状态 |
| POST | `/api/knowledge/reindex` | 重建知识库 |
| POST | `/api/chat` | 普通问答 |
| POST | `/api/chat/stream` | SSE 流式问答 |

## 知识库

索引目录：

- `backend/documents`
- `MySource`

支持格式：

- `.pdf`
- `.txt`
- `.md`

重建索引：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/reindex
```

## 检查

```powershell
.\.venv\Scripts\python.exe -m compileall -q app
```

检查健康接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## 常见问题

### `configured: false`

- 检查 `config.json` 中的 `api_key`
- 检查当前进程是否在修改配置前已经启动
- 修改后重启 Uvicorn

### `Connection error`

- 检查 `api_base`
- 检查运行后端的环境是否可以访问外网
- 检查代理、防火墙和 DNS
- 使用浏览器或 `curl` 测试接口域名

### 上传返回 413

文件超过 `MAX_UPLOAD_MB`，默认限制为 100 MB。

### 上传返回 415

只接受 `.ifc` 和 `.rvt`。

### JSON 配置解析失败

- 使用合法 JSON
- 字符串和字段名使用双引号
- 最后一个字段后不要保留逗号
- 后端支持 UTF-8 和 UTF-8 BOM
