import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// 应用入口：将 React 根组件挂载到 index.html 提供的 #root 节点。
createRoot(document.getElementById('root')).render(
  // StrictMode 会在开发环境重复执行部分生命周期逻辑，用于提前暴露副作用问题。
  <StrictMode>
    <App />
  </StrictMode>,
)
