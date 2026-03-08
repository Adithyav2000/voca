import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { User, Home, Phone, Star, Sun } from "lucide-react";
import { useUserProfile } from "../context/UserProfileContext";
import { useTheme } from "../context/ThemeContext";

export function Settings() {
  const { profile, updateProfile } = useUserProfile();
  const { theme } = useTheme();
  const [displayName, setDisplayName] = useState(profile.displayName);
  const [phone, setPhone] = useState(profile.phone);
  const [homeAddress, setHomeAddress] = useState(profile.homeAddress);
  const [preferHighlyRated, setPreferHighlyRated] = useState(profile.preferHighlyRated);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setDisplayName(profile.displayName);
    setPhone(profile.phone);
    setHomeAddress(profile.homeAddress);
    setPreferHighlyRated(profile.preferHighlyRated);
  }, [profile]);

  function handleSave() {
    updateProfile({ displayName, phone, homeAddress, preferHighlyRated });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  const inputCls = "w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 transition-colors";

  return (
    <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="space-y-8">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight text-foreground">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">Manage your profile and preferences.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left: nav pills */}
        <div className="lg:col-span-1 space-y-1">
          <div className="rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 flex items-center gap-2.5">
            <User className="h-4 w-4 text-primary" />
            <span className="text-sm font-semibold text-primary">Profile</span>
          </div>
          <div className="rounded-xl px-4 py-3 flex items-center gap-2.5 text-muted-foreground hover:bg-muted cursor-default transition-colors">
            <Sun className="h-4 w-4" />
            <span className="text-sm font-medium">Appearance</span>
          </div>
        </div>

        {/* Right: content */}
        <div className="lg:col-span-2 space-y-5">
          {/* Profile */}
          <section className="tile p-6 space-y-5">
            <div>
              <h2 className="text-base font-bold text-foreground">Profile</h2>
              <p className="text-xs text-muted-foreground mt-0.5">Used for greetings and booking context.</p>
            </div>
            <div>
              <label className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <User className="h-3 w-3" />
                Display name
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className={inputCls}
              />
            </div>
            <div>
              <label className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <Phone className="h-3 w-3" />
                Phone number
              </label>
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+1 555 000 0000"
                className={inputCls}
              />
            </div>
            <div>
              <label className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <Home className="h-3 w-3" />
                Home address
              </label>
              <input
                type="text"
                value={homeAddress}
                onChange={(e) => setHomeAddress(e.target.value)}
                placeholder="Boston, MA"
                className={inputCls}
              />
              <p className="mt-1 text-xs text-muted-foreground">Used for proximity scoring during ranking.</p>
            </div>
            <div className="flex items-start gap-3 rounded-xl bg-muted/50 p-4">
              <input
                type="checkbox"
                id="prefer"
                checked={preferHighlyRated}
                onChange={(e) => setPreferHighlyRated(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-border text-primary accent-primary focus:ring-primary"
              />
              <label htmlFor="prefer" className="flex-1 cursor-pointer">
                <div className="flex items-center gap-1.5">
                  <Star className="h-3.5 w-3.5 text-primary" />
                  <span className="text-sm font-semibold text-foreground">Prefer highly rated providers</span>
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Increases the weight of ratings vs. distance in the ranking algorithm.
                </p>
              </label>
            </div>
            <button
              type="button"
              onClick={handleSave}
              className="rounded-xl bg-primary px-6 py-2.5 text-sm font-bold text-primary-foreground hover:opacity-90 transition-opacity"
            >
              {saved ? "Saved!" : "Save changes"}
            </button>
          </section>

          {/* Appearance */}
          <section className="tile p-6">
            <h2 className="text-base font-bold text-foreground">Appearance</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Current theme: <span className="font-semibold text-foreground">{theme === "dark" ? "Dark" : "Light"}</span>.
              Use the toggle in the sidebar to switch.
            </p>
          </section>
        </div>
      </div>
    </motion.div>
  );
}