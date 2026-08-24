import { useEffect, useState } from "react";
import {
  fetchMediaBlobUrl,
  fetchMediaText,
  issueMediaTicket,
  ticketedMediaUrl,
} from "../api";
import { enqueueMediaTask } from "../lib/mediaFetchQueue";

const IMG_EXT = /\.(jpe?g|png|gif|webp|bmp|heic|heif)$/i;
const VID_EXT = /\.(mp4|mov|webm|mkv|3gp|avi|m4v)$/i;
const AUDIO_EXT = /\.(mp3|m4a|aac|wav|ogg|opus|flac|amr)$/i;
const HTML_EXT = /\.(html?|eml)$/i;
const JSON_EXT = /\.json$/i;
const TEXT_EXT = /\.(txt|csv|xml|log|vcf|vcard)$/i;
const PDF_EXT = /\.pdf$/i;

function compact(value: string, limit: number): string {
  return value.replace(/\u0000/g, " ").replace(/\s+/g, " ").trim().slice(0, limit);
}

function collectReadable(value: unknown, key: string, output: string[], depth: number): void {
  if (depth > 6 || output.join("\n").length > 50_000) return;
  if (typeof value === "string") {
    const ignored = /(^|_)(id|hash|sha256|fingerprint|locator|path)$/i.test(key);
    const cleaned = compact(value, 12_000);
    if (!ignored && cleaned && !/^[0-9a-f]{32,}$/i.test(cleaned)) output.push(cleaned);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectReadable(item, key, output, depth + 1));
    return;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const priority = [
      "subject",
      "from",
      "sender",
      "to",
      "date",
      "normalized_text",
      "plain_text",
      "body",
      "text",
      "snippet",
      "content_description",
      "display_name",
    ];
    const visited = new Set<string>();
    priority.forEach((name) => {
      if (name in record) {
        visited.add(name);
        collectReadable(record[name], name, output, depth + 1);
      }
    });
    Object.entries(record).forEach(([name, item]) => {
      if (!visited.has(name)) collectReadable(item, name, output, depth + 1);
    });
  }
}

function primaryReadable(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const output: string[] = [];
  ["subject", "from", "sender", "to", "date"].forEach((name) => {
    collectReadable(record[name], name, output, 0);
  });
  const primary = ["normalized_text", "plain_text", "body", "text", "snippet"].find(
    (name) => typeof record[name] === "string" && compact(record[name] as string, 50_000),
  );
  if (primary) collectReadable(record[primary], primary, output, 0);
  const metadata = record.metadata;
  if (metadata && typeof metadata === "object" && !Array.isArray(metadata)) {
    const values = metadata as Record<string, unknown>;
    ["profile_display_name", "profile_username", "profile_bio", "profile_links"].forEach(
      (name) => collectReadable(values[name], name, output, 0),
    );
  }
  const unique = [...new Set(output.map((item) => compact(item, 12_000)).filter(Boolean))];
  return unique.length > 0 ? unique.join("\n\n").slice(0, 50_000) : null;
}

function readableText(raw: string, json: boolean): string {
  if (!json) return raw.replace(/\u0000/g, " ").trim().slice(0, 50_000);
  try {
    const parsed = JSON.parse(raw) as unknown;
    const primary = primaryReadable(parsed);
    if (primary) return primary;
    const values: string[] = [];
    collectReadable(parsed, "", values, 0);
    const unique = [...new Set(values.map((value) => compact(value, 12_000)).filter(Boolean))];
    return unique.join("\n\n").slice(0, 50_000) || "Tidak ada teks yang dapat ditampilkan.";
  } catch {
    return "Format data tidak dapat dibaca sebagai teks terstruktur.";
  }
}

