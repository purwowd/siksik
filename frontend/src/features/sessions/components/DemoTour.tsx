import type { Tab } from "@/shared/types";

export type TourStep = {
  id: string;
  title: string;
  body: string;
  tab?: Tab;
  target?: string;
};

export const DEMO_TOUR_STEPS: TourStep[] = [
  {
    id: "login",
    title: "Masuk konsol",
    body: "Pilih peran demo (operator, analis, pimpinan) lalu lanjutkan.",
  },
  {
    id: "intake",
    title: "Penerimaan kasus",
    body: "Hubungkan perangkat atau unggah ZIP, pilih kedalaman, jalankan akuisisi.",
    tab: "operator",
    target: ".ent-operator",
  },
  {
    id: "flow",
    title: "Alur lima tahap",
    body: "Bilah kasus di atas menunjukkan progres SPD dari pengambilan hingga hasil akhir.",
    target: ".ent-case-flow",
  },
  {
    id: "findings",
    title: "Tinjauan analis",
    body: "Konfirmasi atau tolak temuan. Gunakan J/K/C/R untuk navigasi cepat.",
    tab: "findings",
    target: ".findings-panel",
  },
  {
    id: "report",
    title: "Keputusan akhir",
    body: "Lihat laporan, unduh PDF, dan sahkan rekomendasi setelah tinjauan selesai.",
    tab: "report",
    target: ".report-stack",
  },
];

type Props = {
  step: number;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
  onJumpTab?: (tab: Tab) => void;
};

export function DemoTour({ step, onNext, onPrev, onClose, onJumpTab }: Props) {
  const current = DEMO_TOUR_STEPS[step];
  if (!current) return null;

  return (
    <aside className="ent-demo-tour ent-glass" role="complementary" aria-label="Tur demo">
      <div className="ent-demo-tour-head">
        <p className="ent-eyebrow gold">
          Tur demo · {step + 1}/{DEMO_TOUR_STEPS.length}
        </p>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Lewati
        </button>
      </div>
      <h3>{current.title}</h3>
      <p>{current.body}</p>
      <div className="ent-demo-tour-actions">
        <button type="button" className="btn btn-ghost" disabled={step === 0} onClick={onPrev}>
          Sebelumnya
        </button>
        {current.tab && onJumpTab && (
          <button type="button" className="btn btn-ghost" onClick={() => onJumpTab(current.tab!)}>
            Buka tab
          </button>
        )}
        <button type="button" className="btn btn-primary" onClick={onNext}>
          {step >= DEMO_TOUR_STEPS.length - 1 ? "Selesai" : "Lanjut"}
        </button>
      </div>
    </aside>
  );
}

export function useTourHighlight(step: number): string | undefined {
  return DEMO_TOUR_STEPS[step]?.target;
}
