import type { NamedCount } from "./common";

export interface YearRiskBucket {
  year: number;
  total: number;
  by_category: NamedCount[];
}

export interface RiskTimeline {
  years_back: number;
  year_from: number;
  year_to: number;
  series: YearRiskBucket[];
  older_than_window: number;
  unknown_date: number;
  trend: string;
  insight: string;
  peak_year: number | null;
  peak_count: number;
  current_year_count: number;
  prior_avg: number;
}

export interface DashboardStats {
  total_sessions: number;
  completed_sessions: number;
  active_sessions: number;
  failed_sessions?: number;
  total_findings: number;
  pending_reviews: number;
  confirmed_findings?: number;
  rejected_findings?: number;
  lulus_count?: number;
  tidak_lulus_count?: number;
  menunggu_review_count?: number;
  avg_total_ms: number;
  avg_acquire_ms: number;
  avg_analyze_ms: number;
  avg_index_ms?: number;
  throughput_peak_fps?: number;
  findings_by_category?: NamedCount[];
  findings_by_layer?: NamedCount[];
  findings_by_source?: NamedCount[];
  acquisition_methods?: NamedCount[];
  toolchain?: Record<string, boolean>;
  gpu_available?: boolean;
  risk_timeline?: RiskTimeline | null;
  timeline_session_id?: string | null;
  timeline_session_label?: string | null;
  social_traces?: NamedCount[];
  contact_unique?: number;
  contact_records?: number;
}
