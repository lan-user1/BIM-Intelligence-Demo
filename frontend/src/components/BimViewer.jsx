import { useEffect, useRef, useState } from 'react'
import {
  Box,
  Eye,
  Focus,
  LoaderCircle,
  Palette,
  Rotate3d,
  ScanLine,
} from 'lucide-react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'

// BimViewer 负责在浏览器端完成 IFC 下载、几何解析、Three.js 场景构建和交互控制。

let ifcApiPromise

// IFC 场景使用 Z 轴表示高度，相机上方向和网格平面都遵循这一坐标约定。
const SCENE_UP = new THREE.Vector3(0, 0, 1)  // SCENE_UP 改为 Z 轴向上
// 默认斜视角方向，兼顾建筑轮廓、立面高度和平面布局。
const FIT_DIRECTION = new THREE.Vector3(1, -1, 0.8).normalize()

// WebIFC 初始化成本较高，通过模块级 Promise 保证整个页面只初始化一次。
async function getIfcApi() {
  if (!ifcApiPromise) {
    ifcApiPromise = (async () => {
      const { IfcAPI } = await import('web-ifc')
      const api = new IfcAPI()
      if (typeof api.SetWasmPath === 'function') {
        api.SetWasmPath('/')
      }
      await api.Init()
      return api
    })()
  }
  return ifcApiPromise
}

// 递归释放 Three.js 几何和材质，避免反复切换模型时泄漏 GPU 资源。
function disposeObject(object) {
  object.traverse((child) => {
    child.geometry?.dispose?.()
    if (Array.isArray(child.material)) {
      child.material.forEach((material) => material.dispose?.())
    } else {
      child.material?.dispose?.()
    }
  })
}

// 将 WebIFC 的颜色四元组转换为 Three.js 颜色和透明度。
function placedColor(placedGeometry) {
  const source = placedGeometry.color
  const hasColor =
    source &&
    Number.isFinite(source.x) &&
    Number.isFinite(source.y) &&
    Number.isFinite(source.z)
  const opacity =
    hasColor && Number.isFinite(source.w)
      ? THREE.MathUtils.clamp(source.w, 0.08, 1)
      : 1
  const color = hasColor
    ? new THREE.Color(source.x, source.y, source.z)
    : new THREE.Color(0x9aa9ad)
  return { color, opacity }
}

// 使用构件 expressID 生成稳定的伪随机颜色，便于区分不同构件。
function componentColor(expressID) {
  const hue = ((Number(expressID) || 1) * 0.61803398875) % 1
  return new THREE.Color().setHSL(hue, 0.46, 0.56)
}

// 在 IFC 材质、构件色和统一灰色三种显示模式之间切换。
function applyDisplayMode(group, mode) {
  if (!group) return
  group.traverse((child) => {
    if (!child.isMesh || !child.userData.originalColor) return
    const color =
      mode === 'component'
        ? componentColor(child.userData.expressID)
        : mode === 'monochrome'
          ? new THREE.Color(0x9eacb0)
          : child.userData.originalColor
    child.material.color.copy(color)
    child.material.needsUpdate = true
  })
}

// 计算模型场景包围盒和用于相机适配的包围球。
function modelBounds(group) {
  const bounds = new THREE.Box3().setFromObject(group)
  const sphere = bounds.getBoundingSphere(new THREE.Sphere())
  return { bounds, sphere }
}

// 对数组取指定分位数，用于识别少量远离主体模型的异常构件。
function percentile(values, ratio) {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const index = Math.min(
    Math.max(Math.floor((sorted.length - 1) * ratio), 0),
    sorted.length - 1,
  )
  return sorted[index]
}

// 从全部几何包围盒中估计主建筑范围，避免个别构件影响相机取景。
function focusedBounds(geometryBoxes) {
  if (!geometryBoxes.length) return new THREE.Box3()

  const centers = geometryBoxes.map((box) =>
    box.getCenter(new THREE.Vector3()),
  )
  const medianCenter = new THREE.Vector3(
    percentile(centers.map((center) => center.x), 0.5),
    percentile(centers.map((center) => center.y), 0.5),
    percentile(centers.map((center) => center.z), 0.5),
  )
  const distances = centers.map((center) => center.distanceTo(medianCenter))
  const diagonals = geometryBoxes.map((box) => box.getSize(new THREE.Vector3()).length())
  const distanceLimit = Math.max(percentile(distances, 0.95) * 1.5, 0.01)
  const diagonalLimit = Math.max(percentile(diagonals, 0.98) * 2.5, 0.01)

  const focused = new THREE.Box3()
  geometryBoxes.forEach((box, index) => {
    if (
      distances[index] <= distanceLimit &&
      diagonals[index] <= diagonalLimit
    ) {
      focused.union(box)
    }
  })
  return focused.isEmpty() ? new THREE.Box3().setFromPoints(centers) : focused
}

