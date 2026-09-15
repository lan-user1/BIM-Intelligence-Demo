# BIM Intelligence

基于 React、Three.js、web-ifc 和 FastAPI 的 BIM 模型解析、三维查看与 AI 问答系统。用户可以提交 IFC 模型，在浏览器中查看构件几何和数据，并通过 DeepSeek 或其他 OpenAI 兼容接口，对当前模型和本地 BIM 资料进行问答。

## 主要功能

- 提交、保存和解析 IFC 模型
- 在浏览器中使用 WebGL2 三维查看 IFC 构件
- 读取项目、楼层、构件、材质、属性和工程量
- 按名称、GlobalId、IFC 类型和楼层筛选构件
- 提供 IFC 材质、构件着色和统一灰色显示模式
- 支持结构边线、视图适配、俯视图和鼠标交互
- 检索本地 PDF、Markdown、TXT BIM 资料
- 将当前模型详情通过 `model_context` 随问答请求发送
- 使用 DeepSeek/OpenAI 兼容接口进行流式问答
- 使用 Markdown 渲染 AI 回答，支持标题、列表、表格和代码块

## 技术栈

### 前端

| 技术 | 用途 |
| --- | --- |
| React 19 | 页面和状态管理 |
| Vite 8 | 开发服务器和生产构建 |
| Three.js | WebGL2 三维场景、材质、相机和图层 |
| web-ifc | 浏览器端 IFC 几何解析 |
| react-markdown | AI 回答 Markdown 渲染 |
| remark-gfm | Markdown 表格、任务列表等扩展 |
| lucide-react | 界面图标 |
| Playwright | 浏览器冒烟测试 |

### 后端

| 技术 | 用途 |
| --- | --- |
| Python 3.12 | 后端运行环境 |
| FastAPI | REST API、模型上传和 SSE 流式接口 |
| Uvicorn | ASGI 服务 |
| Pydantic | 请求参数校验 |
| OpenAI Python SDK | DeepSeek 和 OpenAI 兼容接口 |
| pypdf | PDF 知识资料读取 |
| scikit-learn | 本地 TF-IDF 知识检索 |
| IFC STEP 解析器 | 提取 BIM 构件和属性 |

## 系统结构

```text
浏览器
├── React 页面
├── BimViewer
│   ├── web-ifc 解析 IFC 几何
│   ├── Three.js 构建 Mesh
│   └── WebGL2 / GPU 渲染
└── 模型助手
    └── 携带 model_id 和 model_context

FastAPI
├── 模型上传与 IFC 解析
├── 模型数据缓存
├── PDF/Markdown/TXT 知识检索
└── DeepSeek/OpenAI 兼容调用
```

## 目录结构

```text
BIMProject/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── ifc_parser.py
│   │   ├── model_service.py
│   │   ├── knowledge.py
│   │   ├── llm.py
│   │   └── schemas.py
│   ├── documents/
│   ├── storage/
│   ├── config.json
│   ├── config.example.json
│   ├── requirements.txt
│   └── README.md
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── BimViewer.jsx
│   │   │   ├── ChatPanel.jsx
│   │   │   ├── ElementTable.jsx
│   │   │   └── ModelOverview.jsx
│   │   ├── api.js
│   │   ├── App.jsx
│   │   └── App.css
│   ├── public/web-ifc.wasm
│   ├── scripts/
│   ├── package.json
│   └── README.md
├── MySource/
└── Readme.md
```

## 快速启动

### 启动后端

```powershell
cd D:\Study\summ2026\BIMProject\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次在新环境运行时：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 启动前端

```powershell
cd D:\Study\summ2026\BIMProject\frontend
npm install
npm run dev
```

访问地址：

- 前端：http://127.0.0.1:5173/
- 后端 API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

Vite 会把 `/api` 请求代理到 `http://127.0.0.1:8000`。

## 模型配置

后端优先读取 `backend/config.json`：

```json
{
  "api_key": "sk-your-api-key",
  "api_base": "https://api.deepseek.com",
  "model": "deepseek-flash",
  "temperature": 0.2
}
```

配置优先级：

1. `backend/config.json`
2. `.env` 中的 `DEEPSEEK_*` 或 `OPENAI_*` 变量
3. 代码默认值

修改 `config.json` 后需要重启后端。

## 网页使用

### 提交模型

模型库中的“提交 BIM 模型”支持拖放或选择文件。

- IFC 会自动上传、解析、缓存并进入三维查看。
- RVT 会被保存，但不能直接解析。
- RVT 需要先在 Revit 中导出为 IFC。

### 查看模型

- 使用鼠标旋转、平移和缩放
- 使用视图适配按钮自动居中模型
- 使用俯视图按钮查看平面方向
- 切换结构边线
- 切换 IFC 材质、构件着色和统一灰色

### 使用模型助手

前端发送聊天请求时会携带：

```json
{
  "question": "当前模型有多少墙？",
  "model_id": "sample-rac_basic_sample_project-ifc",
  "model_context": {
    "project": {},
    "statistics": {},
    "storeys": [],
    "element_counts": [],
    "units": {},
    "quantity_totals": {}
  }
}
```

后端会同时读取模型缓存、匹配相关构件并检索本地资料，最终通过 SSE 返回流式回答。

## 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 检查服务和 AI 配置 |
| GET | `/api/models` | 获取模型列表 |
| GET | `/api/models/{id}` | 获取模型解析详情 |
| GET | `/api/models/{id}/elements` | 查询构件 |
| GET | `/api/models/{id}/content` | 获取 IFC 文件 |
| POST | `/api/models/upload` | 提交 IFC/RVT |
| GET | `/api/knowledge/status` | 获取知识库状态 |
| POST | `/api/knowledge/reindex` | 重建知识库索引 |
| POST | `/api/chat` | 普通问答 |
| POST | `/api/chat/stream` | SSE 流式问答 |

## 验证命令

```powershell
# 前端代码检查
cd frontend
npm run lint

# 前端生产构建
npm run build

# 浏览器冒烟测试，需要前后端正在运行
npm run test:smoke

# 后端语法检查
cd ..
.\backend\.venv\Scripts\python.exe -m compileall -q backend\app
```

## 注意事项

- `config.json` 包含明文 API Key，不要提交到公共仓库。
- 顶部“已配置”只表示 Key 存在，不保证模型服务实际可访问。
- 后端进程需要具备外网访问权限，否则聊天会报 `Connection error`。
- IFC 渲染在浏览器中完成，模型质量取决于 IFC 导出质量。
- 大型 IFC 会占用较多显存和内存。
- AI 回答可能存在错误，工程决策需要人工复核。

更详细的模块说明：

- [前端 README](frontend/README.md)
- [后端 README](backend/README.md)
- [知识库 README](backend/documents/README.md)
