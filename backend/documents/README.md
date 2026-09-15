# Knowledge Documents

该目录用于存放模型助手可以检索的 BIM 知识资料。

## 支持格式

- PDF：`.pdf`
- 纯文本：`.txt`
- Markdown：`.md`

## 推荐内容

- BIM 实施标准
- IFC 建模规范
- 项目设计说明
- 建模交付要求
- 构件命名规则
- 工程量计算规则
- 运维和设备手册
- 项目 FAQ

## 索引目录

后端会索引：

```text
backend/documents
MySource
```

`MySource` 中的 IFC 和 RVT 不会作为文本资料处理，其中的 PDF、TXT 和 Markdown 会被索引。

## 添加资料

1. 将文件放入 `backend/documents`。
2. 等待应用启动后的后台索引完成，或调用重建接口。

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/knowledge/reindex
```

3. 检查索引状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/status
```

## 检索方式

当前实现使用字符级 TF-IDF：

- 对中文和英文文本分块
- 使用 2 到 4 字符的 n-gram
- 按余弦相似度排序
- 将最相关的片段加入模型提示词

它不是向量数据库。优点是安装简单、无需额外模型和 GPU；缺点是语义检索能力弱于 embedding 和 rerank 方案。

## 文件建议

- PDF 尽量使用可选择文本的版本，不要使用纯扫描图片。
- 单个文件建议不超过 10 MB。
- 使用清晰标题、章节和段落。
- 避免把大量无关内容放入同一个文件。
- 更新资料后重新建立索引。

## 注意事项

- 这里的内容会作为模型回答上下文，不要放入敏感信息。
- PDF 提取结果取决于文件本身结构。
- 扫描版 PDF 当前不会自动进行 OCR。
- 文档内容不能替代工程规范和人工审核。
