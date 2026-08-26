import type { Role } from "@/shared/api/client";

export const ROLE_LABEL: Record<Role, string> = {
  operator: "Penerimaan",
  analis: "Analis",
  pimpinan: "Pimpinan",
  admin: "Admin",
};

export function roleLabel(role: string): string {
  return ROLE_LABEL[role as Role] ?? role;
}
