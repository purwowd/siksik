import { useEffect, useState } from "react";
import { api, type ParticipantIdentity, type SessionSummary } from "@/shared/api/client";

type FormState = {
  fullName: string;
  registrationNo: string;
  nik: string;
  organization: string;
};

function fromParticipant(p?: ParticipantIdentity | null): FormState {
  return {
    fullName: p?.full_name ?? "",
    registrationNo: p?.registration_no ?? "",
    nik: p?.nik ?? "",
    organization: p?.organization ?? "",
  };
}

type Props = {
  session: SessionSummary;
  canEdit: boolean;
  onSaved: (s: SessionSummary) => void;
  onError: (message: string) => void;
  onToast: (message: string, tone?: "ok" | "warn" | "info") => void;
};

export function ParticipantIdentityEditor({
  session,
  canEdit,
  onSaved,
  onError,
  onToast,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<FormState>(() => fromParticipant(session.participant));

  useEffect(() => {
    setForm(fromParticipant(session.participant));
    setEditing(false);
  }, [session.id, session.participant?.full_name, session.participant?.registration_no]);

  const ready = form.fullName.trim().length > 0 && form.registrationNo.trim().length > 0;
  const nikOk = !form.nik.trim() || /^\d{16}$/.test(form.nik.trim());

  const save = async () => {
    if (!ready || !nikOk) return;
    setBusy(true);
    onError("");
    try {
      const next = await api.updateSessionParticipant(session.id, {
        full_name: form.fullName.trim(),
        registration_no: form.registrationNo.trim(),
        nik: form.nik.trim() || null,
        organization: form.organization.trim() || null,
      });
      onSaved(next);
      setEditing(false);
      onToast("Identitas peserta diperbarui", "ok");
    } catch (e) {
      onError(e instanceof Error ? e.message : "Gagal menyimpan identitas");
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <section className="participant-card" aria-label="Identitas peserta">
        <div className="participant-card-head">
          <h3>Identitas peserta</h3>
          {canEdit && (
            <button className="btn btn-ghost" type="button" onClick={() => setEditing(true)}>
              Ubah
            </button>
          )}
        </div>
        {session.participant?.full_name ? (
          <dl className="participant-dl">
            <div>
              <dt>Nama</dt>
              <dd>{session.participant.full_name}</dd>
            </div>
            <div>
              <dt>No. peserta</dt>
              <dd>{session.participant.registration_no || "—"}</dd>
            </div>
            <div>
              <dt>NIK</dt>
              <dd>{session.participant.nik || "—"}</dd>
            </div>
            <div>
              <dt>Instansi</dt>
              <dd>{session.participant.organization || "—"}</dd>
            </div>
          </dl>
        ) : (
          <p className="field-note">Belum diisi — lengkapi sebelum mencetak laporan resmi.</p>
        )}
      </section>
    );
  }

  return (
    <section className="participant-card editing" aria-label="Ubah identitas peserta">
      <div className="participant-card-head">
        <h3>Ubah identitas peserta</h3>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="edit-participant-name">Nama lengkap</label>
          <input
            id="edit-participant-name"
            value={form.fullName}
            onChange={(e) => setForm((f) => ({ ...f, fullName: e.target.value }))}
            disabled={busy}
          />
        </div>
        <div className="field">
          <label htmlFor="edit-participant-reg">No. peserta</label>
          <input
            id="edit-participant-reg"
            value={form.registrationNo}
            onChange={(e) => setForm((f) => ({ ...f, registrationNo: e.target.value }))}
            disabled={busy}
          />
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="edit-participant-nik">NIK (16 digit, opsional)</label>
          <input
            id="edit-participant-nik"
            inputMode="numeric"
            value={form.nik}
            onChange={(e) => setForm((f) => ({ ...f, nik: e.target.value }))}
            disabled={busy}
          />
          {!nikOk && <small className="field-note">NIK harus 16 digit angka</small>}
        </div>
        <div className="field">
          <label htmlFor="edit-participant-org">Instansi (opsional)</label>
          <input
            id="edit-participant-org"
            value={form.organization}
            onChange={(e) => setForm((f) => ({ ...f, organization: e.target.value }))}
            disabled={busy}
          />
        </div>
      </div>
      <div className="actions">
        <button
          className="btn btn-primary"
          type="button"
          disabled={!ready || !nikOk || busy}
          onClick={() => void save()}
        >
          {busy ? "Menyimpan…" : "Simpan"}
        </button>
        <button
          className="btn btn-ghost"
          type="button"
          disabled={busy}
          onClick={() => {
            setForm(fromParticipant(session.participant));
            setEditing(false);
          }}
        >
          Batal
        </button>
      </div>
    </section>
  );
}
