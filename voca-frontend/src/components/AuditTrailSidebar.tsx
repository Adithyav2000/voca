import { useAuditTrail } from "../context/AuditTrailContext";

/** Real tool calls — connect to backend stream in future */
const ENTRIES: Array<{ type: "tool" | "text"; msg: string }> = [
  // TODO: Connect to /api/sessions/{id}/stream
];

export function AuditTrailSidebar() {
  const { isOpen, close } = useAuditTrail();

  if (!isOpen) return null;

  return (
    <>
      <div
        className="fixed inset-0 bg-black/20 dark:bg-black/40 z-40"
        onClick={close}
        aria-hidden
      />
      <aside
        className="tile fixed top-0 right-0 z-50 flex h-full w-80 max-w-[90vw] flex-col border-l border-border"
        aria-label="Audit Trail"
      >
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="font-semibold text-foreground">Audit Trail</h2>
          <button type="button" onClick={close} className="p-1 text-muted-foreground hover:text-foreground" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-auto p-3">
          <p className="mb-3 text-xs uppercase tracking-wider text-muted-foreground">Brain — Tool Calls</p>
          <ul className="space-y-2 font-mono text-xs text-foreground">
            {ENTRIES.map((e, i) => (
              <li key={i} className="leading-relaxed">
                {e.type === "tool" ? (
                  <span className="font-medium text-orange-500">{e.msg}</span>
                ) : (
                  <span className="text-muted-foreground">{e.msg}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </>
  );
}
