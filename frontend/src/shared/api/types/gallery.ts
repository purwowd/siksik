export interface GalleryAlbum {
  id: string;
  label: string;
  kind: "access" | "album";
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
  preview_text?: string | null;
  captured_at?: string | null;
  favorite: boolean;
}
