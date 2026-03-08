import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useTheme } from "../context/ThemeContext";
import { useUserProfile } from "../context/UserProfileContext";

export function UserDropdown() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { logout } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const { profile } = useUserProfile();

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 font-medium text-sm"
      >
        <span className="w-8 h-8 rounded-full bg-electric/20 dark:bg-electric/30 flex items-center justify-center text-electric font-semibold">
          {profile.displayName.charAt(0)}
        </span>
        {profile.displayName}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-56 py-1 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg z-50">
          <div className="px-3 py-2 border-b border-slate-100 dark:border-slate-800">
            <p className="font-medium text-black dark:text-white text-sm">{profile.displayName}</p>
            <p className="text-slate-500 dark:text-slate-400 text-xs">Signed in with Google</p>
          </div>
          <Link
            to="/settings"
            className="block px-3 py-2 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800"
            onClick={() => setOpen(false)}
          >
            Settings
          </Link>
          <button
            type="button"
            onClick={() => {
              toggleTheme();
              setOpen(false);
            }}
            className="w-full text-left px-3 py-2 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 flex items-center justify-between"
          >
            <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>
            <span className="text-electric">{theme === "dark" ? "☀️" : "🌙"}</span>
          </button>
          <button
            type="button"
            onClick={() => {
              void logout();
              setOpen(false);
            }}
            className="w-full text-left px-3 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-slate-50 dark:hover:bg-slate-800"
          >
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
