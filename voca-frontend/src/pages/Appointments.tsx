import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import { Calendar, Clock, User, ExternalLink, FileText, CalendarDays, List } from "lucide-react";
import { api } from "../lib/api";
import { useAuditTrail } from "../context/AuditTrailContext";
import { buildGoogleCalendarLink } from "../lib/calendarLink";
import type { AppointmentsResponse, Appointment } from "../types/api";

function AppointmentRow({ a, onOpenTranscript }: { a: Appointment; onOpenTranscript: () => void }) {
  const calendarUrl = buildGoogleCalendarLink(a.provider_name, a.appointment_date, a.appointment_time, a.duration_min);

  return (
    <motion.li
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="tile flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10">
          <User className="h-5 w-5 text-primary" />
        </div>
        <div>
          <p className="font-semibold text-foreground">{a.provider_name}</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              {a.appointment_date}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {a.appointment_time} ({a.duration_min} min)
            </span>
            {a.doctor_name && (
              <span className="flex items-center gap-1">
                <User className="h-3 w-3" />
                {a.doctor_name}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {a.calendar_synced && (
          <span className="inline-flex items-center rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
            In Calendar
          </span>
        )}
        <a
          href={calendarUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors"
        >
          <ExternalLink className="h-3 w-3" />
          Google
        </a>
        <button
          type="button"
          onClick={onOpenTranscript}
          className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted transition-colors"
        >
          <FileText className="h-3 w-3" />
          Transcript
        </button>
      </div>
    </motion.li>
  );
}

function CalendarGroupView({ appointments, onOpenTranscript }: { appointments: Appointment[]; onOpenTranscript: () => void }) {
  const byDate: Record<string, Appointment[]> = {};
  appointments.forEach((a) => {
    if (!byDate[a.appointment_date]) byDate[a.appointment_date] = [];
    byDate[a.appointment_date].push(a);
  });
  const dates = Object.keys(byDate).sort();

  return (
    <div className="space-y-6">
      {dates.map((date) => (
        <div key={date}>
          <div className="mb-3 flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
              <CalendarDays className="h-4 w-4 text-primary" />
            </div>
            <h3 className="text-sm font-semibold text-foreground">{date}</h3>
          </div>
          <ul className="space-y-2">
            {byDate[date].map((a) => (
              <AppointmentRow key={a.id} a={a} onOpenTranscript={onOpenTranscript} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="tile flex flex-col items-center justify-center py-16 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10">
        <Calendar className="h-7 w-7 text-primary" />
      </div>
      <h3 className="mt-4 text-base font-semibold text-foreground">No appointments yet</h3>
      <p className="mt-1 text-sm text-muted-foreground">Confirmed bookings will appear here.</p>
      <Link to="/" className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:opacity-90">
        Book an appointment
      </Link>
    </div>
  );
}

export function Appointments() {
  const [searchParams] = useSearchParams();
  const view = searchParams.get("view") === "calendar" ? "calendar" : "list";
  const [data, setData] = useState<AppointmentsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { open: openAudit } = useAuditTrail();

  useEffect(() => {
    api<AppointmentsResponse>("/api/appointments")
      .then((res) => { setData(res); setError(null); })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <div className="h-10 w-10 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-8 text-center">
        <p className="text-destructive mb-4">{error}</p>
        <Link to="/" className="text-primary hover:underline">Back to dashboard</Link>
      </div>
    );
  }

  const appointments = data?.appointments ?? [];
  const upcomingCount = appointments.filter((a) => new Date(`${a.appointment_date}T${a.appointment_time}`) >= new Date()).length;
  const syncedCount = appointments.filter((a) => a.calendar_synced).length;

  return (
    <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground">My Appointments</h1>
          <p className="mt-1 text-sm text-muted-foreground">All confirmed bookings from VOCA sessions.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to={view === "calendar" ? "/appointments" : "/appointments?view=calendar"}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-muted transition-colors"
          >
            {view === "calendar" ? <List className="h-3.5 w-3.5" /> : <CalendarDays className="h-3.5 w-3.5" />}
            {view === "calendar" ? "List view" : "Calendar view"}
          </Link>
        </div>
      </div>

      {/* Stats */}
      {appointments.length > 0 && (
        <div className="grid grid-cols-3 gap-4">
          <div className="tile p-4 text-center">
            <p className="text-2xl font-extrabold text-foreground">{appointments.length}</p>
            <p className="text-xs text-muted-foreground">Total booked</p>
          </div>
          <div className="tile p-4 text-center">
            <p className="text-2xl font-extrabold text-foreground">{upcomingCount}</p>
            <p className="text-xs text-muted-foreground">Upcoming</p>
          </div>
          <div className="tile p-4 text-center">
            <p className="text-2xl font-extrabold text-primary">{syncedCount}</p>
            <p className="text-xs text-muted-foreground">Synced to Calendar</p>
          </div>
        </div>
      )}

      {appointments.length === 0 ? (
        <EmptyState />
      ) : view === "calendar" ? (
        <CalendarGroupView appointments={appointments} onOpenTranscript={openAudit} />
      ) : (
        <ul className="space-y-3">
          {appointments.map((a) => (
            <AppointmentRow key={a.id} a={a} onOpenTranscript={openAudit} />
          ))}
        </ul>
      )}
    </motion.div>
  );
}