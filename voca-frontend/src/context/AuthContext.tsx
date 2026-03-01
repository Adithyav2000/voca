import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getLoginUrl } from "../lib/api";

type AuthState = "loading" | "logged_in" | "logged_out";

interface SessionResponse {
  authenticated: true;
  user_id: string;
  email: string;
  display_name: string;
  auth_provider: "demo" | "google";
}

interface AuthContextValue {
  authState: AuthState;
  isLoggedIn: boolean;
  login: () => void;
  logout: () => Promise<void>;
  refreshAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authState, setAuthState] = useState<AuthState>("loading");

  const refreshAuth = useCallback(async () => {
    try {
      await api<SessionResponse>("/api/auth/session");
      setAuthState("logged_in");
    } catch (e) {
      const err = e as { status?: number };
      if (err.status === 401) {
        setAuthState("logged_out");
      } else {
        setAuthState("logged_out");
      }
    }
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  const login = useCallback(() => {
    window.location.href = getLoginUrl();
  }, []);

  const logout = useCallback(async () => {
    try {
      await api<{ status: "ok" }>("/api/auth/logout", { method: "POST" });
    } catch {
      // Ignore network errors here; we still want to reset client state.
    } finally {
      setAuthState("logged_out");
      window.location.href = "/auth";
    }
  }, []);

  const value: AuthContextValue = {
    authState,
    isLoggedIn: authState === "logged_in",
    login,
    logout,
    refreshAuth,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
