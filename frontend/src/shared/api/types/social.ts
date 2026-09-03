export interface SocialPreview {
  platform: "x";
  kind: "profile" | "post" | "reply";
  display_name?: string | null;
  username?: string | null;
  body?: string | null;
  birth_date?: string | null;
  published_label?: string | null;
  following?: string | null;
  followers?: string | null;
}
