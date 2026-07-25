import { useEffect, useState } from "react";
import { fetchMediaBlobUrl } from "../api";
import { enqueueMediaTask } from "../lib/mediaFetchQueue";

const IMG_EXT = /\.(jpe?g|png|gif|webp|bmp)$/i;
const VID_EXT = /\.(mp4|mov|webm|mkv|3gp|avi)$/i;

export function MediaPreview({
  sessionId,
  path,
  text,
}: {
  sessionId: string;
  path: string;
  text?: string | null;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const isImg = IMG_EXT.test(path);
  const isVid = VID_EXT.test(path);
  const supported = isImg || isVid;
  const previewText = (text || "").replace(/\s+/g, " ").trim().slice(0, 320);

  useEffect(() => {
    let revoke: string | null = null;
    let cancelled = false;
    setUrl(null);
    setFailed(false);
    setLoading(false);
    if (!sessionId || !path || !supported) return;

    setLoading(true);
    enqueueMediaTask(() => fetchMediaBlobUrl(sessionId, path))
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        revoke = u;
        setUrl(u);
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
  }, [sessionId, path, supported]);

  if (!supported) {
    return (
      <div className="media-preview skeleton muted-preview" title={previewText || "Berkas"}>
        {previewText || "Berkas"}
      </div>
    );
  }
  if (loading) {
    return (
      <div className="media-preview skeleton" aria-busy="true" aria-label="Memuat pratinjau">
        <span />
      </div>
    );
  }
  if (failed) {
    return (
      <div className="media-preview skeleton muted-preview" title={previewText || "Gagal muat"}>
        {previewText || "Gagal muat"}
      </div>
    );
  }
  if (!url) return <div className="media-preview skeleton" />;

  if (isImg) {
    return (
      <a className="media-preview" href={url} target="_blank" rel="noreferrer">
        <img
          src={url}
          alt={`Pratinjau ${path.split("/").pop() || "media"}`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      </a>
    );
  }
  return (
    <div className="media-preview video">
      <video src={url} controls preload="metadata" onError={() => setFailed(true)} />
    </div>
  );
}
