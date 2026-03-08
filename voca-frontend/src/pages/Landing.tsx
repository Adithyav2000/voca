import { useAuth } from "../context/AuthContext";

export function Landing() {
  const { authState, isLoggedIn, login } = useAuth();

  if (authState === "loading") {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh]">
        <div className="h-10 w-10 rounded-full border-2 border-slate-200 border-t-electric animate-spin" />
        <p className="mt-4 text-slate-500">Checking session…</p>
      </div>
    );
  }

  if (isLoggedIn) {
    return null;
  }

  return (
    <div className="max-w-lg mx-auto text-center py-12">
      <h1 className="text-3xl font-bold text-black mb-2">
        Call & Book for you
      </h1>
      <p className="text-xs uppercase tracking-widest text-slate-400 mb-2">
        Voice-Orchestrated Concierge for Appointments
      </p>
      <p className="text-slate-600 mb-8">
        Book appointments by voice — we call providers for you.
      </p>
      <button
        type="button"
        onClick={login}
        className="bg-electric text-white px-6 py-3 rounded-xl text-base font-semibold hover:bg-electric-dark shadow-card"
      >
        Log in with Google
      </button>
      <p className="mt-6 text-slate-500 text-sm">
        After login you can create a booking request and watch live status.
      </p>
    </div>
  );
}
