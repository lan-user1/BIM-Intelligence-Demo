# BIM Intelligence Frontend

前端使用 React、Vite、Three.js 和 web-ifc 构建，负责 BIM 模型提交、IFC 浏览器端解析、三维渲染、构件表格、模型概览和流式 AI 问答。

## 技术栈

| 技术 | 版本或用途 |
| --- | --- |
| React | 19.x |
| Vite | 8.x |
| Three.js | WebGL2 三维渲染 |
| web-ifc | IFC 几何和属性解析 |
| react-markdown | Markdown 回答渲染 |
| remark-gfm | 表格和 GFM 扩展 |
| remark-breaks | 保留回答中的换行 |
| lucide-react | 图标 |
| Playwright | 浏览器冒烟测试 |
| Oxlint | 前端代码检查 |

## 目录结构

```text
frontend/
├── public/
│   └── web-ifc.wasm
├── scripts/
│   ├── copy-web-ifc-wasm.mjs
│   └── smoke.mjs
├── src/
│   ├── components/
│   │   ├── BimViewer.jsx
│   │   ├── ChatPanel.jsx
│   │   ├── ElementTable.jsx
│   │   └── ModelOverview.jsx
│   ├── api.js
│   ├── App.jsx
│   ├── App.css
│   ├── index.css
│   └── main.jsx
├── index.html
├── package.json
├── vite.config.js
└── README.md
```

## 启动

```powershell
cd frontend
npm install
npm run dev
```

默认访问地址：http://127.0.0.1:5173

开发服务器会把 `/api` 请求代理到：

```text
http://127.0.0.1:8000
```

代理配置位于 `vite.config.js`。

## 可用命令

```powershell
npm run dev
npm run build
npm run lint
npm run preview
npm run test:smoke
```

`npm run test:smoke` 需要后端和前端正在运行。

## IFC 渲染流程

### 1. 获取模型文件

`App.jsx` 根据当前模型生成：

```text
/api/models/{model_id}/content
```

`BimViewer` 下载 IFC 原始字节，不会从后端加载预生成图片。

### 2. 初始化 WebIFC

`BimViewer.jsx` 动态加载 `web-ifc`，并使用：

```text
frontend/public/web-ifc.wasm
```

WASM 文件由 `postinstall` 脚本自动复制。

主要调用：

```javascript
const api = new IfcAPI()
await api.Init()
const modelID = api.OpenModel(data)
```

### 3. 生成 Three.js 几何

使用 `StreamAllMeshes` 遍历 IFC 构件：

```javascript
api.StreamAllMeshes(modelID, (mesh) => {
  // 读取 mesh.geometries
  // GetGeometry
  // GetVertexArray
  // GetIndexArray
})
```

WebIFC 返回的顶点数组格式为：

```text
position.x, position.y, position.z, normal.x, normal.y, normal.z
```

代码将其拆分为：

- `THREE.BufferAttribute(position, 3)`
- `THREE.BufferAttribute(normal, 3)`
- 索引三角形

### 4. IFC 与 Three.js 坐标系

IFC 使用 Z 轴向上。渲染场景保留 IFC 原始坐标，将 `Z` 作为场景上方向，模型平面位于 `XY` 平面：

```javascript
camera.up.set(0, 0, 1)
```

地面网格和俯视图也按 Z-up 方式对齐。

### 5. GPU 渲染

渲染器使用：

- WebGL2
- `powerPreference: 'high-performance'`
- 抗锯齿
- ACES Filmic 色调映射
- RoomEnvironment 环境光照
- 半球光、主方向光和补光

实际渲染由浏览器 WebGL 实现完成。开启浏览器硬件加速后会使用独立显卡或集成 GPU，否则可能回退到软件渲染。

### 6. 结构边线

每个构件通过 `EdgesGeometry` 生成结构轮廓，再合并成一个 `LineSegments` 几何体，减少 GPU draw call。

边线可以在查看器右上角开关。

### 7. 相机适配

模型加载后计算包围球：

- 自动计算相机距离
- 自动调整近裁剪面和远裁剪面
- 支持鼠标旋转、平移和缩放
- 支持视图适配和俯视图

## 模型显示模式

查看器左上角可选择：

| 模式 | 说明 |
| --- | --- |
| IFC 材质 | 使用 IFC 中的原始颜色和透明度 |
| 构件着色 | 按构件 ID 分配不同颜色 |
| 统一灰色 | 适合检查几何和结构关系 |

## 提交模型

“提交 BIM 模型”支持拖放或文件选择：

- `.ifc`
- `.rvt`

IFC 上传后由后端解析，然后前端自动选中并渲染。

RVT 仅保存文件，浏览器无法直接解析。

## AI 问答请求

发送问题时，前端会向 `/api/chat/stream` 提交：

```json
{
  "question": "当前模型有多少墙？",
  "history": [],
  "model_id": "sample-rac_basic_sample_project-ifc",
  "model_context": {
    "id": "sample-rac_basic_sample_project-ifc",
    "file_name": "rac_basic_sample_project.ifc",
    "schema": "IFC4",
    "project": {},
    "statistics": {},
    "storeys": [],
    "element_counts": [],
    "category_counts": [],
    "units": {},
    "quantity_totals": {},
    "top_properties": []
  }
}
```

`model_context` 不包含全部构件明细，避免请求体过大。后端会再次读取完整模型数据并匹配相关构件。

## Markdown 渲染

模型回答使用 `react-markdown` 渲染，支持：

- 标题
- 段落和换行
- 有序和无序列表
- 引用
- 行内代码和代码块
- 表格
- 链接
- 分隔线

SSE 流式内容会持续重新渲染。

## 浏览器测试

测试脚本会检查：

- IFC 画布是否成功生成
- 三维内容是否为非空画面
- 构件表格是否有数据
- RVT 提示是否正常
- Markdown 是否渲染
- 移动端是否横向溢出
- 浏览器控制台是否有错误

测试产物位于：

```text
frontend/test-artifacts
```

## 注意事项

- web-ifc 首次加载需要下载约 3.5 MB 的 JavaScript 代码和读取 WASM。
- 大型 IFC 会明显增加内存、显存和解析时间。
- Three.js 几何直接来自 IFC，不会自动减面。
- 浏览器关闭硬件加速时，性能会下降。
- 如果 `public/web-ifc.wasm` 丢失，重新执行 `npm install`。
- 生产环境需要将 `/api` 反向代理到 FastAPI，并保证 `/web-ifc.wasm` 可访问。
