import { useAuditTrail } from "../context/AuditTrailContext";
import type { AuditEvent } from "../types/api";

function eventIcon(event: string) {
  if (event === "tool_call") return "🔧";
  if (event === "tool_result") return "✅";
  if (event === "ai_said") return "🤖";
  if (event === "receptionist_said") return "📞";
  if (event === "call_started") return "📲";
  return "•";
}

function eventColor(event: string) {
  if (event === "tool_call" || event === "tool_result") return "text-orange-500";
  if (event === "ai_said") return "text-blue-500 dark:text-blue-400";
  if (event === "receptionist_said") return "text-emerald-600 dark:text-emerald-400";
  return "text-muted-foreground";
}

export function AuditTrailSidebar({ events }: { events?: AuditEvent[] }) {
  const { isOpen, close } = useAuditTrail();

  if (!isOpen) return null;

  const entries = events ?? [];

  return (
    <>
      <div
        className="fixed inset-0 bg-black/20 dark:bg-black/40 z-40"
        onClick={close}
        aria-hidden
      />
      <aside
        className="tile fixed top-0 right-0 z-50 flex h-full w-96 max-w-[90vw] flex-col border-l border-border"
        aria-label="Audit Trail"
      >
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="font-semibold text-foreground">Audit Trail</h2>
          <span className="text-xs text-muted-foreground">{entries.length} events</span>
          <button type="button" onClick={close} className="p-1 text-muted-foreground hover:text-foreground" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-auto p-3">
          {entries.length === 0 ? (
            <p className="text-xs text-muted-foreground mt-4 text-center">
              No events yet. Audit events will appear here as calls progress.
            </p>
          ) : (
            <ul className="space-y-2 font-mono text-xs">
              {entries.map((e, i) => (
                <li key={i} className="leading-relaxed border-b border-border/50 pb-2">
                  <div className="flex items-start gap-2">
                    <span>{eventIcon(e.event)}</span>
                    <div className="min-w-0 flex-1">
                      <span className={`font-semibold ${eventColor(e.event)}`}>
                        {e.event}
                      </span>
                      <span className="ml-2 text-muted-foreground text-[10px]">
                        {new Date(e.ts).toLocaleTimeString()}
                      </span>
                      {e.detail && (
                        <p className="mt-0.5 text-foreground break-words">{e.detail}</p>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}
