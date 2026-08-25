export interface GalleryAlbum {
  id: string;
  label: string;
  kind: "access" | "classification" | "album";
  count: number;
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
  presentation?: "file" | "visual" | "text";
  artifact_role?: string | null;
  recovery_state?: "normal" | "trash" | "recovered_deleted";
  captured_at?: string | null;
  accessed_at?: string | null;
  access_count?: number;
  favorite?: boolean;
  flagged?: boolean;
  finding_badges?: string[];
}
