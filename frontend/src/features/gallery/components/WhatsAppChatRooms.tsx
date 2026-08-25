import type { GalleryItem } from "@/shared/api/client";

type Props = {
  items: GalleryItem[];
};

type Room = {
  id: string;
  name: string;
  address?: string | null;
  type: "chat" | "group";
  items: GalleryItem[];
};

function timestampValue(item: GalleryItem): number {
  const value = item.chat?.timestamp || item.captured_at;
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function timeLabel(value: string | null | undefined): string {
  if (!value) return "Waktu tidak tersedia";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 16).replace("T", " ");
  return new Intl.DateTimeFormat("id-ID", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function messageTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    text: "Teks",
    image: "Gambar",
    video: "Video",
    audio: "Audio",
    sticker: "Stiker",
    file: "Dokumen",
    location: "Lokasi",
    system: "Sistem",
  };
  return labels[value] || value.replace(/_/g, " ");
}

function roomsFrom(items: GalleryItem[]): Room[] {
  const rooms = new Map<string, Room>();
  items.forEach((item) => {
    const chat = item.chat;
    if (!chat) return;
    const existing = rooms.get(chat.conversation_id);
    if (existing) {
      existing.items.push(item);
      return;
    }
    rooms.set(chat.conversation_id, {
      id: chat.conversation_id,
      name: chat.conversation_name,
      address: chat.conversation_address,
      type: chat.conversation_type,
      items: [item],
    });
  });
  return [...rooms.values()]
    .map((room) => ({
      ...room,
      items: [...room.items].sort((a, b) => timestampValue(a) - timestampValue(b)),
    }))
    .sort(
      (a, b) =>
        Math.max(...b.items.map(timestampValue), 0) - Math.max(...a.items.map(timestampValue), 0),
    );
}

export function WhatsAppChatRooms({ items }: Props) {
  const rooms = roomsFrom(items);
  return (
    <section className="wa-room-list" aria-label="Percakapan WhatsApp">
      {rooms.map((room) => {
        const findingCount = room.items.filter((item) => item.flagged).length;
        return (
          <article className="wa-room" key={room.id}>
            <header className="wa-room-header">
              <div className="wa-room-avatar" aria-hidden>
                WA
              </div>
              <div className="wa-room-identity">
                <strong>{room.name}</strong>
                <span>
                  {room.type === "group" ? "Grup WhatsApp" : "Chat WhatsApp"}
                  {room.address && room.address !== room.name ? ` · ${room.address}` : ""}
                </span>
              </div>
              <div className="wa-room-counts" aria-label="Ringkasan percakapan">
                <span>{room.items.length} pesan</span>
                {findingCount > 0 ? (
                  <span className="wa-room-findings">{findingCount} temuan</span>
                ) : null}
              </div>
            </header>

            <div className="wa-thread">
              {room.items.map((item) => {
                const chat = item.chat!;
                const badges = item.finding_badges ?? [];
                return (
                  <article
                    key={item.id}
                    className={`wa-message wa-message-${chat.direction.toLowerCase()} ${
                      item.flagged ? "wa-message-finding" : ""
                    }`}
                    aria-label={`${chat.direction === "OUT" ? "Pesan keluar" : "Pesan masuk"}${
                      item.flagged ? ", ditandai sebagai temuan" : ""
                    }`}
                  >
                    <div className="wa-message-bubble">
                      <div className="wa-message-head">
                        <span>
                          {chat.direction === "OUT"
                            ? "Anda"
                            : chat.sender || room.name || "Pengirim"}
                        </span>
                        {item.flagged ? (
                          <strong className="wa-finding-marker" role="status">
                            Temuan
                          </strong>
                        ) : null}
                      </div>

                      {chat.quoted_text ? (
                        <blockquote className="wa-quote">{chat.quoted_text}</blockquote>
                      ) : null}

                      <p className={chat.revoked ? "wa-message-revoked" : undefined}>
                        {chat.text ||
                          item.preview_text ||
                          `[${messageTypeLabel(chat.message_type)}]`}
                      </p>

                      {badges.length > 0 ? (
                        <div className="wa-finding-badges" aria-label="Kategori temuan">
                          {badges.map((badge) => (
                            <span key={badge}>{badge}</span>
                          ))}
                        </div>
                      ) : null}

                      <footer className="wa-message-foot">
                        <span>{messageTypeLabel(chat.message_type)}</span>
                        {chat.forwarded ? <span>Diteruskan</span> : null}
                        {chat.edited_at ? <span>Diedit</span> : null}
                        {chat.starred ? <span aria-label="Pesan berbintang">★</span> : null}
                        {chat.revoked ? <span>Dicabut</span> : null}
                        <time dateTime={chat.timestamp || item.captured_at || undefined}>
                          {timeLabel(chat.timestamp || item.captured_at)}
                        </time>
                      </footer>
                    </div>
                  </article>
                );
              })}
            </div>
          </article>
        );
      })}
    </section>
  );
}