export function MediaPreview({
  sessionId,
  path,
  text,
  mime,
  presentation = "file",
}: {
  sessionId: string;
  path?: string | null;
  text?: string | null;
  mime?: string | null;
  presentation?: "file" | "visual" | "text";
}) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [contentUrl, setContentUrl] = useState<string | null>(null);
  const [contentText, setContentText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  const mediaPath = path || "";
  const forceText = presentation === "text";
  const forceVisual = presentation === "visual";
  const mimeValue = (mime || "").toLowerCase();
  const isImg = !forceText && (mimeValue.startsWith("image/") || IMG_EXT.test(mediaPath));
  const isVid = !forceText && (mimeValue.startsWith("video/") || VID_EXT.test(mediaPath));
  const isAudio = !forceText && (mimeValue.startsWith("audio/") || AUDIO_EXT.test(mediaPath));
  const isHtml = !forceText && (mimeValue === "text/html" || HTML_EXT.test(mediaPath));
  const isJson = mimeValue.includes("json") || JSON_EXT.test(mediaPath);
  const isText = forceText || isJson || mimeValue.startsWith("text/") || TEXT_EXT.test(mediaPath);
  const isPdf = !forceText && (mimeValue === "application/pdf" || PDF_EXT.test(mediaPath));
  const previewText = compact(text || "", 320);
  const fileName = mediaPath.split("/").pop() || "Berkas";

  useEffect(() => {
    let revoke: string | null = null;
    let cancelled = false;
    setImageUrl(null);
    setContentUrl(null);
    setContentText(null);
    setFailed(false);
    setLoading(false);
    setModalOpen(false);
    if (!sessionId || !mediaPath || !isImg) return;
    setLoading(true);
    enqueueMediaTask(() => fetchMediaBlobUrl(sessionId, mediaPath))
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        revoke = url;
        setImageUrl(url);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [sessionId, mediaPath, isImg]);

  useEffect(() => {
    if (!modalOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setModalOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [modalOpen]);

  async function openPreview(): Promise<void> {
    if (!mediaPath) return;
    setLoading(true);
    setFailed(false);
    try {
      if (isText && !isHtml) {
        const raw = await fetchMediaText(sessionId, mediaPath);
        setContentText(readableText(raw, isJson));
      } else {
        const issued = await issueMediaTicket(sessionId, mediaPath);
        setContentUrl(ticketedMediaUrl(sessionId, mediaPath, issued.ticket));
      }
      setModalOpen(true);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  if (forceVisual && !isImg) {
    return (
      <div className="media-preview muted-preview visual-missing-preview">
        Snapshot visual Instagram tidak tersedia
      </div>
    );
  }

  if (isImg) {
    if (loading) {
      return (
        <div className="media-preview skeleton" aria-busy="true" aria-label="Memuat pratinjau">
          <span />
        </div>
      );
    }
    if (!imageUrl || failed) {
      return <div className="media-preview muted-preview">{previewText || "Gambar"}</div>;
    }
    return (
      <a className="media-preview" href={imageUrl} target="_blank" rel="noreferrer">
        <img
          src={imageUrl}
          alt={`Pratinjau ${fileName}`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      </a>
    );
  }

  const kind = isVid
    ? "Video"
    : isAudio
      ? "Audio"
      : isHtml
        ? "Email"
        : isJson || isText
          ? "Teks"
          : isPdf
            ? "PDF"
            : "Berkas";

  return (
    <>
      <button
        type="button"
        className={`media-preview media-preview-action ${forceText ? "text-only-preview" : ""}`}
        onClick={() => void openPreview()}
        disabled={loading || !mediaPath}
        title={`Buka ${fileName}`}
      >
        <span className="media-preview-kind">{loading ? "Memuat" : kind}</span>
        <span className="media-preview-summary">{previewText || fileName}</span>
      </button>
      {failed ? <span className="media-preview-error">Preview gagal dimuat</span> : null}
      {modalOpen ? (
        <div className="media-modal-backdrop" role="presentation" onClick={() => setModalOpen(false)}>
          <section
            className="media-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Pratinjau ${fileName}`}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="media-modal-header">
              <strong>{fileName}</strong>
              <button type="button" onClick={() => setModalOpen(false)} aria-label="Tutup">
                Tutup
              </button>
            </header>
            <div className="media-modal-body">
              {isVid && contentUrl ? <video src={contentUrl} controls autoPlay preload="metadata" /> : null}
              {isAudio && contentUrl ? <audio src={contentUrl} controls autoPlay preload="metadata" /> : null}
              {(isHtml || isPdf) && contentUrl ? (
                <iframe src={contentUrl} title={`Isi ${fileName}`} sandbox="" />
              ) : null}
              {contentText ? <pre className="media-text-preview">{contentText}</pre> : null}
              {!isVid && !isAudio && !isHtml && !isPdf && contentUrl ? (
                <div className="media-file-open">
                  <p>{previewText || "Pratinjau langsung tidak tersedia untuk format ini."}</p>
                  <a href={contentUrl} target="_blank" rel="noreferrer">
                    Buka berkas
                  </a>
                </div>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