// 根据模型尺寸动态设置近远裁剪面，兼顾小尺寸构件和大型项目坐标。
function updateCameraClipping(camera, sphere) {
  camera.near = Math.max(sphere.radius / 2000, 0.005)
  camera.far = Math.max(sphere.radius * 250, 1000)
  camera.updateProjectionMatrix()
}

// 同时考虑垂直和水平视场角，保证模型在任意画布比例下完整可见。
function fitCameraToBounds(camera, controls, sphere, padding = 1.18) {
  const verticalDistance = sphere.radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))
  const horizontalFov = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.aspect)
  const horizontalDistance = sphere.radius / Math.sin(horizontalFov / 2)
  const distance = Math.max(verticalDistance, horizontalDistance) * padding
  camera.up.copy(SCENE_UP)
  camera.position.copy(sphere.center).addScaledVector(FIT_DIRECTION, distance)
  controls.target.copy(sphere.center)
  controls.minDistance = Math.max(sphere.radius * 0.03, 0.01)
  controls.maxDistance = Math.max(sphere.radius * 40, 10)
  updateCameraClipping(camera, sphere)
  controls.update()
}

// 切换到接近正上方的俯视视角，并保持与 OrbitControls 相同的 Z-up 约定。
function topView(camera, controls, sphere) {
  const distance =
    (sphere.radius / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) *
    1.08
  camera.up.copy(SCENE_UP)
  camera.position.set(
    sphere.center.x,
    sphere.center.y + distance,
    sphere.center.z + distance * 0.001, //已修改
  )
  controls.target.copy(sphere.center)
  updateCameraClipping(camera, sphere)
  controls.update()
}

// 从构件几何提取轮廓线段，并按全局顶点预算控制边线生成成本。
function addEdgeGeometry(geometry, chunks, state) {
  if (state.vertices >= 2_400_000) return
  const edges = new THREE.EdgesGeometry(geometry, 32)
  const position = edges.getAttribute('position')
  if (position?.array?.length) {
    const chunk = new Float32Array(position.array)
    chunks.push(chunk)
    state.vertices += chunk.length / 3
  }
  edges.dispose()
}

// 将所有 EdgeGeometry 顶点合并成一个 LineSegments，减少 draw call。
function mergeEdgeChunks(chunks, vertexCount) {
  const positions = new Float32Array(vertexCount * 3)
  let offset = 0
  chunks.forEach((chunk) => {
    positions.set(chunk, offset)
    offset += chunk.length
  })
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.computeBoundingSphere()
  return geometry
}

