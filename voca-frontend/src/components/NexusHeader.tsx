import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Calendar,
  Settings,
  LogOut,
  Moon,
  Sun,
  Mic2,
  ClipboardList,
  ChevronRight,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useTheme } from "../context/ThemeContext";
import { useUserProfile } from "../context/UserProfileContext";
import { useAuditTrail } from "../context/AuditTrailContext";
import { AuditTrailSidebar } from "./AuditTrailSidebar";

const navItems = [
  { to: "/", end: true, icon: LayoutDashboard, label: "New Booking" },
  { to: "/appointments", end: false, icon: Calendar, label: "Appointments" },
  { to: "/settings", end: false, icon: Settings, label: "Settings" },
];

export function Sidebar() {
  const { logout } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();
  const { profile } = useUserProfile();
  const { toggle: toggleAudit, events: auditEvents } = useAuditTrail();

  return (
    <>
      <AuditTrailSidebar events={auditEvents} />
      {/* Fixed left sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-border bg-card">
        {/* Brand */}
        <div className="flex h-16 shrink-0 items-center gap-3 border-b border-border px-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Mic2 className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-bold leading-none text-foreground">V.O.C.A.</p>
            <p className="mt-0.5 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
              Voice AI
            </p>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
            Navigation
          </p>
          <ul className="space-y-0.5">
            {navItems.map(({ to, end, icon: Icon, label }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                    }`
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>

          <div className="mt-6">
            <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              Tools
            </p>
            <ul className="space-y-0.5">
              <li>
                <button
                  type="button"
                  onClick={toggleAudit}
                  className="group flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <ClipboardList className="h-4 w-4 shrink-0" />
                  Audit Trail
                  <ChevronRight className="ml-auto h-3.5 w-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
              </li>
            </ul>
          </div>
        </nav>

        {/* User profile bottom section */}
        <div className="shrink-0 border-t border-border p-3 space-y-1">
          <button
            type="button"
            onClick={toggleTheme}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>

          <div className="flex items-center gap-3 rounded-xl px-3 py-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/20 text-sm font-bold text-primary">
              {profile.displayName.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-foreground">{profile.displayName}</p>
              <p className="text-[10px] text-muted-foreground">Signed in</p>
            </div>
            <button
              type="button"
              onClick={() => void logout()}
              title="Log out"
              className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

// Keep old export name as alias
export const VocaHeader = Sidebar;

