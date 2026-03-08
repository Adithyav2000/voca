import { motion } from "framer-motion";
import { Mic2, Phone, Calendar, Zap, Shield } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const stats = [
  { value: "15x", label: "Parallel voice agents" },
  { value: "<90s", label: "End-to-end booking" },
  { value: "100%", label: "Calendar sync" },
];

const features = [
  { icon: Phone, title: "Calls providers for you", desc: "Up to 15 simultaneous calls so you get the earliest slot." },
  { icon: Calendar, title: "Calendar-aware", desc: "Checks your Google Calendar before holding any slot." },
  { icon: Zap, title: "One-click confirm", desc: "Ranked shortlist with photos, ratings, and travel time." },
  { icon: Shield, title: "Secure & private", desc: "OAuth 2.0 + encrypted token storage. No data sold." },
];

export function Auth() {
  const { authState, isLoggedIn, login } = useAuth();

  if (authState === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  if (isLoggedIn) return null;

  return (
    <div className="flex min-h-screen">
      {/* Left panel */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden bg-primary p-12 text-primary-foreground lg:flex">
        <div
          className="pointer-events-none absolute inset-0 opacity-10"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.15) 1px, transparent 1px)",
            backgroundSize: "48px 48px",
          }}
        />
        <div className="relative flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary-foreground/20 backdrop-blur">
            <Mic2 className="h-6 w-6" />
          </div>
          <div>
            <p className="text-lg font-bold leading-none">V.O.C.A.</p>
            <p className="text-[10px] font-medium uppercase tracking-widest opacity-75">
              Voice-Orchestrated AI
            </p>
          </div>
        </div>

        <div className="relative space-y-6">
          <h1 className="text-4xl font-extrabold leading-tight">
            Book appointments<br />while you sleep.
          </h1>
          <p className="text-base leading-relaxed opacity-80">
            VOCA sends 15 AI voice agents in parallel to call providers,
            negotiate slots, and return a ranked shortlist — all in under
            90 seconds.
          </p>
          <div className="grid grid-cols-3 gap-4">
            {stats.map((s) => (
              <div key={s.label} className="rounded-xl bg-primary-foreground/10 p-4 text-center backdrop-blur">
                <p className="text-2xl font-extrabold">{s.value}</p>
                <p className="mt-1 text-[11px] leading-tight opacity-75">{s.label}</p>
              </div>
            ))}
          </div>
          <ul className="space-y-3">
            {features.map((f) => (
              <li key={f.title} className="flex gap-3">
                <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-primary-foreground/15">
                  <f.icon className="h-3.5 w-3.5" />
                </div>
                <div>
                  <p className="text-sm font-semibold">{f.title}</p>
                  <p className="text-xs opacity-70">{f.desc}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs opacity-50">2026 V.O.C.A. All rights reserved.</p>
      </div>

      {/* Right panel */}
      <div className="flex flex-1 flex-col items-center justify-center bg-background px-8 py-12">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="w-full max-w-sm"
        >
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Mic2 className="h-5 w-5" />
            </div>
            <p className="text-lg font-bold text-foreground">V.O.C.A.</p>
          </div>

          <h2 className="text-2xl font-extrabold text-foreground">Welcome back</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Sign in with your Google account to start booking.
          </p>

          <button
            type="button"
            onClick={login}
            className="mt-8 flex w-full items-center justify-center gap-3 rounded-xl border border-border bg-card px-4 py-3.5 text-sm font-semibold text-foreground shadow-sm transition-colors hover:bg-muted"
          >
            <svg className="h-5 w-5 shrink-0" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Continue with Google
          </button>

          <div className="mt-6 flex items-center gap-3">
            <div className="h-px flex-1 bg-border" />
            <span className="text-xs text-muted-foreground">secure sign-in</span>
            <div className="h-px flex-1 bg-border" />
          </div>

          <p className="mt-6 text-center text-xs text-muted-foreground">
            No account needed — Google sign-in creates your profile automatically.
          </p>
        </motion.div>
      </div>
    </div>
  );
}
