/** Kolom modul dasbor dari sesi + temuan. */

import type { DashboardStats, Finding, SessionSummary } from "@/shared/api/client";
import { REC_LULUS, REC_MENUNGGU_REVIEW, REC_TIDAK_LULUS } from "@/shared/constants";
import { methodSummary } from "@/features/dashboard/lib/dashboardLabels";
import { humanProgressMessage } from "@/shared/lib/humanProgress";

export type ModuleAvailability = "live" | "empty" | "planned" | "unavailable";

export type AnalysisModuleCard = {
  id: string;
  title: string;
  subtitle: string;
  availability: ModuleAvailability;
  availabilityLabel: string;
  metrics: { label: string; value: string }[];
  notes?: string[];
  drillDown?: boolean;
};

const GALLERY_SOURCES = new Set([
  "gallery",
  "dcim",
  "download",
  "image",
  "video",
  "media_image",
  "media_video",
  "recovered_trash",
  "recovered_cache",
  "ios_hidden",
  "ios_recently_deleted",
  "ios_recovered_cache",
  "ios_deleted_metadata",
]);

const SOCIAL_SOURCES = new Set([
  "visible_ui",
  "accessibility_visible_ui",
  "notification",
  "instagram",
  "facebook",
  "x",
  "twitter",
  "social",
]);

const EMAIL_SOURCES = new Set(["gmail", "email", "mail"]);

const WA_SOURCES = new Set(["whatsapp", "wa", "msgstore"]);

const GALLERY_CATEGORY_BUCKETS: { id: string; label: string; match: (c: string) => boolean }[] = [
  {
    id: "pornografi",
    label: "Pornografi",
    match: (c) => /ketelanjangan|nudi|porn|eksplisit|seks/i.test(c),
  },
  {
    id: "kekerasan",
    label: "Kekerasan / Radikal",
    match: (c) => /kekerasan|senjata|bom|radikal/i.test(c),
  },
  {
    id: "teror",
    label: "Radikalisme / Teror",
    match: (c) => /teror|makar/i.test(c),
  },
  {
    id: "kebencian",
    label: "Ujaran Kebencian",
    match: (c) => /benci|hate|ujaran/i.test(c),
  },
  {
    id: "anti_pemerintah",
    label: "Ideologi Anti-Pemerintah",
    match: (c) => /politik|anti.?pemerintah|presiden/i.test(c),
  },
];

export type RiskLevel = "Rendah" | "Sedang" | "Tinggi";

export function riskFromRecommendation(rec?: string | null): {
  level: RiskLevel;
  scorePct: number;
  statement: string;
} {
  if (rec === REC_TIDAK_LULUS) {
    return {
      level: "Tinggi",
      scorePct: 78,
      statement: "Perlu verifikasi & tindakan lanjutan sebelum keputusan seleksi.",
    };
  }
  if (rec === REC_MENUNGGU_REVIEW) {
    return {
      level: "Sedang",
      scorePct: 48,
      statement: "Temuan menunggu tinjauan analis — skor belum final.",
    };
  }
  if (rec === REC_LULUS) {
    return {
      level: "Rendah",
      scorePct: 12,
      statement: "Tidak ada temuan terkonfirmasi pada sesi ini.",
    };
  }
  return {
    level: "Sedang",
    scorePct: 0,
    statement: "Penilaian integritas belum tersedia untuk sesi ini.",
  };
}

export function recommendationsForLevel(level: RiskLevel): {
  id: string;
  label: string;
  active: boolean;
}[] {
  return [
    {
      id: "verify",
      label: "Verifikasi mendalam atas bukti temuan",
      active: level === "Tinggi" || level === "Sedang",
    },
    {
      id: "eligibility",
      label: "Evaluasi kelayakan terhadap kriteria seleksi",
      active: level === "Tinggi",
    },
    {
      id: "monitor",
      label: "Pantau aktivitas lanjutan bila resiko residual ada",
      active: level === "Tinggi" || level === "Sedang",
    },
  ];
}

function countBySource(
  items: { name: string; count: number }[] | undefined,
  pred: (name: string) => boolean,
): number {
  return (items ?? []).reduce((sum, i) => (pred(i.name.toLowerCase()) ? sum + i.count : sum), 0);
}

