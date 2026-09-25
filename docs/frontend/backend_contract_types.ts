// 后端接口类型（由 scripts/export_frontend_contract.py 自动生成，请勿手改）
//
// 生成时间：2026-09-23 21:59
// 来源：main.app.openapi() → docs/frontend/openapi.json
//
// 用法：整体复制到前端 src/types/backendContract.ts，
// 页面内部仍可保留自己的 ViewModel，但**接口返回值的形状以本文件为准**。
//
// 注意两点：
// 1. 标记为可选（?）的字段是**后端允许缺失**的，前端必须处理 undefined，
//    不允许 `value || 0` 这类兜底（缺数据和真实 0 是两件事）。
// 2. 后端新增可选字段不会破坏本文件，但**删除或改名字段是破坏性变更**，
//    升级时先 diff docs/frontend/openapi.json。

/* eslint-disable */

export interface AuditLogItem {
  action_type: string;
  after_summary?: string;
  before_summary?: string;
  created_at: string;
  device_info?: string;
  id: number;
  ip?: string;
  object_id: string;
  object_type: string;
  operator_id: string;
  operator_role: string;
  request_id?: string;
}

export interface AuditLogListResponse {
  count: number;
  items: AuditLogItem[];
}

export interface Body_upload_document_knowledge_bases__knowledge_base_id__documents_post {
  file: string;
}

export interface CategoriesResponse {
  categories: string[];
  count: number;
}

export interface ChatHistoryMessage {
  content: string;
  created_at?: string;
  intent?: Record<string, unknown>;
  risk_level?: string;
  role: string;
}

export interface ChatHistoryResponse {
  latest_response?: Record<string, unknown>;
  messages?: ChatHistoryMessage[];
  order_id?: string | null;
  session_id?: string;
  user_id?: string;
}

export interface ChatRequest {
  channel?: string;
  message: string;
  order_id?: string | null;
  session_id?: string | null;
  user_id?: string;
}

export interface ChatResponse {
  answer_basis?: string;
  citations?: Record<string, unknown>[];
  confidence_level?: string;
  confidence_score: number;
  context_used?: Record<string, unknown>;
  conversation_status?: string;
  decision_trace?: Record<string, unknown>;
  evaluation_metrics?: Record<string, unknown>;
  evidence_citations?: Record<string, unknown>[];
  expected_evidence_keywords?: string[];
  expected_intent?: string;
  final_prompt: string;
  forbidden_keyword_hits?: string[];
  forbidden_keywords?: string[];
  full_trace?: Record<string, unknown>[];
  handoff_ticket?: Record<string, unknown> | null;
  human_review_reason?: string;
  intent_analysis?: Record<string, unknown>;
  issue_type?: string;
  manual_judgment?: Record<string, unknown>;
  matched_evidence_keywords?: string[];
  memory_snapshot?: Record<string, unknown>;
  missing_evidence_keywords?: string[];
  mixed_supporting_intent?: boolean;
  need_human_review?: boolean;
  needs_manual_review?: boolean;
  order_id?: string | null;
  prompt_context_items?: PromptContextItemResponse[];
  prompt_version?: string;
  reply: string;
  request_id?: string;
  retrieved_documents: string[];
  retrieved_items?: Record<string, unknown>[];
  risk_level?: string;
  risky_promises?: string[];
  safety_status?: Record<string, unknown>;
  session_id?: string;
  suggested_layer?: string;
  token_usage?: Record<string, unknown>;
  tool_results?: Record<string, unknown>[];
  trace: ChatTrace;
  used_primary_evidence?: boolean;
  user_id?: string;
}

export interface ChatReviewActionRequest {
  action: "accepted" | "edited_and_sent" | "human_handoff" | "marked_bad_case";
  final_reply?: string;
  operator_id?: string;
  operator_role?: string;
  reason?: string;
  request_id: string;
}

export interface ChatReviewActionResponse {
  action: "accepted" | "edited_and_sent" | "human_handoff" | "marked_bad_case";
  audit_id?: number | null;
  created_at: string;
  final_reply?: string;
  handoff_ticket?: Record<string, unknown> | null;
  order_id?: string | null;
  reason?: string;
  request_id: string;
  saved?: boolean;
  session_id: string;
  status: string;
  user_id: string;
}

export interface ChatTrace {
  answer_source: string;
  degraded: boolean;
  failure_stage: string;
  fallback_reason: string;
  latency_ms?: number;
  order_id?: string | null;
  reply_rules_applied: boolean;
  request_id?: string;
  retrieval_count: number;
  session_id?: string;
  top1_intent?: string;
  used_fallback_prompt: boolean;
  user_id?: string;
}

export interface ChunkIndexInfo {
  built_at: string;
  chunk_count: number;
  embedding_dimension: number;
  embedding_model: string;
  index_name: string;
  index_version: number;
  tokenizer_id?: string;
  visible_chunk_count?: number;
}

