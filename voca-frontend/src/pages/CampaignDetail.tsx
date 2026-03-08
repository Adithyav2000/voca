import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { MapPin, FileText, CheckCircle2, XCircle, Activity } from "lucide-react";
import { api } from "../lib/api";
import { useSessionStream } from "../hooks/useCampaignStream";
import { useAuditTrail } from "../context/AuditTrailContext";
import type { Session, SessionResults, CallTask, ConfirmResponse } from "../types/api";

const STATUS_LABELS: Record<string, string> = {
  created: "Created",
  provider_lookup: "Finding providers",
  dialing: "Dialing",
  negotiating: "Negotiating",
  ranking: "Ranking",
  confirmed: "Confirmed",
  failed: "Failed",
  cancelled: "Cancelled",
};

function statusColor(status: string) {
  if (status === "confirmed" || status === "slot_offered") return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";
  if (status === "failed" || status === "no_answer" || status === "rejected" || status === "cancelled") return "bg-destructive/10 text-destructive border-destructive/20";
  return "bg-primary/10 text-primary border-primary/20";
}

function badgePill(status: string) {
  if (status === "confirmed" || status === "slot_offered") return "bg-emerald-500/90 text-white";
  if (status === "failed" || status === "no_answer" || status === "rejected") return "bg-destructive/90 text-white";
  return "bg-primary/90 text-white";
}

function getProviderPhotoUrl(o: CallTask): string {
  return o.photo_url || "https://images.unsplash.com/photo-1629909613654-28e377c37b09?w=120&h=80&fit=crop";
}

