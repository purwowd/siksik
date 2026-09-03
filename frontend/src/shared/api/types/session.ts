import type {
  AcquisitionMode,
  AnalysisScope,
  ReviewStatus,
  Scenario,
  SessionStatus,
} from "./common";
import type { SocialPreview } from "./social";

export interface SessionProgress {
  phase: SessionStatus;
  percent: number;
  message: string;
  files_listed: number;
  files_pulled: number;
  files_indexed: number;
  files_analyzed: number;
  findings_count: number;
  throughput_files_per_sec: number;
  live_analysis_ms?: number;
  acquisition_method?: string | null;
  hits_l1?: number;
  hits_l2?: number;
  hits_l3?: number;
  hits_l4?: number;
  hits_ocr?: number;
  hits_asr?: number;
  whatsapp_state?: string | null;
  whatsapp_ui_attempt?: number;
  whatsapp_ui_attempts?: number;
  whatsapp_messages?: number;
  whatsapp_conversations?: number;
  whatsapp_parse_skipped?: number;
  notes_state?: string | null;
  notes_flow?: string | null;
  notes_app?: string | null;
  notes_captured?: number;
  notes_skipped?: number;
  notes_warning_count?: number;
  crawl_state?: string | null;
  crawl_source?: string | null;
  crawl_target?: string | null;
  crawl_scope?: string | null;
  crawl_stage?: string | null;
  crawl_attempt?: number | null;
  crawl_attempt_state?: string | null;
  crawl_failure_class?: string | null;
  crawl_reason?: string | null;
  crawl_scroll_count?: number | null;
  crawl_screenshot_count?: number | null;
  authorized_by?: string | null;
  authorized_at?: string | null;
  authorize_note?: string | null;
  analysis_scope?: AnalysisScope | null;
  device_sources?: string[];
  social_targets?: string[];
  report_sha256?: string | null;
  authorized_confirmed_findings?: number | null;
  crawl_discovered?: number;
  crawl_duplicates?: number;
  preprocessing_state?: string;
  preprocessing_total?: number;
  preprocessing_completed?: number;
  preprocessing_skipped?: number;
  preprocessing_truncated?: number;
  preprocessing_failed?: number;
  preprocessing_preprocessor_totals?: Record<
    string,
    {
      attempted?: number;
      processed?: number;
      skipped?: number;
      truncated?: number;
      failed?: number;
      cancelled?: number;
    }
  >;
  selection_evaluated?: number;
  selection_candidates?: number;
  selection_selected?: number;
  transfer_completed?: number;
  transfer_records?: number;
  transfer_artifacts?: number;
  android_inventory_ms?: number;
  android_preprocessing_ms?: number;
  android_selection_ms?: number;
  android_transfer_ms?: number;
  recovery_state?: "scanning" | "complete" | "partial" | "unavailable";
  recovery_mode?: AcquisitionMode;
  recovery_candidates?: number;
  recovery_captured?: number;
  recovery_bytes?: number;
  recovery_warning_count?: number;
  recovery_duration_ms?: number;
  recovery_cache_sources?: number;
  recovery_cache_captured?: number;
  recovery_error_category?: string;
  ios_library_state?: string;
  ios_hidden_captured?: number;
  ios_recently_deleted_captured?: number;
  ios_cache_captured?: number;
  ios_deleted_metadata_captured?: number;
  ios_library_warning_count?: number;
}

export interface TimingBreakdown {
  t_detect_ms: number;
  t_acquire_ms: number;
  t_inventory_ms?: number;
  t_preprocess_ms?: number;
  t_selection_ms?: number;
  t_transfer_ms?: number;
  t_index_ms: number;
  t_analyze_ms: number;
  t_total_ms: number;
}

export interface ParticipantIdentity {
  full_name: string;
  registration_no: string;
  nik?: string | null;
  organization?: string | null;
}

export interface SessionSummary {
  id: string;
  device_id: string;
  device_type: import("./common").DeviceType;
  label: string;
  mode: AcquisitionMode;
  scenario: Scenario;
  status: SessionStatus;
  progress: SessionProgress;
  timing: TimingBreakdown;
  participant?: ParticipantIdentity | null;
  recommendation: string | null;
  created_at: string;
  updated_at: string;
  error: string | null;
  review_candidates?: boolean;
}

export interface Finding {
  id: string;
  session_id: string;
  file_id: string;
  source: string;
  path: string;
  category: string;
  label: string;
  confidence: number;
  layer_origin: string;
  evidence: string;
  review_status: ReviewStatus;
  created_at: string;
  media_year?: number | null;
  media_captured_at?: string | null;
  preview_path?: string | null;
  preview_text?: string | null;
  source_app?: string | null;
  social_scope?: string | null;
  presentation?: "file" | "visual" | "text" | "chat";
  social_preview?: SocialPreview | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
}