export interface ChunkRetrievalItem {
  acl?: Record<string, unknown>[];
  chunk_id: string;
  chunk_type?: string;
  content_hash?: string;
  document_id: string;
  document_title?: string;
  document_version: number;
  document_version_id: string;
  filename?: string;
  heading_path?: string[];
  page_end?: number | null;
  page_start?: number | null;
  rank: number;
  retrieval_origin?: string;
  score?: number;
  source_type?: string;
  source_uri?: string;
  tenant_id: string;
  text?: string;
  title?: string;
  token_count?: number;
}

export interface ChunkRetrievalRequest {
  limit?: number;
  min_score?: number | null;
  query: string;
}

export interface ChunkRetrievalResponse {
  count: number;
  index: ChunkIndexInfo;
  query: string;
  results: ChunkRetrievalItem[];
  retrieval_path?: "chunk-index" | "seed-faq-demo";
}

export interface DocumentDetail {
  created_at: string;
  created_by?: string | null;
  id: string;
  knowledge_base_id: string;
  latest_version?: DocumentVersionSummary | null;
  source_type: string;
  source_uri: string;
  status: string;
  tenant_id: string;
  title: string;
  updated_at: string;
}

export interface DocumentReprocessResponse {
  document_id: string;
  job_id: string;
  job_stage: string;
  job_status: string;
  status?: "accepted";
}

export interface DocumentUploadResponse {
  document_id: string;
  filename: string;
  job_id: string;
  job_stage: string;
  job_status: string;
  knowledge_base_id: string;
  reused_document: boolean;
  size_bytes: number;
  source_type: string;
  source_uri: string;
  status?: "accepted";
}

export interface DocumentVersionListResponse {
  document_id: string;
  items: DocumentVersionSummary[];
  total: number;
}

export interface DocumentVersionSummary {
  content_hash: string;
  created_at: string;
  id: string;
  parser_name: string;
  parser_version: string;
  status: string;
  version: number;
}

export interface ExampleItem {
  answer: string;
  question: string;
}

export interface ExamplesByCategoryResponse {
  category: string;
  count: number;
  examples: ExampleItem[];
}

export interface ExportEvalCaseRequest {
  feedback_id: number;
}

export interface ExportEvalCaseResponse {
  eval_case: Record<string, unknown>;
  feedback_id: number;
}

export interface FeedbackItem {
  answer_source: string;
  created_at: string;
  expected_reply: string;
  exported: boolean;
  failure_stage: string;
  helpful: boolean;
  id: number;
  latency_ms: number;
  query: string;
  reason: string;
  reply: string;
  request_id: string;
  top1_intent: string;
}

export interface FeedbackRequest {
  expected_reply?: string;
  helpful: boolean;
  query: string;
  reason?: string;
  reply: string;
  request_id: string;
  trace?: Record<string, unknown>;
}