export default function BimViewer({
  contentUrl,
  file,
  modelName,
  onStats,
  className = '',
}) {
  // Three.js 场景对象及供 React 事件处理器访问的可变引用。
  const mountRef = useRef(null)
  const sceneRef = useRef(null)
  const cameraRef = useRef(null)
  const rendererRef = useRef(null)
  const controlsRef = useRef(null)
  const modelGroupRef = useRef(null)
  const edgeRef = useRef(null)
  const gridRef = useRef(null)
  const environmentRef = useRef(null)
  const displayModeRef = useRef('material')
  const edgesVisibleRef = useRef(true)

  // 组件展示状态：加载状态、错误信息、统计数据及工具栏选项。
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [stats, setStats] = useState(null)
  const [displayMode, setDisplayMode] = useState('material')
  const [edgesVisible, setEdgesVisible] = useState(true)

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return undefined

    // 创建场景、相机和 WebGL 渲染器，并挂载到组件容器。
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0xe4eaec)
    sceneRef.current = scene

    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 2_000_000)
    camera.up.copy(SCENE_UP)
    camera.position.set(15, -15, 12)
    cameraRef.current = camera

    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
      stencil: false,
    })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.05
    renderer.shadowMap.enabled = false
    mount.appendChild(renderer.domElement)
    rendererRef.current = renderer

    // 使用室内环境贴图为标准材质提供柔和反射，无需额外下载 HDR 文件。
    const pmrem = new THREE.PMREMGenerator(renderer)
    const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
    scene.environment = environment
    environmentRef.current = environment
    pmrem.dispose()

    // OrbitControls 使用相机上方向作为轨道轴，相机已预设为 Z-up。
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.075
    controls.screenSpacePanning = true
    controls.zoomToCursor = true
    controls.rotateSpeed = 0.72
    controls.zoomSpeed = 0.9
    controls.panSpeed = 0.78
    controlsRef.current = controls

    // 半球光提供环境基础照明，两个方向光分别塑造主次面。
    const hemisphere = new THREE.HemisphereLight(0xf5fbff, 0x53666d, 1.6)
    scene.add(hemisphere)
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.4)
    keyLight.position.set(24, 20, 38)
    scene.add(keyLight)
    const fillLight = new THREE.DirectionalLight(0xffead2, 0.85)
    fillLight.position.set(-22, -26, 14)
    scene.add(fillLight)

    const resize = () => {
      const { clientWidth, clientHeight } = mount
      if (!clientWidth || !clientHeight) return
      renderer.setSize(clientWidth, clientHeight, false)
      camera.aspect = clientWidth / clientHeight
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(mount)
    resize()

    const render = () => {
      controls.update()
      renderer.render(scene, camera)
    }
    renderer.setAnimationLoop(render)

    return () => {
      // 卸载时停止渲染循环并释放场景中的 GPU 资源。
      renderer.setAnimationLoop(null)
      observer.disconnect()
      controls.dispose()
      environment.dispose()
      if (gridRef.current) {
        scene.remove(gridRef.current)
        disposeObject(gridRef.current)
        gridRef.current = null
      }
      if (modelGroupRef.current) {
        scene.remove(modelGroupRef.current)
        disposeObject(modelGroupRef.current)
      }
      renderer.dispose()
      renderer.domElement.remove()
      sceneRef.current = null
      cameraRef.current = null
      rendererRef.current = null
      controlsRef.current = null
      modelGroupRef.current = null
      edgeRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!contentUrl && !file) {
      setStatus('idle')
      return undefined
    }

    let cancelled = false
    const controller = new AbortController()

    const load = async () => {
      // 每次加载前重置状态，避免旧模型的统计数据和错误残留。
      setStatus('loading')
      setError('')
      setStats(null)
      onStats?.(null)
      try {
        const payload = file
          ? await file.arrayBuffer()
          : await fetch(contentUrl, { signal: controller.signal }).then((response) => {
              if (!response.ok) {
                throw new Error(`模型文件加载失败 (${response.status})`)
              }
              return response.arrayBuffer()
            })
        if (cancelled) return

        const api = await getIfcApi()
        // 将项目坐标平移到原点，降低大型工程坐标带来的浮点精度问题。
        const modelId = api.OpenModel(new Uint8Array(payload), {
          COORDINATE_TO_ORIGIN: true,
          USE_FAST_BOOLS: false,
        })
        if (modelId < 0) throw new Error('WebIFC 无法打开该模型。')

        const group = new THREE.Group()
        group.name = 'IFC_MODEL_ROOT'
        // 边线、包围盒和统计在遍历构件时逐步累积。
        const edgeChunks = []
        const geometryBoxes = []
        const edgeState = { vertices: 0 }
        const expressIds = new Set()
        let geometryCount = 0
        let triangleCount = 0

        api.StreamAllMeshes(modelId, (mesh) => {
          if (cancelled) return
          expressIds.add(mesh.expressID)
          const placedGeometries = mesh.geometries
          const count =
            typeof placedGeometries.size === 'function'
              ? placedGeometries.size()
              : placedGeometries.size

          for (let index = 0; index < count; index += 1) {
            // 同一个 IFC 构件可能包含多个带独立位置矩阵的几何实例。
            const placed = placedGeometries.get(index)
            const geometryData = api.GetGeometry(
              modelId,
              placed.geometryExpressID,
            )
            if (!geometryData) continue

            const vertices = api.GetVertexArray(
              geometryData.GetVertexData(),
              geometryData.GetVertexDataSize(),
            )
            const indices = api.GetIndexArray(
              geometryData.GetIndexData(),
              geometryData.GetIndexDataSize(),
            )
            if (!vertices?.length || !indices?.length) {
              geometryData.delete?.()
              continue
            }

            const vertexCount = Math.floor(vertices.length / 6)
            // WebIFC 顶点布局为 position.xyz + normal.xyz，需要拆成两个属性数组。
            const positions = new Float32Array(vertexCount * 3)
            const normals = new Float32Array(vertexCount * 3)
            for (
              let vertex = 0, target = 0;
              vertex + 5 < vertices.length;
              vertex += 6, target += 3
            ) {
              positions[target] = vertices[vertex]
              positions[target + 1] = vertices[vertex + 1]
              positions[target + 2] = vertices[vertex + 2]
              normals[target] = vertices[vertex + 3]
              normals[target + 1] = vertices[vertex + 4]
              normals[target + 2] = vertices[vertex + 5]
            }

            const geometry = new THREE.BufferGeometry()
            geometry.setAttribute(
              'position',
              new THREE.BufferAttribute(positions, 3),
            )
            geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3))
            geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indices), 1))
            if (placed.flatTransformation?.length === 16) {
              // 将构件在 IFC 世界中的放置矩阵直接烘焙进几何坐标。
              const transform = new THREE.Matrix4().fromArray(
                placed.flatTransformation,
              )
              geometry.applyMatrix4(transform)
            }
            geometry.computeBoundingBox()
            geometry.computeBoundingSphere()
            geometryBoxes.push(geometry.boundingBox.clone())

            const { color, opacity } = placedColor(placed)
            // 标准材质保留透明度、双面显示和轮廓所需的 polygon offset。
            const material = new THREE.MeshStandardMaterial({
              color,
              roughness: 0.68,
              metalness: 0.025,
              envMapIntensity: 0.72,
              side: THREE.DoubleSide,
              transparent: opacity < 1,
              opacity,
              depthWrite: opacity >= 1,
              polygonOffset: true,
              polygonOffsetFactor: 1,
              polygonOffsetUnits: 1,
            })
            const threeMesh = new THREE.Mesh(geometry, material)
            threeMesh.name = `IFC_${mesh.expressID}`
            threeMesh.userData.expressID = mesh.expressID
            threeMesh.userData.originalColor = color.clone()
            group.add(threeMesh)

            addEdgeGeometry(geometry, edgeChunks, edgeState)
            geometryCount += 1
            triangleCount += Math.floor(indices.length / 3)
            geometryData.delete?.()
          }
        })
        api.CloseModel(modelId)

        if (cancelled) {
          disposeObject(group)
          return
        }

        if (edgeChunks.length) {
          // 合并后的边线作为单个对象加入场景，减少 GPU draw call。
          const edgeGeometry = mergeEdgeChunks(edgeChunks, edgeState.vertices)
          const edgeMaterial = new THREE.LineBasicMaterial({
            color: 0x294047,
            transparent: true,
            opacity: 0.42,
            depthWrite: false,
          })
          const edgeLines = new THREE.LineSegments(edgeGeometry, edgeMaterial)
          edgeLines.name = 'IFC_EDGE_LINES'
          edgeLines.renderOrder = 2
          edgeLines.visible = edgesVisibleRef.current
          group.add(edgeLines)
          edgeRef.current = edgeLines
        } else {
          edgeRef.current = null
        }

        applyDisplayMode(group, displayModeRef.current)

        const previous = modelGroupRef.current
        if (previous) {
          sceneRef.current?.remove(previous)
          disposeObject(previous)
        }
        modelGroupRef.current = group
        sceneRef.current?.add(group)

        // 统一模型根节点朝向，使网格与当前场景坐标约定保持一致。
        group.rotation.x = Math.PI / 2 //已修改，优化475行与497行
        group.updateMatrixWorld(true)

        const { bounds } = modelBounds(group)
        if (bounds.isEmpty()) throw new Error('模型中没有可显示的几何体。')
        // group.updateMatrixWorld(true)
        const focusBounds = focusedBounds(geometryBoxes)
        focusBounds.applyMatrix4(group.matrixWorld)
        const focusSphere = focusBounds.getBoundingSphere(new THREE.Sphere())

        // 网格尺寸跟随主建筑范围，高度固定在 IFC 默认原点 Z=0。
        const previousGrid = gridRef.current
        if (previousGrid) {
          sceneRef.current?.remove(previousGrid)
          disposeObject(previousGrid)
        }
        const gridSpan = Math.max(focusSphere.radius * 2.4, 10)
        const grid = new THREE.GridHelper(
          gridSpan,
          24,
          0x7c9197,
          0xb9c5c8,
        )
        grid.position.set(
          focusSphere.center.x,
          focusSphere.center.y,
          0,
        )
        grid.rotation.x = Math.PI / 2
        grid.material.transparent = true
        grid.material.opacity = 0.26
        grid.material.depthWrite = false
        grid.renderOrder = -1
        gridRef.current = grid
        sceneRef.current?.add(grid)

        const camera = cameraRef.current
        const controls = controlsRef.current
        if (camera && controls) {
          // 使用主建筑聚焦范围而非完整包围盒，避免离群构件拉远相机。
          fitCameraToBounds(camera, controls, focusSphere)
        }

        // 汇总渲染统计和边界信息，供父组件和自动化测试读取。
        const result = {
          elements: expressIds.size,
          geometries: geometryCount,
          triangles: triangleCount,
          edgeSegments: Math.floor(edgeState.vertices / 2),
          graphics: rendererRef.current?.capabilities.isWebGL2
            ? 'WebGL2'
            : 'WebGL',
          bounds: {
            min: bounds.min.toArray(),
            max: bounds.max.toArray(),
            size: bounds.getSize(new THREE.Vector3()).toArray(),
          },
          focus_bounds: {
            min: focusBounds.min.toArray(),
            max: focusBounds.max.toArray(),
            size: focusBounds.getSize(new THREE.Vector3()).toArray(),
            source_geometries: geometryBoxes.length,
          },
        }
        setStats(result)
        onStats?.(result)
        setStatus('ready')
      } catch (loadError) {
        if (cancelled || loadError.name === 'AbortError') return
        setError(loadError.message || '模型加载失败。')
        setStatus('error')
      }
    }

    load()
    return () => {
      // 模型切换或组件卸载时中断旧请求，防止过期结果覆盖新模型。
      cancelled = true
      controller.abort()
    }
  }, [contentUrl, file, onStats])

  // 工具栏“适配视图”使用完整模型边界重新摆放相机。
  const fitView = () => {
    const group = modelGroupRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!group || !camera || !controls) return
    const { sphere } = modelBounds(group)
    fitCameraToBounds(camera, controls, sphere)
  }

  // 工具栏“俯视图”从正上方观察当前模型。
  const showTopView = () => {
    const group = modelGroupRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!group || !camera || !controls) return
    const { sphere } = modelBounds(group)
    topView(camera, controls, sphere)
  }

  // 显示模式同时更新 React 状态和已加载模型的材质颜色。
  const changeDisplayMode = (event) => {
    const mode = event.target.value
    displayModeRef.current = mode
    setDisplayMode(mode)
    applyDisplayMode(modelGroupRef.current, mode)
  }

  // 边线开关只控制合并后的 LineSegments 可见性，不重建几何。
  const toggleEdges = () => {
    const next = !edgesVisibleRef.current
    edgesVisibleRef.current = next
    setEdgesVisible(next)
    if (edgeRef.current) edgeRef.current.visible = next
  }

  return (
    <div
      className={`bim-viewer ${className}`}
      data-model-bounds={
        stats
          ? JSON.stringify({
              full: stats.bounds,
              focus: stats.focus_bounds,
            })
          : undefined
      }
    >
      <div ref={mountRef} className="bim-viewer__canvas" />
      <div className="bim-viewer__display">
        <Palette size={14} />
        <select
          value={displayMode}
          onChange={changeDisplayMode}
          title="模型着色方式"
          aria-label="模型着色方式"
        >
          <option value="material">IFC 材质</option>
          <option value="component">构件着色</option>
          <option value="monochrome">统一灰色</option>
        </select>
      </div>
      <div className="bim-viewer__tools">
        <button
          type="button"
          className={edgesVisible ? 'is-active' : ''}
          onClick={toggleEdges}
          title="显示或隐藏结构边线"
          aria-label="显示或隐藏结构边线"
        >
          <ScanLine size={17} />
        </button>
        <button type="button" onClick={fitView} title="适配视图" aria-label="适配视图">
          <Focus size={17} />
        </button>
        <button
          type="button"
          onClick={showTopView}
          title="俯视图"
          aria-label="俯视图"
        >
          <Eye size={17} />
        </button>
      </div>
      <div className="bim-viewer__axis" aria-hidden="true">
        <Rotate3d size={16} />
        <span>Z</span>
      </div>
      {status === 'idle' && (
        <div className="bim-viewer__empty">
          <Box size={28} />
          <span>{modelName || '选择 IFC 模型'}</span>
        </div>
      )}
      {status === 'loading' && (
        <div className="bim-viewer__empty">
          <LoaderCircle className="spin" size={28} />
          <span>正在解析几何数据</span>
        </div>
      )}
      {status === 'error' && (
        <div className="bim-viewer__empty bim-viewer__empty--error">
          <Box size={28} />
          <span>{error}</span>
        </div>
      )}
      {status === 'ready' && stats && (
        <div className="bim-viewer__stats">
          <span>{stats.elements.toLocaleString()} 个构件</span>
          <span>{stats.triangles.toLocaleString()} 三角面</span>
          <span>GPU · {stats.graphics}</span>
        </div>
      )}
    </div>
  )
}
