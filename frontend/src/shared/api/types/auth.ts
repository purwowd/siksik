import type { Role } from "./common";

export interface AuthSession {
  token: string;
  username: string;
  role: Role;
  display_name: string;
  permissions: string[];
}
