import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useSearchParams } from "react-router-dom";
import { useApi, UserContext, type Json } from "./api";
import { Ask } from "./screens/Ask";
import { Builds } from "./screens/Builds";
import { Demand } from "./screens/Demand";
import { Drift } from "./screens/Drift";
import { Lineage } from "./screens/Lineage";
import { Access } from "./screens/Access";
import { Inbox } from "./screens/Inbox";
import { Market } from "./screens/Market";

const NAV = [
  { to: "/market", label: "Marketplace" },
  { to: "/", label: "Ask" },
  { to: "/agents", label: "Agent store" },
  { to: "/access", label: "Access" },
  { to: "/inbox", label: "Inbox" },
  { to: "/demand", label: "Demand board" },
  { to: "/builds", label: "Builds" },
  { to: "/drift", label: "Drift" },
  { to: "/lineage", label: "Lineage" },
];

function Notifications({ user }: { user: string }) {
  const n = useApi<Json[]>(["notifications", user], `/notifications?user=${user}`, { refetchInterval: 4000 });
  const [open, setOpen] = useState(false);
  const unread = (n.data ?? []).filter((x) => !x.read_at);
  return (
    <div className="relative">
      <button className="btn" onClick={() => setOpen(!open)} data-testid="notifications">
        Notifications {unread.length > 0 && <span className="rounded-full bg-accent px-1.5 text-xs text-white">{unread.length}</span>}
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-2 w-96 card p-3 shadow-lg">
          {(n.data ?? []).length === 0 && <div className="text-sm text-slate-500">Nothing yet.</div>}
          {(n.data ?? []).map((x) => (
            <div key={x.notification_id} className="border-b border-slate-100 py-2 text-sm last:border-0">{x.message}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [params, setParams] = useSearchParams();
  const [user, setUserState] = useState(params.get("user") ?? "alice");
  const users = useApi<Json[]>(["users"], "/users");
  useEffect(() => {
    const u = params.get("user");
    if (u && u !== user) setUserState(u);
  }, [params, user]);
  const setUser = (u: string) => {
    setUserState(u);
    params.set("user", u);
    setParams(params, { replace: true });
  };
  const me = users.data?.find((u) => u.user_id === user);
  return (
    <UserContext.Provider value={{ user, setUser }}>
      <div className="flex h-full">
        <aside className="w-56 shrink-0 border-r border-slate-200 bg-white p-4">
          <div className="mb-6">
            <div className="text-lg font-semibold tracking-tight">Tessera</div>
            <div className="text-xs text-slate-500">Governed answers, built by agents</div>
          </div>
          <nav className="space-y-1">
            {NAV.map((n) => (
              <NavLink key={n.to} to={{ pathname: n.to, search: `?user=${user}` }} end className={({ isActive }) => `block rounded-md px-3 py-2 text-sm ${isActive ? "bg-accent-soft font-semibold text-accent" : "text-slate-600 hover:bg-slate-50"}`}>
                {n.label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
            <div className="text-sm text-slate-500">
              {me ? `${me.display_name} · ${me.role.replace("_", " ")}${me.region ? ` · ${me.region}` : ""}` : ""}
            </div>
            <div className="flex items-center gap-2">
              <Notifications user={user} />
              <select className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" value={user} onChange={(e) => setUser(e.target.value)} data-testid="user-switcher">
                {(users.data ?? []).map((u) => (
                  <option key={u.user_id} value={u.user_id}>{u.display_name}</option>
                ))}
              </select>
            </div>
          </header>
          <main className="flex-1 overflow-auto p-6">
            <Routes>
              <Route path="/" element={<Ask />} />
              <Route path="/demand" element={<Demand />} />
              <Route path="/builds" element={<Builds />} />
              <Route path="/drift" element={<Drift />} />
              <Route path="/lineage" element={<Lineage />} />
              <Route path="/market" element={<Market />} />
              <Route path="/agents" element={<Market only="agent" />} />
              <Route path="/access" element={<Access />} />
              <Route path="/inbox" element={<Inbox />} />
            </Routes>
          </main>
        </div>
      </div>
    </UserContext.Provider>
  );
}
