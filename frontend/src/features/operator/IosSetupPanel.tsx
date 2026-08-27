import type { IosSetupStatus } from "@/features/operator/iosSetupReady";

type Props = {
  status: IosSetupStatus | null;
  busy: boolean;
  code: string;
  setCode: (value: string) => void;
  error: string | null;
  showWdaSteps: boolean;
  disabled: boolean;
  onStart: () => void;
  onSubmitCode: () => void;
  onAckTrust: () => void;
  onCancel: () => void;
};

function stepClass(done: boolean, current: boolean): string {
  if (done) return "on";
  if (current) return "on";
  return "";
}

export function IosSetupPanel(p: Props) {
  const state = p.status?.state;
  const paired = !!p.status?.paired && state !== "usb_unpaired" && state !== "awaiting_usb_trust";
  const waitingCode = state === "awaiting_apple_id_code";
  const waitingTrust = state === "awaiting_developer_trust";
  const installing = state === "installing_wda";
  const failedInstall =
    state === "failed" && /ipa|wda|install|altserver/i.test(p.status?.message || "");
  const needsStart =
    !state ||
    state === "usb_unpaired" ||
    state === "needs_wda" ||
    state === "developer_mode_off" ||
    state === "failed";
  const ready = state === "ready";
  const hint = p.status?.apple_id_hint;

  return (
    <div className="ios-setup-panel" role="region" aria-labelledby="ios-setup-heading">
      <p id="ios-setup-heading" className="field-group-title">
        Siapkan iPhone
      </p>
      <ol className="ent-intake-steps ios-setup-steps" aria-label="Langkah siapkan iPhone">
        <li className={stepClass(paired || ready, state === "usb_unpaired" || state === "awaiting_usb_trust")}>
          <span>1</span> USB Trust
        </li>
        <li
          className={stepClass(
            state === "ready" ||
              state === "needs_wda" ||
              state === "installing_wda" ||
              waitingCode ||
              waitingTrust,
            state === "developer_mode_off",
          )}
        >
          <span>2</span> Developer Mode
        </li>
        {p.showWdaSteps && (
          <>
            <li className={stepClass(!!p.status?.wda_installed || ready, waitingCode || installing || failedInstall)}>
              <span>3</span> Pasang WDA
            </li>
            <li className={stepClass(ready, waitingTrust)}>
              <span>4</span> Trust profil
            </li>
          </>
        )}
      </ol>
      <p className="ios-setup-copy">
        {p.status?.message ||
          "Colok USB, unlock iPhone, ketuk Trust This Computer, lalu pastikan Developer Mode ON."}
      </p>
      {state === "developer_mode_off" && (
        <p className="field-note">
          Settings → Privacy &amp; Security → Developer Mode → ON. Restart jika diminta, lalu siapkan lagi.
        </p>
      )}
      {p.showWdaSteps && waitingCode && (
        <div className="field">
          <label htmlFor="ios-wda-code">Kode 6 digit di layar iPhone</label>
          <input
            id="ios-wda-code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="000000"
            value={p.code}
            onChange={(e) => p.setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            disabled={p.disabled || p.busy}
          />
          <small className="field-note">Bukan NIK. Kode verifikasi Apple ID saat memasang WebDriverAgent.</small>
        </div>
      )}
      {p.showWdaSteps && waitingTrust && (
        <p className="field-note">
          Settings → General → VPN &amp; Device Management
          {hint ? ` → ${hint}` : " → Apple ID yang dipakai sign"} → Trust.
        </p>
      )}
      {p.error && <div className="error-banner spaced">{p.error}</div>}
      {state === "failed" && (
        <p className="field-note">
          Jejak lengkap ada di <code>logs/setup_ios.log</code> (folder SATRIA). Tail:{" "}
          <code>tail -f logs/setup_ios.log</code>
        </p>
      )}
      <div className="actions field-actions">
        {needsStart && (
          <button className="btn btn-ghost" type="button" onClick={p.onStart} disabled={p.disabled || p.busy}>
            {p.busy ? "Menyiapkan…" : "Siapkan iPhone"}
          </button>
        )}
        {waitingCode && (
          <button
            className="btn btn-primary"
            type="button"
            onClick={p.onSubmitCode}
            disabled={p.disabled || p.busy || !/^\d{6}$/.test(p.code)}
          >
            Kirim kode
          </button>
        )}
        {waitingTrust && (
          <button className="btn btn-primary" type="button" onClick={p.onAckTrust} disabled={p.disabled || p.busy}>
            {p.busy ? "Memeriksa…" : "Sudah di-Trust, periksa lagi"}
          </button>
        )}
        {(installing || waitingCode || state === "awaiting_usb_trust") && (
          <button className="btn btn-ghost" type="button" onClick={p.onCancel} disabled={p.disabled || p.busy}>
            Batalkan siapkan
          </button>
        )}
      </div>
      {ready && <p className="field-note">iPhone siap. Tombol akuisisi dapat diklik.</p>}
      {!p.showWdaSteps && paired && (
        <p className="field-note">Cakupan HP saja tidak memasang WebDriverAgent.</p>
      )}
    </div>
  );
}
