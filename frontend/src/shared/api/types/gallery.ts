export interface GalleryAlbum {
  id: string;
  label: string;
  kind: "access" | "classification" | "album";
  count: number;
}

export interface WhatsAppChatMeta {
  account_id?: string | null;
  account_slot?: number | null;
  conversation_id: string;
  conversation_name: string;
  conversation_address?: string | null;
  conversation_type: "chat" | "group";
  peer_jid?: string | null;
  participant_jid?: string | null;
  message_id: string;
  direction: "IN" | "OUT" | "UNKNOWN";
  direction_evidence: string;
  actor_kind: "self" | "peer" | "group_participant" | "system" | "unknown";
  sender?: string | null;
  message_type: string;
  message_type_code?: number | null;
  text?: string | null;
  timestamp?: string | null;
  system_action_type?: number | null;
  system_kind?: string | null;
  analysis_eligible?: boolean;
  media_filename?: string | null;
  media_size?: number;
  media_mime_type?: string | null;
  quoted_text?: string | null;
  starred?: boolean;
  revoked?: boolean;
  forwarded?: boolean;
  edited_at?: string | null;
}

export interface WhatsAppMediaContext {
  conversation_id: string;
  conversation_name?: string | null;
  message_id: string;
  direction: "IN" | "OUT" | "UNKNOWN";
  actor_kind: "self" | "peer" | "group_participant" | "system" | "unknown";
  sender?: string | null;
  timestamp?: string | null;
  match_basis: string;
}

export interface GalleryItem {
  id: string;
  session_id: string;
  file_id: string;
  source: string;
  path: string;
  album: string;
  album_key: string;
  label: string;
  mime?: string | null;
  preview_path?: string | null;
  preview_mime?: string | null;
  preview_text?: string | null;
  source_path?: string | null;
  source_app?: string | null;
  social_scope?: string | null;
  presentation?: "file" | "visual" | "text" | "chat";
  chat?: WhatsAppChatMeta | null;
  whatsapp_media?: WhatsAppMediaContext | null;
  artifact_role?: string | null;
  recovery_state?: "normal" | "trash" | "recovered_deleted";
  captured_at?: string | null;
  accessed_at?: string | null;
  access_count?: number;
  favorite?: boolean;
  flagged?: boolean;
  finding_badges?: string[];
}
