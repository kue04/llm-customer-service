# 前端设计文档

本文档描述当前项目第一版前端应该具备的内容。目标不是做营销页面，而是做一个面向学习和调试的 RAG 客服工作台。

## 产品定位

前端名称建议：

```text
外卖客服 RAG 调试台
```

核心目标：

- 输入用户问题并查看检索结果。
- 对比 `vector` 和 `hybrid` 检索模式。
- 展示分数拆解，理解排序原因。
- 查看最终客服回复。
- 浏览和搜索知识库样本。

## 技术选型

推荐：

- Vite
- React
- TypeScript
- Tailwind CSS
- lucide-react

理由：

- Vite 启动快，适合本地学习项目。
- React 组件拆分清晰，适合聊天面板、检索结果面板、知识库浏览器。
- TypeScript 可以和 FastAPI 的 schema 对齐，减少字段拼写错误。
- Tailwind 适合快速构建工作台式界面。

## 页面布局

第一版建议做单页工作台：

```text
顶部：模型状态栏
左侧：问题输入和聊天回复
中间：检索结果和分数拆解
右侧：知识库样本浏览
```

不要做单独 landing page。用户打开页面后应该直接进入调试工作台。

## 页面模块

### ModelInfoBar

接口：

```text
GET /model/info
```

展示内容：

- 基础模型名
- LoRA adapter 是否启用
- adapter 名称
- 后端连接状态

### QueryControlPanel

功能：

- 输入用户问题
- 选择 `mode`
- 设置 `limit`
- 设置 `min_score`
- 点击检索
- 点击生成客服回复

控件建议：

- `mode` 用 segmented control 或 radio group。
- `limit` 用 number input 或 stepper。
- `min_score` 用 slider + number input。

### RetrievalPanel

接口：

```text
POST /retrieval/search
```

展示字段：

- `rank`
- `score`
- `vector_score`
- `keyword_bonus`
- `direction_penalty`
- `category`
- `intent`
- `question`
- `answer`

设计要求：

- 每条检索结果用紧凑卡片。
- `score` 显示为主分数。
- `vector_score`、`keyword_bonus`、`direction_penalty` 用小标签展示。
- `category` 和 `intent` 放在卡片顶部，便于快速扫描。
- 如果 `direction_penalty > 0`，用明显但克制的样式提示这条被方向性降权。

### ChatPanel

接口：

```text
POST /chat/prompt
GET /chat/history
```

展示内容：

- 用户问题
- 客服回复
- `confidence_score`
- 重新打开订单客服时恢复后端保存的历史消息和最近一次诊断响应

注意：

- 聊天历史以后端 SQLite 为准，前端不应只依赖 React 内存状态。
- 前端可以继续保留本地临时状态，但重新打开窗口时应调用 `/chat/history`。

### Order State

接口：

```text
PUT /orders/{order_id}/state
GET /orders/{order_id}/state
```

功能：

- 保存当前订单状态，供客服订单工具读取。
- 模拟不同外卖阶段时，必须更新同一个订单，不要创建 `DEMO-PAID`、`DEMO-RIDER` 这类多份订单。
- 订单列表中同一份订单只能有一个订单号，状态以 `status` 和 `delivery_status` 流转。

### KnowledgeBrowser

接口：

```text
GET /examples/categories
GET /examples/by-category
POST /examples/search
```

功能：

- 展示分类列表。
- 按分类查看样本。
- 按关键词搜索样本。
- 展示 `question` 和 `answer`。

## 推荐目录结构

```text
frontend/
  package.json
  index.html
  src/
    main.tsx
    App.tsx
    api/
      client.ts
      retrieval.ts
      chat.ts
      examples.ts
      model.ts
    components/
      ModelInfoBar.tsx
      QueryControlPanel.tsx
      RetrievalPanel.tsx
      ChatPanel.tsx
      KnowledgeBrowser.tsx
      ScoreBreakdown.tsx
    types/
      api.ts
    styles/
      index.css
```

## TypeScript 类型建议

```ts
export type RetrievalMode = "vector" | "hybrid";

export type RetrievalSearchRequest = {
  query: string;
  mode: RetrievalMode;
  limit: number;
  min_score: number;
};

export type RetrievalResultItem = {
  rank: number;
  score: number;
  vector_score: number;
  keyword_bonus: number;
  direction_penalty: number;
  category: string;
  intent: string;
  question: string;
  answer: string;
};

export type RetrievalSearchResponse = {
  query: string;
  mode: RetrievalMode;
  count: number;
  results: RetrievalResultItem[];
};
```

## 第一版开发顺序

1. 初始化 Vite + React + TypeScript。
2. 配置 API base URL。
3. 实现 `/retrieval/search` 对接。
4. 实现 `RetrievalPanel`，展示分数拆解。
5. 实现 `mode`、`limit`、`min_score` 控件。
6. 接入 `/model/info`。
7. 接入 `/chat/prompt`。
8. 接入知识库浏览接口。

## 交互原则

- 第一屏直接展示调试工作台。
- 不做营销 hero。
- 信息密度适中，优先可读和可扫描。
- 避免装饰性渐变和大面积卡片堆叠。
- 所有分数都应该能解释排序原因。

## 后续增强方向

- 增加 prompt preview。
- 增加固定评估集可视化。
- 支持导出单次检索报告。
- 支持对比 `vector` 与 `hybrid` 的 TopK 差异。
- 支持 rerank 后分数展示。