function findingsMatching(findings: Finding[] | undefined, pred: (f: Finding) => boolean): Finding[] {
  return (findings ?? []).filter(pred);
}

export function buildAnalysisModules(args: {
  session: SessionSummary | null;
  dash: DashboardStats | null;
  findings: Finding[] | undefined;
}): AnalysisModuleCard[] {
  const { session, dash, findings } = args;
  const progress = session?.progress;
  const bySource = dash?.findings_by_source;

  const pulled = progress?.files_pulled ?? 0;
  const listed = progress?.files_listed ?? 0;
  const indexed = progress?.files_indexed ?? 0;
  const transfer = progress?.transfer_records ?? progress?.transfer_completed ?? 0;

  const galleryFindings = findingsMatching(
    findings,
    (f) => GALLERY_SOURCES.has(f.source.toLowerCase()) || /gallery|dcim|image|video/i.test(f.source),
  );
  const galleryFromDash = countBySource(bySource, (n) =>
    [...GALLERY_SOURCES].some((s) => n.includes(s)),
  );
  const galleryCount = Math.max(galleryFindings.length, galleryFromDash);

  const categoryLines: string[] = [];
  if (galleryFindings.length) {
    for (const bucket of GALLERY_CATEGORY_BUCKETS) {
      const n = galleryFindings.filter((f) => bucket.match(f.category) || bucket.match(f.label)).length;
      if (n > 0) categoryLines.push(`${bucket.label}: ${n}`);
    }
    const tagged = new Set(
      galleryFindings.filter((f) =>
        GALLERY_CATEGORY_BUCKETS.some((b) => b.match(f.category) || b.match(f.label)),
      ).map((f) => f.id),
    );
    const other = galleryFindings.length - tagged.size;
    if (other > 0) categoryLines.push(`Lainnya: ${other}`);
  }

  const socialFindings = findingsMatching(findings, (f) => {
    const s = f.source.toLowerCase();
    return (
      SOCIAL_SOURCES.has(s) ||
      /instagram|facebook|twitter|\bx\b|visible_ui|social|threads|barcelona|whatsapp/i.test(s) ||
      /instagram|facebook|twitter|threads|barcelona|whatsapp/i.test(f.path || "")
    );
  });
  const socialFromDash = countBySource(
    bySource,
    (n) =>
      [...SOCIAL_SOURCES].some((s) => n.includes(s)) ||
      /instagram|facebook|twitter|visible/.test(n),
  );
  const emailFindings = findingsMatching(
    findings,
    (f) => EMAIL_SOURCES.has(f.source.toLowerCase()) || /gmail|email|mail/i.test(f.path || ""),
  );
  const emailFromDash = countBySource(bySource, (n) => /gmail|email|mail/.test(n));
  const emailCount = Math.max(emailFindings.length, emailFromDash);

  const waFindings = findingsMatching(
    findings,
    (f) => WA_SOURCES.has(f.source.toLowerCase()) || /whatsapp|msgstore/i.test(f.path || ""),
  );
  const socialTraces = dash?.social_traces ?? [];
  const socialTraceCount = socialTraces.reduce((sum, item) => sum + item.count, 0);
  const waTraces = socialTraces.filter((item) => /whatsapp/i.test(item.name));
  const waTraceCount = waTraces.reduce((sum, item) => sum + item.count, 0);
  const socialCount = Math.max(socialFindings.length, socialFromDash, socialTraceCount);
  const waCount = Math.max(waFindings.length, waTraceCount);

  const cards: AnalysisModuleCard[] = [
    {
      id: "forensic",
      title: "Data perangkat",
      subtitle: "Perangkat & email yang diizinkan",
      availability: session ? "live" : "empty",
      availabilityLabel: session ? "Data sesi aktif" : "Pilih sesi",
      metrics: [
        { label: "Berkas terdaftar", value: String(listed) },
        { label: "Berkas diambil", value: String(pulled) },
        { label: "Terindeks", value: String(indexed) },
        { label: "Rekam transfer", value: String(transfer) },
      ],
      notes: [
        progress?.acquisition_method
          ? `Metode: ${methodSummary(progress.acquisition_method)}`
          : "Metode pengambilan belum tercatat",
        `${progress?.percent ?? 0}% · ${humanProgressMessage(progress?.message) || session?.status || "—"}`,
      ],
    },
    {
      id: "gallery",
      title: "Analisis Galeri",
      subtitle: "Foto & video · kategori resiko integritas",
      availability: galleryCount > 0 ? "live" : session ? "empty" : "empty",
      availabilityLabel: galleryCount > 0 ? "Temuan terpetakan" : "Belum ada temuan galeri",
      metrics: [
        { label: "Temuan galeri", value: String(galleryCount) },
        { label: "Teks pada foto", value: String(progress?.hits_ocr ?? 0) },
        { label: "Audio", value: String(progress?.hits_asr ?? 0) },
      ],
      notes: categoryLines.length
        ? categoryLines
        : ["Klasifikasi kategori muncul setelah temuan galeri tersedia"],
      drillDown: galleryCount > 0,
    },
    {
      id: "whatsapp",
      title: "WhatsApp & Grup",
      subtitle: "Obrolan / grup · indikasi kontestasi",
      availability: waCount > 0 ? "live" : "unavailable",
      availabilityLabel:
        waCount > 0
          ? waTraceCount > 0
            ? "Jejak media WhatsApp di galeri"
            : "Temuan terkait chat terdeteksi"
          : "Modul belum aktif di runtime ini",
      metrics: [
        { label: "Temuan terkait", value: waFindings.length > 0 ? String(waFindings.length) : "—" },
        { label: "Jejak galeri", value: waTraceCount > 0 ? String(waTraceCount) : "—" },
      ],
      notes: [
        waTraceCount > 0
          ? "Jejak dari foto/video WhatsApp di galeri — bukan isi percakapan penuh."
          : "Temuan terkait chat terdeteksi pada sesi ini.",
        waCount > 0
          ? "Buka galeri album WhatsApp untuk meninjau media."
          : "Tidak ada jejak WhatsApp pada sesi ini.",
      ],
      drillDown: waFindings.length > 0,
    },
    {
      id: "social",
      title: "Media Sosial",
      subtitle: "IG · Facebook · X · Threads (jejak akun / galeri)",
      availability: socialCount > 0 ? "live" : "empty",
      availabilityLabel:
        socialCount > 0
          ? socialTraceCount > 0
            ? "Jejak galeri / crawl sosmed"
            : "Crawl / temuan sosmed"
          : "Belum ada temuan sosmed pada sesi",
      metrics: [
        { label: "Temuan", value: socialFindings.length > 0 ? String(socialFindings.length) : "0" },
        {
          label: "Jejak galeri",
          value: socialTraceCount > 0 ? String(socialTraceCount) : "0",
        },
      ],
      notes: socialTraces.length
        ? [
            socialTraces.map((item) => `${item.name}: ${item.count}`).join(" · "),
            "Jejak galeri bukan profil terverifikasi. Crawl akun milik tetap terpisah.",
          ]
        : [
            "Platform crawl akun: Instagram, Facebook, X.",
            "Screenshot Threads/Facebook/WhatsApp di galeri dihitung sebagai jejak perangkat.",
          ],
      drillDown: socialFindings.length > 0,
    },
    {
      id: "email",
      title: "Analisis Email",
      subtitle: "Kotak masuk · lampiran · metadata",
      availability: emailCount > 0 ? "live" : "empty",
      availabilityLabel: emailCount > 0 ? "Temuan email" : "Belum ada temuan email",
      metrics: [
        { label: "Temuan email", value: String(emailCount) },
      ],
      notes:
        emailCount > 0
          ? ["Gunakan tab Temuan untuk meninjau item email."]
          : ["Email dianalisa bila dikonfigurasi pada sesi."],
      drillDown: emailCount > 0,
    },
  ];

  return cards.filter((card) => {
    if (card.availability === "planned") return false;
    if (card.id === "whatsapp" && card.availability === "unavailable") return false;
    return true;
  });
}
