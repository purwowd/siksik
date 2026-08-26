import { type FormEvent } from "react";
import { DEMO_ACCOUNTS } from "@/shared/constants";
import { BrandLogo } from "@/shared/ui/BrandLogo";
import { APP_VERSION } from "@/shared/lib/appVersion";
import { LAB_UI } from "@/shared/lib/labUi";

type Props = {
  loginUser: string;
  loginPass: string;
  loginBusy: boolean;
  error: string | null;
  onUserChange: (v: string) => void;
  onPassChange: (v: string) => void;
  onPickDemo?: (user: string, pass: string) => void;
  onSubmit: (e?: FormEvent) => void;
};

export function LoginScreen({
  loginUser,
  loginPass,
  loginBusy,
  error,
  onUserChange,
  onPassChange,
  onPickDemo,
  onSubmit,
}: Props) {
  return (
    <div className="ent-login ent-login-gate">
      <div className="ent-login-split">
        <aside className="ent-login-mission">
          <BrandLogo size="lg" />
          <p className="ent-login-mission-lead">
            Konsol pemeriksaan integritas ASN — satu sesi, satu keputusan. Analis meninjau sebelum pengesahan.
          </p>
          <ol className="ent-login-flow">
            <li>
              <span>1</span>
              <div>
                <strong>Penerimaan</strong>
                <small>Operator mengambil data HP atau arsip</small>
              </div>
            </li>
            <li>
              <span>2</span>
              <div>
                <strong>Tinjauan</strong>
                <small>Analis konfirmasi atau tolak temuan</small>
              </div>
            </li>
            <li>
              <span>3</span>
              <div>
                <strong>Keputusan</strong>
                <small>Pimpinan sahkan laporan PDF</small>
              </div>
            </li>
          </ol>
        </aside>
        <section className="ent-login-card">
          <h2>Masuk</h2>
          <p className="ent-login-sub">v{APP_VERSION}</p>
          {error && <div className="error-banner">{error}</div>}
          <form className="form-grid" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="login-user">Nama pengguna</label>
              <input
                id="login-user"
                value={loginUser}
                onChange={(e) => onUserChange(e.target.value)}
                autoComplete="username"
                placeholder="Nama pengguna"
              />
            </div>
            <div className="field">
              <label htmlFor="login-pass">Kata sandi</label>
              <input
                id="login-pass"
                type="password"
                value={loginPass}
                onChange={(e) => onPassChange(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            <div className="actions">
              <button className="btn btn-primary ent-btn-wide" type="submit" disabled={loginBusy}>
                {loginBusy ? "Memverifikasi…" : "Lanjutkan"}
              </button>
            </div>
          </form>
          {LAB_UI && DEMO_ACCOUNTS.length > 0 && onPickDemo && (
            <div className="ent-role-row" aria-label="Akun lab">
              {DEMO_ACCOUNTS.map((a) => (
                <button
                  key={a.user}
                  type="button"
                  className={`ent-role ${loginUser === a.user ? "selected" : ""}`}
                  onClick={() => onPickDemo(a.user, a.pass)}
                >
                  <strong>{a.role}</strong>
                  <span>{a.user}</span>
                </button>
              ))}
            </div>
          )}
          <p className="login-hint login-hint-minimal">Hanya untuk petugas yang ditugaskan.</p>
        </section>
      </div>
    </div>
  );
}
