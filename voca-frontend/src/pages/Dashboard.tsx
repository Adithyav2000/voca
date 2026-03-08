import { useState, useMemo, useEffect, useRef, useCallback, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { extractEntitiesLocal, extractEntitiesAI, type ExtractedEntity } from "../lib/entityExtract";
import { useUserProfile } from "../context/UserProfileContext";
import type { SquadPlan } from "../types/api";

function getGreeting() {
  const h = new Date().getHours();
  return h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
}

export function Dashboard() {
  const navigate = useNavigate();
  const { profile } = useUserProfile();
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const regexEntities = useMemo(() => extractEntitiesLocal(prompt), [prompt]);
  const [aiEntities, setAiEntities] = useState<ExtractedEntity[] | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchAiEntities = useCallback((text: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!text.trim() || text.trim().length < 5) {
      setAiEntities(null);
      setAiLoading(false);
      return;
    }
    setAiLoading(true);
    debounceRef.current = setTimeout(async () => {
      const result = await extractEntitiesAI(text);
      setAiEntities(result);
      setAiLoading(false);
    }, 500);
  }, []);

  useEffect(() => {
    fetchAiEntities(prompt);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [prompt, fetchAiEntities]);

  const entities = aiEntities && aiEntities.length > 0 ? aiEntities : regexEntities;

  const locationEntity = useMemo(
    () => entities.find((e) => e.label === "Location")?.value || "",
    [entities]
  );

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const p = prompt.trim();
    if (!p) {
      setError("Please describe what you'd like to book.");
      return;
    }
    if (!locationEntity) {
      setError("Please include a location in your request (e.g. 'dentist in Malvern').");
      return;
    }
    setLoading(true);
    try {
      const plan = await api<SquadPlan>("/api/sessions", {
        method: "POST",
        body: { prompt: p, user_location: locationEntity },
      });
      const cid = plan.session_id;
      if (cid) {
        navigate(`/sessions/${cid}`);
      } else {
        setError("Session created but no ID returned. Check backend.");
      }
    } catch (err) {
      const e = err as Error & { status?: number };
      if (e.status === 401) setError("Session expired or not logged in.");
      else if (e.status === 422) setError("Invalid request. Please check your input and try again.");
      else if (e.status === 400) setError(e.message || "Could not determine location. Please include a location in your request.");
      else if (e.status === 504 || e.status === 502 || e.status === 503) setError("Service temporarily unavailable. Please retry.");
      else setError(e.message || "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-8"
    >
      <header>
        <p className="text-sm text-muted-foreground">
          Good {getGreeting()},{" "}
          <span className="font-semibold text-foreground">{profile.displayName}</span>
        </p>
        <h1 className="mt-1 text-3xl font-extrabold tracking-tight text-foreground">
          New Booking
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Describe your request and 15 AI agents will call providers simultaneously.
        </p>
      </header>

      <div className="grid gap-8 lg:grid-cols-5">
        {/* Main form */}
        <div className="lg:col-span-3">
          <form onSubmit={handleSubmit} className="space-y-5">
            <section className="tile space-y-4 p-6" aria-label="Booking request">
              <label htmlFor="prompt" className="block text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Your request
              </label>
              <textarea
                id="prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="e.g., Find a dentist in Boston for tomorrow morning..."
                className="h-36 w-full resize-none rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                disabled={loading}
                aria-describedby={entities.length > 0 ? "entities" : undefined}
              />
              <p className="text-xs text-muted-foreground">
                Include a location such as "in Boston" or "near downtown."
              </p>
              {locationEntity && (
                <div className="flex items-center gap-2 rounded-lg bg-primary/5 border border-primary/20 px-3 py-2.5">
                  <span className="text-xs font-semibold text-primary">Location:</span>
                  <span className="text-sm font-medium text-foreground">{locationEntity}</span>
                </div>
              )}
              {entities.length > 0 && (
                <div id="entities" className="rounded-lg bg-muted/50 px-3 py-2.5">
                  <p className="text-xs font-medium text-muted-foreground">
                    {entities.length} {entities.length === 1 ? "entity" : "entities"} detected
                    {aiEntities ? " (AI)" : " (instant)"}
                    {aiLoading && " — refining..."}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <AnimatePresence>
                      {entities.map((ent, i) => (
                        <motion.span
                          key={`${ent.label}-${ent.value}`}
                          initial={{ scale: 0.9, opacity: 0 }}
                          animate={{ scale: 1, opacity: 1 }}
                          transition={{ delay: i * 0.04 }}
                          className="inline-flex rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary"
                        >
                          {ent.label}: {ent.value}
                        </motion.span>
                      ))}
                    </AnimatePresence>
                  </div>
                </div>
              )}
            </section>

            {error && (
              <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-4 text-base font-bold text-primary-foreground shadow-sm transition-opacity hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background disabled:opacity-50"
            >
              {loading ? (
                <>
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
                  Dispatching agents...
                </>
              ) : (
                <>
                  <Sparkles className="h-5 w-5" aria-hidden />
                  Dispatch Agents
                </>
              )}
            </button>
          </form>
        </div>

        {/* Right column */}
        <div className="lg:col-span-2 space-y-4">
          <div className="tile p-5">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">How it works</p>
            <ol className="mt-3 space-y-3">
              {[
                { n: "01", t: "You describe it", d: "Plain language with date, type, and location." },
                { n: "02", t: "15 agents call", d: "Parallel voice calls to local providers." },
                { n: "03", t: "Pick a slot", d: "Ranked by rating, distance and availability." },
                { n: "04", t: "Calendar sync", d: "Confirmed directly to Google Calendar." },
              ].map((s) => (
                <li key={s.n} className="flex gap-3">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-bold text-primary">
                    {s.n}
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-foreground">{s.t}</p>
                    <p className="text-xs text-muted-foreground">{s.d}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
          <div className="tile p-5">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Prompt tips</p>
            <ul className="mt-3 space-y-2 text-xs text-muted-foreground">
              <li className="flex gap-2"><span className="text-primary font-bold">*</span> Include a date or window ("tomorrow", "this week")</li>
              <li className="flex gap-2"><span className="text-primary font-bold">*</span> Name the provider type ("dentist", "GP", "physio")</li>
              <li className="flex gap-2"><span className="text-primary font-bold">*</span> Add a neighbourhood for closer results</li>
              <li className="flex gap-2"><span className="text-primary font-bold">*</span> Use "urgent" to prioritise earliest slots</li>
            </ul>
          </div>
        </div>
      </div>
    </motion.div>
  );
}