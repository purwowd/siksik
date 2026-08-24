export interface VisionHealth {
  pillow?: boolean;
  ffmpeg?: boolean;
  image_cap_quick?: number;
  ocr?: { enabled?: boolean; available?: boolean; backend?: string };
  media_text?: {
    enabled?: boolean;
    video_overlay_keyframes?: number;
    whisper?: boolean;
  };
  gpu_stack?: {
    enabled?: boolean;
    device?: string | null;
    backends?: Record<string, { available?: boolean; configured?: boolean }>;
  };
}

export interface HealthInfo {
  status: string;
  gpu_available: boolean;
  app: string;
  extras?: {
    toolchain?: Record<string, boolean>;
    vision?: VisionHealth;
    rbac?: boolean;
    runtime_env?: string;
    image_cap_quick?: number;
    image_cap_full?: number;
    zip_enabled?: boolean;
    zip_max_mb?: number;
    focus_scope?: string;
  };
}
