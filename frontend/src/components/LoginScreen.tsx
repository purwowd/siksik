import { type FormEvent } from "react";
import { DEMO_ACCOUNTS } from "../constants";

type Props = {
  loginUser: string;
  loginPass: string;
  loginBusy: boolean;
  error: string | null;
  onUserChange: (v: string) => void;
  onPassChange: (v: string) => void;
  onPickDemo: (user: string, pass: string) => void;
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
    <div className="app-shell wide login-shell">
      <div className="classify-rail slim">
        <span>Internal · lab offline</span>
        <span>PoC</span>
      </div>
      <div className="login-hero">
        <section className="login-brand">
          <p className="brand-kicker">Sistem Analisis Digital Terpadu</p>
          <h1>SADT // OPS</h1>
          <p className="tagline">
            Akses berbasis peran: Operator akuisisi, Analis verifikasi, Pimpinan
            pengesahan. Satu sesi login per konsol.
          </p>
          <div className="login-status-row">
            <span className="pill warn">Internal</span>
            <span className="pill muted">Gallery focus</span>
          </div>
          <div className="role-cards">
            {DEMO_ACCOUNTS.map((a) => (
              <button
                key={a.user}
                type="button"
                className={`role-card ${loginUser === a.user ? "selected" : ""}`}
                onClick={() => onPickDemo(a.user, a.pass)}
              >
                <strong>{a.role}</strong>
                <span>{a.user}</span>
              </button>
            ))}
          </div>
        </section>
        <section className="login-gate">
          <div className="panel-title">
            <h2>Autentikasi</h2>
            <span className="code">RBAC-01</span>
          </div>
          {error && <div className="error-banner">{error}</div>}
          <form className="form-grid" onSubmit={onSubmit}>
            <div className="field">
              <label>Username</label>
              <input
                value={loginUser}
                onChange={(e) => onUserChange(e.target.value)}
                autoComplete="username"
              />
            </div>
            <div className="field">
              <label>Password</label>
              <input
                type="password"
                value={loginPass}
                onChange={(e) => onPassChange(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            <div className="actions">
              <button className="btn btn-primary" type="submit" disabled={loginBusy}>
                {loginBusy ? "Memverifikasi…" : "Masuk konsol"}
              </button>
            </div>
          </form>
          <p className="login-hint">
            Akun demo lab — override lewat env <code>SADT_SEED_*_PASSWORD</code>. Jangan
            expose di luar localhost.
          </p>
        </section>
      </div>
      <div className="classify-rail bottom slim">
        <span>SADT · fokus galeri</span>
        <span>PoC</span>
      </div>
    </div>
  );
}
