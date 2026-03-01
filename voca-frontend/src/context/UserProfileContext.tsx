import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";

const STORAGE_KEY = "voca-user-profile";

export interface UserProfile {
  displayName: string;
  phone: string;
  homeAddress: string;
  preferHighlyRated: boolean;
}

interface SessionResponse {
  authenticated: true;
  user_id: string;
  email: string;
  display_name: string;
  auth_provider: "demo" | "google";
}

async function getAuthDisplayName(): Promise<string> {
  try {
    const session = await api<SessionResponse>("/api/auth/session");
    return session.display_name || "User";
  } catch {
    return "User";
  }
}

const defaultProfile: UserProfile = {
  displayName: "User",
  phone: "",
  homeAddress: "",
  preferHighlyRated: true,
};

function loadProfile(): UserProfile {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<UserProfile>;
      return { ...defaultProfile, ...parsed };
    }
  } catch {
    // ignore
  }
  return { ...defaultProfile };
}

function saveProfile(p: UserProfile) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    // ignore
  }
}

interface UserProfileContextValue {
  profile: UserProfile;
  updateProfile: (partial: Partial<UserProfile>) => void;
}

const UserProfileContext = createContext<UserProfileContextValue | null>(null);

export function UserProfileProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<UserProfile>(loadProfile);

  // Load display name from auth on mount
  useEffect(() => {
    const initializeFromAuth = async () => {
      const authName = await getAuthDisplayName();
      setProfile((prev) => {
        // Only update if current displayName is still the default
        if (prev.displayName === defaultProfile.displayName || !prev.displayName) {
          const updated = { ...prev, displayName: authName };
          saveProfile(updated);
          return updated;
        }
        return prev;
      });
    };
    initializeFromAuth();
  }, []);

  const updateProfile = useCallback((partial: Partial<UserProfile>) => {
    setProfile((prev) => {
      const next = { ...prev, ...partial };
      saveProfile(next);
      return next;
    });
  }, []);

  return (
    <UserProfileContext.Provider value={{ profile, updateProfile }}>
      {children}
    </UserProfileContext.Provider>
  );
}

export function useUserProfile(): UserProfileContextValue {
  const ctx = useContext(UserProfileContext);
  if (!ctx) throw new Error("useUserProfile must be used within UserProfileProvider");
  return ctx;
}
