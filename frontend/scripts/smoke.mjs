import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { chromium } from 'playwright'

// 端到端烟测：验证 IFC 加载、WebGL 渲染、视图切换、构件表格和移动端布局。
const baseUrl = process.env.BIM_APP_URL || 'http://127.0.0.1:5173'
const outputDir = resolve('test-artifacts')
await mkdir(outputDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
// 收集页面异常和控制台错误，作为最终测试失败条件之一。
const errors = []

// 创建页面并等待 IFC 几何解析和渲染统计完成。
async function preparePage(context) {
  const page = await context.newPage()
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`)
  })
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' })
  await page.locator('.bim-viewer__canvas canvas').waitFor({ timeout: 45_000 })
  await page.locator('.bim-viewer__stats').waitFor({ timeout: 90_000 })
  await page.waitForTimeout(800)
  return page
}

// 对画布截图采样，判断模型是否有足够可见像素和颜色层次。
async function pixelStats(page, png) {
  const dataUrl = `data:image/png;base64,${png.toString('base64')}`
  return page.evaluate(async (source) => {
    const image = new Image()
    image.src = source
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = image.width
    canvas.height = image.height
    const context = canvas.getContext('2d')
    context.drawImage(image, 0, 0)
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
    const buckets = new Set()
    let differentPixels = 0
    let samples = 0
    for (let index = 0; index < pixels.length; index += 32) {
      const red = pixels[index]
      const green = pixels[index + 1]
      const blue = pixels[index + 2]
      const distance =
        Math.abs(red - 232) + Math.abs(green - 237) + Math.abs(blue - 239)
      if (distance > 36) differentPixels += 1
      buckets.add(`${red >> 4}-${green >> 4}-${blue >> 4}`)
      samples += 1
    }
    return {
      width: image.width,
      height: image.height,
      nonBackgroundRatio: differentPixels / samples,
      colorBuckets: buckets.size,
    }
  }, dataUrl)
}

// 桌面端检查初始视图、俯视图和构件明细。
const desktopContext = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 1,
})
const desktop = await preparePage(desktopContext)
const viewerText = await desktop.locator('.bim-viewer__stats').innerText()
const canvas = desktop.locator('.bim-viewer__canvas canvas')
const canvasShot = await canvas.screenshot()
const desktopStats = await pixelStats(desktop, canvasShot)
await desktop.screenshot({
  path: resolve(outputDir, 'desktop.png'),
  fullPage: true,
})

await desktop.getByRole('button', { name: '俯视图' }).click()
await desktop.waitForTimeout(300)
const topViewShot = await canvas.screenshot()
const topViewStats = await pixelStats(desktop, topViewShot)
await desktop.screenshot({
  path: resolve(outputDir, 'top-view.png'),
  fullPage: true,
})
await desktop.getByRole('button', { name: '适配视图' }).click()

await desktop.getByRole('button', { name: '构件清单' }).click()
await desktop.locator('.element-table tbody tr').first().waitFor()
const elementRows = await desktop.locator('.element-table tbody tr').count()
await desktop.screenshot({
  path: resolve(outputDir, 'elements.png'),
  fullPage: true,
})

await desktop.getByRole('button', { name: /\.rvt/ }).first().click()
await desktop.getByText('RVT 无法在浏览器中直接解析').waitFor()
await desktop.getByRole('button', { name: /\.ifc/ }).first().click()
await desktop.locator('.bim-viewer__stats').waitFor({ timeout: 90_000 })

// 移动端使用独立浏览器上下文，检查响应式布局是否产生横向溢出。
const mobileContext = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 1,
})
const mobile = await preparePage(mobileContext)
const overflow = await mobile.evaluate(
  () => document.documentElement.scrollWidth - window.innerWidth,
)
await mobile.screenshot({
  path: resolve(outputDir, 'mobile.png'),
  fullPage: true,
})

const summary = {
  viewerText,
  desktopStats,
  topViewStats,
  elementRows,
  mobileOverflow: overflow,
  errors,
}
await writeFile(
  resolve(outputDir, 'summary.json'),
  JSON.stringify(summary, null, 2),
)
console.log(JSON.stringify(summary, null, 2))

// 释放测试浏览器资源。
await mobileContext.close()
await desktopContext.close()
await browser.close()

if (
  // 桌面和俯视图都必须有足够可见像素，表格至少返回一条记录。
  desktopStats.nonBackgroundRatio < 0.03 ||
  desktopStats.colorBuckets < 8 ||
  topViewStats.nonBackgroundRatio < 0.03 ||
  topViewStats.colorBuckets < 8 ||
  elementRows < 1 ||
  overflow > 1 ||
  errors.length > 0
) {
  process.exitCode = 1
}
