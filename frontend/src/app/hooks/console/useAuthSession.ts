import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, can, loadAuth, saveAuth, type AuthSession } from "@/shared/api/client";
import { preferredLandingTab, TAB_DEFS, TAB_PERMS } from "@/shared/constants";
import { pathFromTab } from "@/app/routes";
import type { Tab } from "@/shared/types";

type Params = {
  setError: (e: string | null) => void;
  onLogoutReset: () => void;
  urlFilterApplied: React.MutableRefObject<boolean>;
};

export function useAuthSession({ setError, onLogoutReset, urlFilterApplied }: Params) {
  const [auth, setAuth] = useState<AuthSession | null>(() => loadAuth());
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const intendedPathRef = useRef<string | null>(null);

  useEffect(() => {
    if (!auth && location.pathname !== "/") {
      intendedPathRef.current = location.pathname + location.search;
    }
  }, [auth, location.pathname, location.search]);

  const doLogin = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault();
      setLoginBusy(true);
      setError(null);
      try {
        const sessionAuth = await api.login(loginUser, loginPass);
        saveAuth(sessionAuth);
        setAuth(sessionAuth);
        urlFilterApplied.current = false;
        const dest = intendedPathRef.current;
        intendedPathRef.current = null;
        if (dest) {
          navigate(dest, { replace: true });
        } else {
          const allowed = TAB_DEFS.filter((t) => can(sessionAuth, TAB_PERMS[t.id]));
          const land = preferredLandingTab(sessionAuth, allowed) ?? allowed[0]?.id ?? "operator";
          navigate(pathFromTab(land));
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Login gagal");
      } finally {
        setLoginBusy(false);
      }
    },
    [loginUser, loginPass, navigate, setError, urlFilterApplied],
  );

  const doLogout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    saveAuth(null);
    setAuth(null);
    onLogoutReset();
    setError(null);
    navigate("/");
  }, [navigate, onLogoutReset, setError]);

  return {
    auth,
    setAuth,
    loginUser,
    loginPass,
    loginBusy,
    setLoginUser,
    setLoginPass,
    doLogin,
    doLogout,
    location,
    navigate,
  };
}

export function useAllowedTabs(auth: AuthSession | null) {
  const allowedTabs = TAB_DEFS.filter((t) => can(auth, TAB_PERMS[t.id]));
  const landingTab: Tab =
    !auth || allowedTabs.length === 0
      ? "operator"
      : (preferredLandingTab(auth, allowedTabs) ?? allowedTabs[0].id);
  return { allowedTabs, landingTab };
}
