import { useEffect, useState } from "react";
import { motion } from "framer-motion";

type Health = { status?: string; mode?: string } | null;
type Ready = { status?: string; db?: string; redis?: string } | null;

export function Admin() {
  const [health, setHealth] = useState<Health>(null);
  const [ready, setReady] = useState<Ready>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  useEffect(() => {
    const base = (import.meta.env.VITE_API_URL as string)?.trim() || "";
    const prefix = base ? `${base}` : "";
    Promise.all([
      fetch(`${prefix}/health`, { credentials: "include" }).then((r) => r.json()).catch(() => null),
      fetch(`${prefix}/ready`, { credentials: "include" }).then((r) => r.json()).catch(() => null),
    ]).then(([h, r]) => {
      setHealth(h);
      setReady(r);
    }).catch(() => setHealthError("Could not reach backend"));
  }, []);

  return (
    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Admin Analytics</h1>
        <p className="mt-0.5 text-xs uppercase tracking-widest text-muted-foreground">
          Bird's-eye view · God Mode
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="tile p-4">
          <h2 className="mb-3 font-semibold text-foreground">System Health</h2>
          {healthError && <p className="text-red-600 text-sm">{healthError}</p>}
          {health && (
            <ul className="space-y-1 text-sm">
            <li>API: <span className="text-emerald-600">{health.status ?? "—"}</span></li>
            <li>Mode: <span className="text-primary">live</span></li>
            </ul>
          )}
          {ready && (
            <ul className="space-y-1 text-sm mt-2">
            <li>Redis: <span className="text-emerald-600">{ready.redis ?? "—"}</span></li>
            <li>DB: <span className="text-emerald-600">{ready.db ?? "—"}</span></li>
            </ul>
          )}
        </section>
      </div>

      <section className="tile p-4">
        <h2 className="mb-3 font-semibold text-foreground">Squad Analytics</h2>
        <div className="grid gap-4 text-sm sm:grid-cols-3">
          <div>
            <p className="text-muted-foreground">Active calls</p>
            <p className="text-2xl font-bold text-primary">—</p>
          </div>
          <div>
            <p className="text-muted-foreground">Twilio cost (today)</p>
            <p className="text-2xl font-bold text-foreground">$—</p>
          </div>
          <div>
            <p className="text-muted-foreground">OpenAI API (min)</p>
            <p className="text-2xl font-bold text-foreground">—</p>
          </div>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">Real data will be available once sessions are running.</p>
      </section>

      <section className="tile p-4">
        <h2 className="mb-3 font-semibold text-foreground">Active Sessions</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="py-2 text-left text-muted-foreground">Session ID</th>
                <th className="py-2 text-left text-muted-foreground">Status</th>
                <th className="py-2 text-left text-muted-foreground">User</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td colSpan={3} className="py-4 text-center text-muted-foreground">No active sessions</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </motion.div>
  );
}
