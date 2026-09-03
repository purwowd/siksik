import type { GalleryItem, Paginated } from "@/shared/api/client";
import { Pagination } from "@/shared/ui/Pagination";
import { humanLabel } from "@/features/dashboard/lib/dashboardLabels";
import { MediaPreview } from "./MediaPreview";
import { WhatsAppChatRooms } from "./WhatsAppChatRooms";

type Props = {
  sessionId: string;
  data: Paginated<GalleryItem>;
  onPage: (page: number) => void;
};

function capturedLabel(value: string | null | undefined): string {
  if (!value) return "—";
  const stamp = value.replace("T", " ").replace("Z", "");
  return stamp.slice(0, 16);
}

const SOCIAL_LABELS: Record<string, string> = {
  "com.instagram.android": "Instagram",
  "com.twitter.android": "X",
  "com.facebook.katana": "Facebook",
  "com.whatsapp": "WhatsApp",
};

const SCOPE_LABELS: Record<string, string> = {
  own_profile: "Profil akun",
  own_posts: "Postingan akun",
  own_tweets: "Tweet akun",
  own_story_archive: "Arsip story",
  own_comments: "Komentar akun",
  own_replies: "Balasan akun",
  own_likes: "Aktivitas suka akun",
};

const ROLE_LABELS: Record<string, string> = {
  canonical_record: "Data terstruktur",
  source_binary: "File sumber",
  screenshot: "Snapshot visual",
  email_body: "Isi email",
  email_attachment: "Lampiran email",
  email_metadata: "Metadata email",
  canonical_message: "Pesan WhatsApp terstruktur",
  canonical_note: "Catatan Android terstruktur",
};

const RECOVERY_LABELS = {
  normal: "Data normal",
  trash: "Masih berada di Trash",
  recovered_deleted: "Recovered image · sumber asli sudah terhapus",
} as const;

function recoveryState(item: GalleryItem): keyof typeof RECOVERY_LABELS {
  return item.recovery_state || "normal";
}

function sourceLabel(item: GalleryItem): string {
  const recovery = recoveryState(item);
  if (recovery === "trash") return "Trash perangkat";
  if (recovery === "recovered_deleted") return "Recovery cache/thumbnail";
  return (item.source_app && SOCIAL_LABELS[item.source_app]) || humanLabel("source", item.source);
}

function sourcePath(item: GalleryItem): string {
  return item.source_path || item.path;
}

function AccessMeta({ item }: { item: GalleryItem }) {
  return (
    <>
      <div>Data: {capturedLabel(item.captured_at)}</div>
      <div className="finding-meta">Terakhir diakses: {capturedLabel(item.accessed_at)}</div>
      {(item.access_count ?? 0) > 0 ? (
        <div className="finding-meta">{item.access_count} kali dilihat/diputar</div>
      ) : null}
    </>
  );
}

function ItemMeta({ item }: { item: GalleryItem }) {
  const recovery = recoveryState(item);
  return (
    <>
      <div className={`finding-meta gallery-state gallery-state-${recovery}`}>
        {RECOVERY_LABELS[recovery]}
      </div>
      {item.artifact_role ? (
        <div className="finding-meta">
          {ROLE_LABELS[item.artifact_role] || item.artifact_role.replace(/_/g, " ")}
        </div>
      ) : null}
      {item.favorite ? <div className="finding-meta">Favorit perangkat</div> : null}
      {item.whatsapp_media ? (
        <div className="finding-meta">
          Media WhatsApp · {item.whatsapp_media.conversation_name || "Percakapan"} ·{" "}
          {item.whatsapp_media.direction === "OUT"
            ? "keluar"
            : item.whatsapp_media.direction === "IN"
              ? "masuk"
              : "arah tidak diketahui"}
        </div>
      ) : null}
      {item.flagged ? <div className="finding-meta gallery-flagged">Terflag</div> : null}
      {(item.finding_badges ?? []).length > 0 ? (
        <div className="gallery-finding-badges" aria-label="Kategori temuan">
          {(item.finding_badges ?? []).map((badge) => (
            <span key={badge} className="gallery-finding-badge">
              {badge}
            </span>
          ))}
        </div>
      ) : null}
      {item.social_scope ? (
        <div className="finding-meta">{SCOPE_LABELS[item.social_scope] || item.social_scope}</div>
      ) : null}
    </>
  );
}

export function GalleryList({ sessionId, data, onPage }: Props) {
  const chatItems = data.items.filter((item) => item.presentation === "chat" && item.chat);
  const regularItems = data.items.filter(
    (item) => item.presentation !== "chat" || !item.chat,
  );
  return (
    <>
      {chatItems.length > 0 ? <WhatsAppChatRooms items={chatItems} /> : null}

      {regularItems.length > 0 ? (
        <div className="findings-desktop">
          <table className="table findings-table">
            <thead>
              <tr>
                <th>Pratinjau</th>
                <th>Nama</th>
                <th>Album</th>
                <th>Sumber</th>
                <th>Waktu data / akses</th>
              </tr>
            </thead>
            <tbody>
              {regularItems.map((item) => (
                <tr key={item.id} className="hit-row">
                  <td>
                    <MediaPreview
                      sessionId={sessionId}
                      path={item.preview_path}
                      text={item.preview_text || item.label}
                      mime={item.preview_mime || item.mime}
                      presentation={item.presentation}
                      socialPreview={item.social_preview}
                      flagged={item.flagged}
                      findingBadges={item.finding_badges}
                    />
                  </td>
                  <td>
                    <strong className="finding-label">{item.label}</strong>
                    <ItemMeta item={item} />
                  </td>
                  <td>
                    <span className="finding-source">{item.album}</span>
                  </td>
                  <td>
                    <span className="finding-source">{sourceLabel(item)}</span>
                    <div className="finding-path">{sourcePath(item)}</div>
                  </td>
                  <td>
                    <AccessMeta item={item} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {regularItems.length > 0 ? (
        <div className="findings-cards" aria-label="Daftar galeri">
          {regularItems.map((item) => (
            <article
              key={item.id}
              className={`finding-card ${
                item.presentation === "text" ? "gallery-text-card" : ""
              }`}
            >
              <div className="finding-card-media">
                <MediaPreview
                  sessionId={sessionId}
                  path={item.preview_path}
                  text={item.preview_text || item.label}
                  mime={item.preview_mime || item.mime}
                  presentation={item.presentation}
                  socialPreview={item.social_preview}
                  flagged={item.flagged}
                  findingBadges={item.finding_badges}
                />
              </div>
              <div className="finding-card-body">
                <strong className="finding-label">{item.label}</strong>
                <ItemMeta item={item} />
                <div className="finding-meta">
                  <span>{item.album}</span>
                  <span>·</span>
                  <span>{sourceLabel(item)}</span>
                </div>
                <div className="finding-path">{sourcePath(item)}</div>
                <div className="evidence-body">
                  <AccessMeta item={item} />
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : null}

      <Pagination
        page={data.page}
        pages={data.pages}
        total={data.pagination_total ?? data.total}
        page_size={data.page_size}
        onPage={onPage}
        label={
          data.pagination_unit === "item_or_conversation"
            ? "Item/percakapan"
            : "Baris"
        }
      />
    </>
  );
}
