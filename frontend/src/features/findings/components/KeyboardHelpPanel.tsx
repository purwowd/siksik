export function KeyboardHelpPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;

  return (
    <div className="ent-kbd-overlay" role="dialog" aria-modal="true" aria-labelledby="kbd-title">
      <div className="ent-kbd-panel">
        <div className="ent-kbd-head">
          <div>
            <h2 id="kbd-title">Navigasi cepat temuan</h2>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Tutup">
            Tutup
          </button>
        </div>
        <ul className="ent-kbd-list">
          <li>
            <kbd>J</kbd> / <kbd>↓</kbd>
            <span>Temuan berikutnya</span>
          </li>
          <li>
            <kbd>K</kbd> / <kbd>↑</kbd>
            <span>Temuan sebelumnya</span>
          </li>
          <li>
            <kbd>C</kbd>
            <span>Konfirmasi temuan terpilih</span>
          </li>
          <li>
            <kbd>R</kbd>
            <span>Tolak temuan terpilih</span>
          </li>
          <li>
            <kbd>?</kbd>
            <span>Buka / tutup bantuan ini</span>
          </li>
        </ul>
        <p className="login-hint">Hanya aktif saat fokus tidak di kolom input.</p>
      </div>
      <button type="button" className="ent-kbd-backdrop" tabIndex={-1} aria-hidden onClick={onClose} />
    </div>
  );
}
