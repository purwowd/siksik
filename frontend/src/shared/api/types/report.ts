export interface SocialReportItem {
  record_id: string;
  scope: string;
  scope_label: string;
  observed_at?: string | null;
  preview_text?: string | null;
}

export interface SocialReportAccount {
  platform: string;
  source_app: string;
  username?: string | null;
  display_name?: string | null;
  bio?: string | null;
  profile_links: string[];
  profile_metrics: {
    posts?: number | null;
    followers?: number | null;
    friends?: number | null;
    following?: number | null;
  };
  scope_counts: Record<string, number>;
  items: SocialReportItem[];
}

export interface DeviceIdentityHint {
  names: string[];
  emails: string[];
  phones: string[];
  organizations: string[];
  nik_candidates: string[];
  sources: { name: string; kind: string; label: string }[];
}

export interface SessionReport {
  generated_at: string;
  social_accounts: SocialReportAccount[];
  social_data: {
    total_items: number;
    truncated: boolean;
    maximum_items: number;
  };
  device_identity?: DeviceIdentityHint;
  metrics?: {
    contact_unique?: number;
    contact_records?: number;
    sms_by_direction?: Record<string, number>;
  };
}
