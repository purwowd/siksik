/** Judul halaman fitur. */

export function PanelTitle({ title }: { title: string }) {
  return (
    <div className="panel-title">
      <h2>{title}</h2>
    </div>
  );
}
