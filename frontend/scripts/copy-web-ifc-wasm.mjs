import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// npm install 后把 web-ifc 的 WASM 文件复制到 public，供浏览器运行时加载。
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
// 兼容不同版本 web-ifc 的包内目录结构。
const candidates = [
  resolve(root, 'node_modules/web-ifc/web-ifc.wasm'),
  resolve(root, 'node_modules/web-ifc/dist/web-ifc.wasm'),
]
const source = candidates.find((candidate) => existsSync(candidate))

if (!source) {
  // 复制失败时只警告，不阻断前端依赖安装和生产构建。
  console.warn('web-ifc.wasm was not found; the IFC viewer may not initialize.')
} else {
  // Vite 会把 public 下的 WASM 原样复制到构建输出目录。
  const destination = resolve(root, 'public/web-ifc.wasm')
  mkdirSync(dirname(destination), { recursive: true })
  copyFileSync(source, destination)
}
