"use client";
// CareCall dashboard for v0 (Next.js App Router + Tailwind). No other libraries needed.
// Paste into v0 as app/page.tsx if the generated page drifts from the API contract.
import { useCallback, useEffect, useRef, useState } from "react";

type Ev = { id: number; agent: string; text: string; created_at: string };
type Call = {
  id: string; phone: string; message: string; language: string; priority: string; status: string;
  scheduled_at: string; attempts: number; max_attempts: number; summary: string; events: Ev[];
};

const STATUS: Record<string, string> = {
  scheduled: "bg-indigo-100 text-indigo-800",
  calling: "bg-amber-100 text-amber-800",
  confirmed: "bg-green-100 text-green-800",
  rescheduled: "bg-sky-100 text-sky-800",
  retrying: "bg-orange-100 text-orange-800",
  needs_attention: "bg-red-600 text-white",
  unreachable: "bg-gray-200 text-gray-700",
};
const PRIORITY: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  high: "bg-amber-100 text-amber-800",
  normal: "bg-gray-100 text-gray-700",
};
const AGENT: Record<string, string> = {
  PLANNER: "bg-violet-100 text-violet-800",
  LISTENER: "bg-blue-100 text-blue-800",
  GUARDIAN: "bg-yellow-100 text-yellow-900",
  REPORTER: "bg-green-100 text-green-800",
  SCHEDULER: "bg-slate-100 text-slate-700",
  TWILIO: "bg-pink-100 text-pink-800",
  PERSON: "bg-sky-100 text-sky-800",
  CARECALL: "bg-teal-100 text-teal-800",
  SYSTEM: "bg-red-100 text-red-800",
};

