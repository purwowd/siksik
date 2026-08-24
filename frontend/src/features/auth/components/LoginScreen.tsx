import { type FormEvent } from "react";
import { DEMO_ACCOUNTS } from "@/shared/constants";
import { BrandLogo } from "@/shared/ui/BrandLogo";

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
    <div className="ent-login ent-login-stack">
      <div className="ent-login-stack-inner">
        <header className="ent-login-header ent-rise">
          <BrandLogo size="lg" />
          <p className="ent-login-motto">Integritas · Kompetensi · Loyalitas</p>
        </header>

        <section className="ent-login-card ent-glass ent-rise">
          <div className="ent-login-card-beam" aria-hidden />
          <h2>Masuk konsol</h2>
          {error && <div className="error-banner">{error}</div>}
          <form className="form-grid" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="login-user">Nama pengguna</label>
              <input
                id="login-user"
                value={loginUser}
                onChange={(e) => onUserChange(e.target.value)}
                autoComplete="username"
                placeholder="operator"
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
              <button className="btn btn-primary ent-btn-wide ent-btn-shine" type="submit" disabled={loginBusy}>
                {loginBusy ? "Memverifikasi…" : "Lanjutkan"}
              </button>
            </div>
          </form>
          <div className="ent-role-row" aria-label="Akun demo">
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
          <p className="login-hint login-hint-minimal">PoC lab · localhost</p>
        </section>
      </div>
    </div>
  );
}
