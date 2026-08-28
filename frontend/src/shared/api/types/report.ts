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

export interface WhatsAppReportMessage {
  account_id?: string | null;
  account_slot?: number | null;
  conversation_id: string;
  conversation_name: string;
  conversation_address?: string | null;
  conversation_type: "chat" | "group";
  message_id: string;
  direction: "IN" | "OUT" | "UNKNOWN";
  direction_evidence: string;
  actor_kind: "self" | "peer" | "group_participant" | "system" | "unknown";
  peer_jid?: string | null;
  participant_jid?: string | null;
  sender?: string | null;
  message_type: string;
  message_type_code?: number | null;
  system_action_type?: number | null;
  system_kind?: string | null;
  analysis_eligible: boolean;
  timestamp?: string | null;
  preview_text: string;
  quoted_text?: string | null;
  starred: boolean;
  revoked: boolean;
  forwarded: boolean;
  edited_at?: string | null;
  flagged: boolean;
  finding_labels: string[];
  review_statuses: string[];
}

export interface WhatsAppReportRoom {
  conversation_id: string;
  name: string;
  address?: string | null;
  type: "chat" | "group";
  finding_count: number;
  messages: WhatsAppReportMessage[];
}

export interface SessionReport {
  generated_at: string;
  product?: string;
  product_full_name?: string;
  product_tagline?: string;
  session?: {
    id: string;
    participant?: {
      full_name?: string | null;
      registration_no?: string | null;
      nik?: string | null;
      organization?: string | null;
    } | null;
    authorized_by?: string | null;
    authorized_at?: string | null;
    authorize_note?: string | null;
    recommendation?: string | null;
  };
  social_accounts: SocialReportAccount[];
  social_data: {
    total_items: number;
    truncated: boolean;
    maximum_items: number;
  };
  whatsapp_rooms: WhatsAppReportRoom[];
  whatsapp_data: {
    total_messages: number;
    total_conversations: number;
    truncated: boolean;
    maximum_messages: number;
  };
}
