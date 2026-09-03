import type { SocialPreview } from "@/shared/api/client";

type Props = {
  preview: SocialPreview;
  flagged?: boolean;
  findingBadges?: string[];
};

const KIND_LABELS: Record<SocialPreview["kind"], string> = {
  profile: "Profil X",
  post: "Postingan X",
  reply: "Balasan X",
};

function avatarLabel(preview: SocialPreview): string {
  if (preview.kind === "profile") return "X";
  const source = preview.display_name || preview.username || "X";
  return source.trim().charAt(0).toUpperCase() || "X";
}

export function XSocialPreview({ preview, flagged = false, findingBadges = [] }: Props) {
  const hasMetrics = preview.following !== null && preview.following !== undefined
    || preview.followers !== null && preview.followers !== undefined;
  const identity = preview.display_name || preview.username || "Akun X";

  return (
    <article className={`x-social-card x-social-${preview.kind}`}>
      <header className="x-social-header">
        <div className="x-social-avatar" aria-hidden>
          {avatarLabel(preview)}
        </div>
        <div className="x-social-identity">
          <strong>{identity}</strong>
          {preview.kind !== "profile" && preview.username ? (
            <span>
              {preview.username}
              {preview.published_label ? ` · ${preview.published_label}` : ""}
            </span>
          ) : null}
        </div>
        <div className="x-social-status">
          <span className="x-social-kind">{KIND_LABELS[preview.kind]}</span>
          {flagged ? (
            <strong className="x-social-finding" role="status">
              Temuan
            </strong>
          ) : null}
        </div>
      </header>

      {preview.kind === "profile" ? (
        <div className="x-social-profile-body">
          {preview.username ? <strong>{preview.username}</strong> : null}
          {preview.birth_date ? <p>{preview.birth_date}</p> : null}
          {hasMetrics ? (
            <div className="x-social-profile-metrics" aria-label="Metrik profil X">
              {preview.following !== null && preview.following !== undefined ? (
                <span><strong>{preview.following}</strong> Mengikuti</span>
              ) : null}
              {preview.followers !== null && preview.followers !== undefined ? (
                <span><strong>{preview.followers}</strong> Pengikut</span>
              ) : null}
            </div>
          ) : null}
          {!preview.username && !preview.birth_date && !hasMetrics ? (
            <p className="x-social-unavailable">Metadata profil tidak lengkap.</p>
          ) : null}
        </div>
      ) : (
        <div className="x-social-post-body">
          <p>{preview.body || "Isi postingan tidak tersedia."}</p>
        </div>
      )}

      {findingBadges.length > 0 ? (
        <footer className="x-social-findings" aria-label="Kategori temuan">
          {[...new Set(findingBadges)].map((badge) => <span key={badge}>{badge}</span>)}
        </footer>
      ) : null}
    </article>
  );
}