export interface FeedbackResponse {
  feedback_id: number;
  saved: boolean;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface IndexRebuildResponse {
  chunk_count: number;
  embedding_dimension?: number;
  embedding_model?: string;
  index_name: string;
  index_version: number;
  manifest_uri?: string;
  skip_reason?: string;
  skipped?: boolean;
  switched?: boolean;
  tenant_count?: number;
}

export interface IngestionJobDetail {
  created_at: string;
  document_id: string;
  document_version_id?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  id: string;
  retry_count: number;
  stage: string;
  status: string;
  tenant_id: string;
  updated_at: string;
  warnings?: ParseWarningItem[];
}

export interface KnowledgeExportResponse {
  count: number;
  jsonl: string;
}

export interface KnowledgeItem {
  answer: string;
  base_id: string;
  category: string;
  created_at: string;
  effective_at: string;
  expired_at: string;
  id: number;
  intent: string;
  owner: string;
  question: string;
  review_note: string;
  reviewed_at: string;
  source: string;
  status: string;
  title: string;
  updated_at: string;
  version: number;
}

export interface KnowledgeItemPayload {
  answer: string;
  category: string;
  effective_at?: string;
  expired_at?: string;
  intent: string;
  owner?: string;
  question: string;
  source?: string;
  title?: string;
}

export interface KnowledgeListResponse {
  items: KnowledgeItem[];
  limit: number;
  offset: number;
  total: number;
}

export interface KnowledgePublishHistoryItem {
  action: string;
  backup_path: string;
  created_at: string;
  faiss_index_path: string;
  id: number;
  item_ids: number[];
  knowledge_path: string;
  merged_count: number;
  note: string;
  publish_id: string;
  status: string;
}

export interface KnowledgePublishHistoryResponse {
  count: number;
  items: KnowledgePublishHistoryItem[];
}

export interface KnowledgePublishResponse {
  action: string;
  backup_path: string;
  created_at: string;
  faiss_index_path: string;
  id: number;
  item_ids: number[];
  knowledge_path: string;
  merged_count: number;
  note: string;
  publish_id: string;
  status: string;
}

export interface KnowledgeReviewRequest {
  review_note?: string;
  status: string;
}

export interface ModelInfoResponse {
  adapter_enabled: boolean;
  adapter_name: string | null;
  base_model: string;
  generation_provider?: string;
  online_api_base_url_configured?: boolean;
  online_api_key_env?: string;
  online_model_name?: string;
}

export interface OpsMetricsResponse {
  accepted_count: number;
  accepted_rate: number;
  average_latency_ms: number;
  average_tokens_per_request: number;
  bad_case_count: number;
  bad_case_rate: number;
  edited_sent_count: number;
  edited_sent_rate: number;
  empty_retrieval_count: number;
  failure_count: number;
  fallback_count: number;
  human_handoff_count: number;
  human_handoff_rate: number;
  p95_latency_ms: number;
  reply_rules_hit_count: number;
  request_count: number;
  reviewed_count: number;
  source?: string;
  token_record_rate: number;
  token_recorded_count: number;
  total_completion_tokens: number;
  total_prompt_tokens: number;
  total_tokens: number;
}

export interface OrderStateRequest {
  delivery_status?: string;
  items?: Record<string, unknown>[];
  order_id: string;
  refund_status?: string;
  status: string;
  status_label?: string;
  store_name?: string;
  summary?: string;
  total?: number;
  user_id?: string;
}

export interface OrderStateResponse {
  delivery_status?: string;
  items?: Record<string, unknown>[];
  order_id: string;
  refund_status?: string;
  status: string;
  status_label?: string;
  store_name?: string;
  summary?: string;
  total?: number;
  updated_at?: string;
  user_id?: string;
}

export interface ParseWarningItem {
  code: string;
  detail?: Record<string, unknown> | null;
  message: string;
}

export interface PromptContextItemResponse {
  answer: string;
  category: string;
  display_title?: string;
  evidence_strength: string;
  evidence_summary?: string;
  intent: string;
  knowledge_id?: string;
  prompt_instruction?: string;
  question: string;
  rank: number;
  rerank_score: number;
  role: string;
  score: number;
  source?: string;
  source_answer?: string;
  source_question?: string;
  title?: string;
  updated_at?: string;
  version?: string;
}

export interface PromptPreviewResponse {
  count: number;
  mode: "vector" | "hybrid";
  prompt: string;
  prompt_context_items: RetrievalResultItem[];
  query: string;
  results: RetrievalResultItem[];
}

export interface PromptVersionItem {
  activated_at: string;
  author: string;
  change_reason: string;
  created_at: string;
  developer_prompt: string;
  effective_at: string;
  evaluation_result: string;
  id: number;
  rolled_back_from: string;
  status: string;
  system_prompt: string;
  version: string;
}

export interface PromptVersionListResponse {
  count: number;
  items: PromptVersionItem[];
}

export interface PromptVersionPayload {
  change_reason?: string;
  developer_prompt?: string;
  effective_at?: string;
  evaluation_result?: string;
  system_prompt: string;
  version?: string;
}

export interface PromptVersionStatusRequest {
  evaluation_result?: string;
  status: string;
}

export interface RagConfigResponse {
  embedding_model_name: string;
  faiss_docs_path: string;
  faiss_index_path: string;
  faiss_store_dir: string;
  min_vector_score: number;
  model_rerank_weight: number;
  reply_rules_enabled: boolean;
  reranker_model_name: string;
}

export interface RecentFeedbackResponse {
  count: number;
  items: FeedbackItem[];
}

export interface ReleaseChecklistItem {
  evidence: string;
  name: string;
  next_step?: string;
  status: string;
}

export interface ReleaseChecklistResponse {
  failed_count: number;
  items: ReleaseChecklistItem[];
  ready: boolean;
  warning_count: number;
}

export interface RetrievalResultItem {
  answer: string;
  category: string;
  direction_penalty: number;
  display_title?: string;
  evidence_strength?: string;
  evidence_summary?: string;
  intent: string;
  keyword_bonus: number;
  model_rerank_score: number;
  prompt_instruction?: string;
  question: string;
  rank: number;
  rerank_score: number;
  role?: string;
  score: number;
  source_answer?: string;
  source_question?: string;
  vector_score: number;
}

export interface RetrievalSearchRequest {
  limit?: number;
  min_score?: number;
  mode?: "vector" | "hybrid";
  query: string;
}

export interface RetrievalSearchResponse {
  count: number;
  mode: "vector" | "hybrid";
  query: string;
  results: RetrievalResultItem[];
  retrieval_path?: "chunk-index" | "seed-faq-demo";
}

export interface SearchExamplesRequest {
  keyword: string;
  limit?: number;
}

export interface SearchExamplesResponse {
  count: number;
  keyword: string;
  results: SearchResultItem[];
}

export interface SearchResultItem {
  answer: string;
  category: string;
  question: string;
}

export interface ValidationError {
  ctx?: Record<string, unknown>;
  input?: unknown;
  loc: Array<string | number>;
  msg: string;
  type: string;
}