function mapUrlForProvider(o: CallTask): string {
  const addr = o.address || (o.provider_name ? `${o.provider_name} Boston MA` : "Boston, MA");
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(addr)}`;
}

export function SessionDetail() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<Session | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [offers, setOffers] = useState<CallTask[] | null>(null);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [confirmSuccess, setConfirmSuccess] = useState<ConfirmResponse | null>(null);
  const { open: openAudit } = useAuditTrail();

  const { data: streamData, streamError, isLive } = useSessionStream(sessionId ?? null);
  const status = streamData?.session_status ?? session?.status;
  const callTasks = streamData?.call_tasks ?? [];

  useEffect(() => {
    if (!sessionId) return;
    api<Session>(`/api/sessions/${sessionId}`)
      .then(setSession)
      .catch((e: Error & { status?: number }) => {
        if (e.status === 404) setSessionError("Session not found.");
        else setSessionError(e.message || "Failed to load session.");
      });
  }, [sessionId]);

  async function loadResults() {
    if (!sessionId) return;
    setResultsLoading(true);
    setSessionError(null);
    try {
      const res = await api<SessionResults>(`/api/sessions/${sessionId}/results`);
      setOffers(res.offers ?? []);
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 404) setSessionError("Session not found.");
      else setSessionError(err.message || "Failed to load results.");
    } finally {
      setResultsLoading(false);
    }
  }

  async function confirmOffer(callTaskId: string) {
    if (!sessionId) return;
    setConfirmError(null);
    setConfirmingId(callTaskId);
    try {
      const res = await api<ConfirmResponse>(`/api/sessions/${sessionId}/confirm`, {
        method: "POST",
        body: { call_task_id: callTaskId },
      });
      setConfirmSuccess(res);
    } catch (e) {
      const err = e as Error & { status?: number };
      setConfirmError(err.message || "Confirm failed.");
    } finally {
      setConfirmingId(null);
    }
  }

  async function cancelSession() {
    if (!sessionId) return;
    try {
      await api(`/api/sessions/${sessionId}/cancel`, { method: "POST" });
      setSession((c) => (c ? { ...c, status: "cancelled" } : null));
    } catch (e) {
      setSessionError((e as Error).message);
    }
  }

  const canCancel = status && ["created", "provider_lookup", "dialing", "negotiating", "ranking"].includes(status);
  const winningCallTaskId = confirmSuccess?.call_task_id ?? null;
  const vocaSlots = Array.from({ length: 15 }, (_, i) => ({
    index: i + 1,
    task: callTasks[i] ?? undefined,
    connected: i < callTasks.length && !!callTasks[i],
  }));

  const offeredCount = callTasks.filter((t) => t.status === "slot_offered").length;
  const _failedCount = callTasks.filter((t) => ["failed", "no_answer", "rejected"].includes(t.status)).length;
  void _failedCount;
  const activeCount = callTasks.filter((t) => ["dialing", "negotiating"].includes(t.status)).length;

  if (sessionError && !session) {
    return (
      <div className="py-8 text-center">
        <p className="text-destructive mb-4">{sessionError}</p>
        <Link to="/" className="text-primary hover:underline">Back to dashboard</Link>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Top bar */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-extrabold tracking-tight text-foreground">Session</h1>
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${statusColor(status ?? "")}`}>
              {isLive && <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />}
              {STATUS_LABELS[status ?? ""] ?? status ?? "Unknown"}
            </span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground font-mono text-xs">{sessionId}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openAudit}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-muted transition-colors"
          >
            <FileText className="h-3.5 w-3.5" />
            Audit Trail
          </button>
          {canCancel && (
            <button
              type="button"
              onClick={cancelSession}
              className="flex items-center gap-1.5 rounded-lg border border-destructive/30 px-3 py-2 text-xs font-medium text-destructive hover:bg-destructive/5 transition-colors"
            >
              Cancel session
            </button>
          )}
        </div>
      </div>

      {/* Request + stats row */}
      <div className="grid gap-4 lg:grid-cols-4">
        <div className="tile p-4 lg:col-span-2">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Your request</p>
          <p className="mt-1.5 text-sm text-foreground">{session?.query_text ?? "Loading..."}</p>
        </div>
        <div className="tile p-4 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/10">
            <CheckCircle2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
          </div>
          <div>
            <p className="text-2xl font-extrabold text-foreground">{offeredCount}</p>
            <p className="text-xs text-muted-foreground">Slots offered</p>
          </div>
        </div>
        <div className="tile p-4 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
            <Activity className="h-5 w-5 text-primary" />
          </div>
          <div>
            <p className="text-2xl font-extrabold text-foreground">{activeCount}</p>
            <p className="text-xs text-muted-foreground">Active calls</p>
          </div>
        </div>
      </div>

      {streamError && (
        <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">Stream error: {streamError}</p>
      )}
      {sessionError && (
        <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{sessionError}</p>
      )}

      {/* Main content */}
      <div className="grid gap-6 lg:grid-cols-5">
        {/* Agent grid — 3 cols */}
        <div className="lg:col-span-3">
          <h2 className="mb-3 text-sm font-semibold text-foreground">Live Agent Squad</h2>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
            {vocaSlots.map(({ index, task, connected }, i) => (
              <motion.div
                key={index}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: i * 0.03 }}
                className={`tile p-2.5 text-center transition-all ${connected && task ? "ring-1 ring-primary/30" : "opacity-70"}`}
              >
                <div className="font-mono text-[9px] font-semibold text-muted-foreground">
                  VOCA-{String(index).padStart(2, "0")}
                </div>
                {task ? (
                  <>
                    <div className="mt-1 truncate text-[11px] font-medium text-foreground leading-tight" title={task.provider_name ?? ""}>
                      {task.provider_name ?? "..."}
                    </div>
                    <div className="mt-1">
                      <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${badgePill(task.status)}`}>
                        {task.status === "slot_offered" ? "OFFERED" : task.status.toUpperCase()}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={openAudit}
                      className="mt-1.5 flex w-full items-center justify-center gap-0.5 text-[10px] text-primary hover:underline"
                    >
                      <FileText className="h-2.5 w-2.5" />
                      log
                    </button>
                    {(task.status === "slot_offered" || task.status === "negotiating") && (
                      <button
                        type="button"
                        className="mt-1 w-full rounded bg-primary/80 py-0.5 text-[9px] font-semibold text-primary-foreground hover:bg-primary"
                      >
                        INTERVENE
                      </button>
                    )}
                  </>
                ) : (
                  <div className="mt-1 text-[10px] text-muted-foreground">Standby</div>
                )}
              </motion.div>
            ))}
          </div>
        </div>

        {/* Best Matches — 2 cols */}
        <div className="lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold text-foreground">Best Matches</h2>
          <div className="tile p-4">
            <p className="text-xs text-muted-foreground">Ranked by distance, rating and availability.</p>
            {(status === "ranking" || offers !== null || confirmSuccess) ? (
              <div className="mt-3">
                {offers === null && !confirmSuccess ? (
                  <button
                    type="button"
                    onClick={loadResults}
                    disabled={resultsLoading}
                    className="w-full rounded-lg bg-primary py-2.5 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
                  >
                    {resultsLoading ? "Loading..." : "View results"}
                  </button>
                ) : (offers?.length ?? 0) === 0 && !confirmSuccess ? (
                  <p className="mt-2 text-sm text-muted-foreground">No slots offered yet.</p>
                ) : (
                  <ul className="space-y-3">
                    {offers?.map((o, i) => (
                      <motion.li
                        key={o.id}
                        initial={{ opacity: 0, x: 8 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: i * 0.05 }}
                        className={`rounded-xl border p-3 transition-all ${o.id === winningCallTaskId ? "border-primary bg-primary/5" : "border-border bg-muted/30"}`}
                      >
                        <div className="flex gap-3">
                          <img
                            src={getProviderPhotoUrl(o)}
                            alt=""
                            className="h-14 w-14 shrink-0 rounded-lg object-cover"
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-1">
                              <p className="truncate text-sm font-semibold text-foreground">{o.provider_name ?? "Unknown"}</p>
                              {o.score != null && (
                                <span className="shrink-0 text-xs font-bold text-primary">{Math.round(o.score)}pt</span>
                              )}
                            </div>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {o.offered_date} {o.offered_time && `at ${o.offered_time}`}
                              {o.offered_doctor && ` — ${o.offered_doctor}`}
                            </p>
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {!confirmSuccess && (
                                <button
                                  type="button"
                                  onClick={() => confirmOffer(o.id)}
                                  disabled={!!confirmingId}
                                  className="rounded-lg bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
                                >
                                  {confirmingId === o.id ? "Confirming..." : "Confirm & Book"}
                                </button>
                              )}
                              <a
                                href={mapUrlForProvider(o)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-foreground hover:bg-muted"
                              >
                                <MapPin className="h-3 w-3 text-primary" />
                                Map
                              </a>
                            </div>
                          </div>
                        </div>
                      </motion.li>
                    ))}
                  </ul>
                )}
                {confirmError && <p className="mt-2 text-xs text-destructive">{confirmError}</p>}
                {confirmSuccess && (
                  <div className="mt-3 rounded-xl bg-emerald-500/10 border border-emerald-200 dark:border-emerald-900 p-3">
                    <div className="flex items-center gap-2">
                      <XCircle className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                      <p className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">Booking confirmed</p>
                    </div>
                    {confirmSuccess.calendar_synced && (
                      <p className="mt-1 text-xs text-emerald-600 dark:text-emerald-400">Added to Google Calendar</p>
                    )}
                    <Link to="/appointments" className="mt-2 inline-block text-xs font-semibold text-primary hover:underline">
                      View in My Appointments
                    </Link>
                  </div>
                )}
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted-foreground">
                {isLive ? "Agents are calling providers. Results will appear here..." : "Start a booking to see matches."}
              </p>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}