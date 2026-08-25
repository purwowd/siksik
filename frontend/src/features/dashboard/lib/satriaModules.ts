/** Aggregate SPD module columns from session + findings (honest stubs when empty). */

import type { DashboardStats, Finding, SessionSummary } from "@/shared/api/client";
import { REC_LULUS, REC_MENUNGGU_REVIEW, REC_TIDAK_LULUS } from "@/shared/constants";

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
const BROWSER_SOURCES = new Set([
  "browser_history_full",
  "browser_history_partial",
  "browser_history",
]);

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
    match: (c) => /kekerasan|senjata|bom|radikal|incitement|extremism/i.test(c),
  },
  {
    id: "teror",
    label: "Radikalisme / Teror",
    match: (c) => /teror|makar/i.test(c),
  },
  {
    id: "kebencian",
    label: "Ujaran Kebencian",
    match: (c) => /benci|hate|ujaran|hate_speech/i.test(c),
  },
  {
    id: "anti_pemerintah",
    label: "Konten Politik",
    match: (c) => /politik|political|campaign|demonstration|anti.?pemerintah|presiden/i.test(c),
  },
  {
    id: "lgbt_content",
    label: "LGBT text/flag",
    match: (c) => /lgbt|pride|transgender.?flag/i.test(c),
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
      /instagram|facebook|twitter|\bx\b|visible_ui|social/i.test(s) ||
      /instagram|facebook|twitter/i.test(f.path || "")
    );
  });
  const socialFromDash = countBySource(
    bySource,
    (n) =>
      [...SOCIAL_SOURCES].some((s) => n.includes(s)) ||
      /instagram|facebook|twitter|visible/.test(n),
  );
  const socialCount = Math.max(socialFindings.length, socialFromDash);

  const emailFindings = findingsMatching(
    findings,
    (f) => EMAIL_SOURCES.has(f.source.toLowerCase()) || /gmail|email|mail/i.test(f.path || ""),
  );
  const emailFromDash = countBySource(bySource, (n) => /gmail|email|mail/.test(n));
  const emailCount = Math.max(emailFindings.length, emailFromDash);

  const browserFindings = findingsMatching(
    findings,
    (f) =>
      BROWSER_SOURCES.has(f.source.toLowerCase()) || /browser_history/i.test(f.path || ""),
  );
  const browserFromDash = countBySource(bySource, (n) => /browser_history|chrome_cdp/.test(n));
  const browserCount = Math.max(browserFindings.length, browserFromDash);

  const waFindings = findingsMatching(
    findings,
    (f) => WA_SOURCES.has(f.source.toLowerCase()) || /whatsapp|msgstore/i.test(f.path || ""),
  );
  const waState = progress?.whatsapp_state;
  const waMessages = progress?.whatsapp_messages ?? 0;
  const waConversations = progress?.whatsapp_conversations ?? 0;
  const waLive = waState === "complete";

  return [
    {
      id: "forensic",
      title: "Forensik Pengambilan Data",
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
          ? `Metode: ${progress.acquisition_method}`
          : "Metode akuisisi belum tercatat",
        `${progress?.percent ?? 0}% · ${progress?.message || session?.status || "—"}`,
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
        { label: "Hit OCR", value: String(progress?.hits_ocr ?? 0) },
        { label: "Hit ASR", value: String(progress?.hits_asr ?? 0) },
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
      availability: waLive || waFindings.length > 0 ? "live" : waState === "parse_unavailable" ? "unavailable" : "empty",
      availabilityLabel:
        waFindings.length > 0
          ? "Temuan pesan siap direview"
          : waLive
            ? "Akuisisi & parsing selesai"
            : waState === "parse_unavailable"
              ? "Backup diperoleh · parser belum cocok"
              : waState === "not_installed"
                ? "WhatsApp tidak terpasang"
                : "Belum ada data WhatsApp",
      metrics: [
        { label: "Pesan", value: String(waMessages) },
        { label: "Percakapan", value: String(waConversations) },
        { label: "Temuan terkait", value: String(waFindings.length) },
      ],
      notes: [
        "Native SATRIA: UI backup WhatsApp → Crypt15 → canonical pesan → analisis/review.",
        `UI automator: ${progress?.whatsapp_ui_attempt ?? 0}/${progress?.whatsapp_ui_attempts ?? 4} percobaan.`,
      ],
      drillDown: waFindings.length > 0,
    },
    {
      id: "social",
      title: "Media Sosial",
      subtitle: "IG · Facebook · X (akun milik)",
      availability: socialCount > 0 ? "live" : "empty",
      availabilityLabel:
        socialCount > 0 ? "Crawl / temuan sosmed" : "Belum ada temuan sosmed pada sesi",
      metrics: [
        { label: "Temuan / sinyal", value: String(socialCount) },
        { label: "TikTok / YouTube / Threads", value: "Direncanakan" },
      ],
      notes: [
        "Platform yang didukung runtime: Instagram, Facebook, X (akun milik).",
        "TikTok, YouTube, dan Threads belum tersedia.",
      ],
      drillDown: socialCount > 0,
    },
    {
      id: "email",
      title: "Analisis Email",
      subtitle: "Kotak masuk · lampiran · metadata",
      availability: emailCount > 0 ? "live" : "empty",
      availabilityLabel: emailCount > 0 ? "Temuan email" : "Belum ada temuan email",
      metrics: [
        { label: "Temuan email", value: String(emailCount) },
        {
          label: "Periode",
          value: session?.mode === "full" ? "FULL (~6 bln)" : "QUICK (~3 bln)",
        },
      ],
      notes:
        emailCount > 0
          ? ["Gunakan tab Temuan untuk meninjau item email."]
          : ["Akuisisi Gmail/email aktif bila dikonfigurasi pada sesi."],
      drillDown: emailCount > 0,
    },
    {
      id: "browser",
      title: "Riwayat Browser",
      subtitle: "Chrome · URL lengkap · jejak sebagian",
      availability: browserCount > 0 ? "live" : session ? "empty" : "empty",
      availabilityLabel:
        browserCount > 0 ? "Temuan riwayat browser" : "Belum ada temuan riwayat browser",
      metrics: [
        { label: "Temuan browser", value: String(browserCount) },
        {
          label: "Periode",
          value: session?.mode === "full" ? "FULL (~6 bln)" : "QUICK (~3 bln)",
        },
      ],
      notes:
        browserCount > 0
          ? ["Galeri memisahkan URL lengkap dan jejak sebagian."]
          : ["CDP Chrome aktif pada akuisisi Android bila DevTools tersedia."],
      drillDown: browserCount > 0,
    },
    {
      id: "tiktok",
      title: "TikTok & Short Video",
      subtitle: "YouTube · Threads · direncanakan",
      availability: "planned",
      availabilityLabel: "Modul PoC — belum aktif",
      metrics: [
        { label: "TikTok", value: "Direncanakan" },
        { label: "YouTube / Threads", value: "Direncanakan" },
      ],
      notes: [
        "TikTok / YouTube / Threads: modul direncanakan — tidak ada crawl aktif pada PoC ini.",
        "Angka kosong bukan indikasi hasil bersih.",
      ],
      drillDown: false,
    },
  ];
}
