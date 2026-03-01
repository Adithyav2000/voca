import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

interface AuditTrailContextValue {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
}

const AuditTrailContext = createContext<AuditTrailContextValue | null>(null);

export function AuditTrailProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const toggle = useCallback(() => setIsOpen((v) => !v), []);

  return (
    <AuditTrailContext.Provider value={{ isOpen, open, close, toggle }}>
      {children}
    </AuditTrailContext.Provider>
  );
}

export function useAuditTrail(): AuditTrailContextValue {
  const ctx = useContext(AuditTrailContext);
  if (!ctx) throw new Error("useAuditTrail must be used within AuditTrailProvider");
  return ctx;
}