function defaultWhen() {
  const d = new Date(Date.now() + 60_000);
  d.setSeconds(0, 0);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function Page() {
  const [api, setApi] = useState("");
  const [calls, setCalls] = useState<Call[]>([]);
  const [health, setHealth] = useState("not connected");
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [form, setForm] = useState({ phone: "", message: "", note: "", sender_name: "", language: "en", when: "" });
  const apiRef = useRef("");

  useEffect(() => {
    let saved = "";
    try { saved = localStorage.getItem("carecall_api") || ""; } catch {}
    setApi(saved); apiRef.current = saved;
    setForm(f => ({ ...f, when: defaultWhen() }));
  }, []);

  const headers = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };

  const load = useCallback(async () => {
    const base = apiRef.current;
    if (!base) return;
    try {
      const r = await fetch(`${base}/api/calls`, { headers });
      if (r.ok) setCalls(await r.json());
      const h = await (await fetch(`${base}/api/health`, { headers })).json();
      setHealth(`${h.dry_run ? "DRY RUN" : "LIVE CALLS"} · db ${h.db} · llm ${h.llm}`);
    } catch {
      setHealth("backend offline");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 2000); // live updates by polling every 2 seconds
    return () => clearInterval(t);
  }, [load]);

  function saveApi(v: string) {
    const clean = v.trim().replace(/\/+$/, "");
    setApi(clean); apiRef.current = clean;
    try { localStorage.setItem("carecall_api", clean); } catch {}
    load();
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg("Planner agent is thinking…");
    try {
      const r = await fetch(`${api}/api/calls`, {
        method: "POST", headers,
        body: JSON.stringify({
          phone: form.phone, message: form.message, note: form.note, sender_name: form.sender_name,
          language: form.language, scheduled_at: new Date(form.when).toISOString(),
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || String(r.status));
      setMsg(`Scheduled. Priority: ${data.priority}.`);
      setForm(f => ({ ...f, when: defaultWhen() }));
      load();
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
  }

  async function reset() {
    if (!confirm("Delete all calls?")) return;
    await fetch(`${api}/api/calls`, { method: "DELETE", headers });
    load();
  }
  async function callNow(id: string) {
    await fetch(`${api}/api/calls/${id}/call-now`, { method: "POST", headers });
    load();
  }

  const input = "w-full rounded-md border border-gray-300 px-3 py-2 text-sm";
  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="flex flex-wrap items-center gap-3 bg-teal-700 px-5 py-3 text-white">
        <h1 className="text-xl font-bold">CareCall</h1>
        <span className="text-sm opacity-80">Multi-agent reminder calls · Team Carpe Diem (Ah-11)</span>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <input className="w-72 rounded px-2 py-1 text-gray-900" placeholder="Backend URL (https://xxxx.ngrok-free.app)"
            defaultValue={api} key={api} onBlur={e => saveApi(e.target.value)} />
          <span>{health}</span>
          <button onClick={reset} className="rounded bg-red-100 px-3 py-1 font-semibold text-red-700">Reset demo</button>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-4 p-4 md:grid-cols-[340px_1fr]">
        <form onSubmit={submit} className="h-fit space-y-2 rounded-xl border bg-white p-4">
          <h2 className="font-semibold">Schedule a call</h2>
          <label className="block text-sm font-medium">Phone number (international)</label>
          <input className={input} required placeholder="+919876543210" value={form.phone}
            onChange={e => setForm({ ...form, phone: e.target.value })} />
          <label className="block text-sm font-medium">Message to speak</label>
          <textarea className={input} required rows={3} value={form.message}
            placeholder="Please take your blood pressure tablet after breakfast."
            onChange={e => setForm({ ...form, message: e.target.value })} />
          <label className="block text-sm font-medium">Note for questions (optional)</label>
          <textarea className={input} rows={2} value={form.note}
            placeholder="Blue tablet, the box is on the kitchen shelf. Take it with water."
            onChange={e => setForm({ ...form, note: e.target.value })} />
          <label className="block text-sm font-medium">Your name (optional)</label>
          <input className={input} value={form.sender_name} placeholder="Ravi"
            onChange={e => setForm({ ...form, sender_name: e.target.value })} />
          <label className="block text-sm font-medium">Date and time</label>
          <input className={input} type="datetime-local" required value={form.when}
            onChange={e => setForm({ ...form, when: e.target.value })} />
          <label className="block text-sm font-medium">Language</label>
          <select className={input} value={form.language} onChange={e => setForm({ ...form, language: e.target.value })}>
            <option value="en">English</option><option value="hi">Hindi</option><option value="kn">Kannada</option>
          </select>
          <button className="mt-2 w-full rounded-md bg-teal-700 py-2 font-semibold text-white" type="submit">Schedule call</button>
          <p className="text-sm text-gray-600">{msg}</p>
        </form>

        <section className="space-y-3">
          {calls.length === 0 && <div className="rounded-xl border bg-white p-4 text-sm text-gray-500">No calls yet.</div>}
          {calls.map(c => {
            const attn = c.status === "needs_attention";
            return (
              <div key={c.id} className={`rounded-xl bg-white ${attn ? "border-2 border-red-600" : "border"}`}>
                <div className="flex flex-wrap items-center gap-2 px-4 py-3">
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold uppercase ${STATUS[c.status] || ""}`}>
                    {c.status.replace("_", " ")}
                  </span>
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold uppercase ${PRIORITY[c.priority] || ""}`}>
                    {c.priority}
                  </span>
                  <b>{c.phone}</b>
                  <span className="text-xs text-gray-500">
                    {c.language.toUpperCase()} · {new Date(c.scheduled_at).toLocaleString()} · attempts {c.attempts}
                  </span>
                  <button onClick={() => callNow(c.id)} className="ml-auto rounded border px-2 py-1 text-xs">Call now</button>
                </div>
                <p className="px-4 pb-2 text-sm text-gray-500">“{c.message}”</p>
                {c.summary && (
                  <p className={`px-4 pb-3 text-sm ${attn ? "font-bold text-red-700" : ""}`}>Reporter: {c.summary}</p>
                )}
                <div className="border-t px-4 py-2">
                  <button className="text-sm text-gray-500" onClick={() => setOpen(o => ({ ...o, [c.id]: !o[c.id] }))}>
                    {open[c.id] ? "▾" : "▸"} Transcript ({c.events.length} lines)
                  </button>
                  {open[c.id] && (
                    <div className="mt-2 space-y-1">
                      {c.events.map(ev => (
                        <div key={ev.id} className="flex gap-2 text-sm">
                          <span className="w-16 shrink-0 text-xs text-gray-400">
                            {new Date(ev.created_at).toLocaleTimeString()}
                          </span>
                          <span className={`h-fit w-24 shrink-0 rounded px-1.5 py-0.5 text-center text-[10px] font-extrabold ${AGENT[ev.agent] || ""}`}>
                            {ev.agent}
                          </span>
                          <span>{ev.text}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </section>
      </main>
    </div>
  );
}
