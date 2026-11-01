/**
 * CareCall - Multi-Agent Dashboard Client
 * Team Carpe Diem · AfterHours 1.0 Final Round
 */

const API = "";
const H = {
  "Content-Type": "application/json",
  "ngrok-skip-browser-warning": "true"
};

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;"
}[c]));

const toast = document.getElementById("toast");
function showToast(msg) {
  if (!toast) return;
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3000);
}

// Modal open/close helpers
const modal = document.getElementById("modal");
const contactModal = document.getElementById("contact-modal");

function openModal() {
  if (!modal) return;
  setDefaultDateTime();
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
}

function closeModal() {
  if (modal) {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }
  if (contactModal) {
    contactModal.classList.remove("open");
    contactModal.setAttribute("aria-hidden", "true");
  }
}

function openContactModal() {
  if (!contactModal) return;
  contactModal.classList.add("open");
  contactModal.setAttribute("aria-hidden", "false");
}

function setDefaultDateTime() {
  const d = new Date(Date.now() + 60000);
  const dateInput = document.getElementById("reminder-date");
  const timeInput = document.getElementById("reminder-time");
  if (dateInput) dateInput.value = d.toISOString().slice(0, 10);
  if (timeInput) {
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    timeInput.value = `${hh}:${mm}`;
  }
}

// Formats
function formatTime(isoStr) {
  if (!isoStr) return "—";
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return isoStr;
  }
}

function formatDate(isoStr) {
  if (!isoStr) return "—";
  try {
    const d = new Date(isoStr);
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return isoStr;
  }
}

// Global cached state
let allCalls = [];
let allContacts = [];
let allAlerts = [];

// Receiver Quick Selection
function selectContact(phone, name, msg, voice, btnEl) {
  const pInput = document.getElementById("instantPhone");
  const nInput = document.getElementById("instantName");
  const mInput = document.getElementById("instantMessage");
  const vInput = document.getElementById("instantVoice");

  if (pInput) pInput.value = phone;
  if (nInput) nInput.value = name;
  if (mInput) mInput.value = msg || "Daily check-in call";
  if (vInput && voice) vInput.value = voice;

  // Visual highlight for the clicked quick receiver button
  document.querySelectorAll(".receiver-quick-btn").forEach(b => {
    b.style.borderColor = "#eeeef3";
    b.style.background = "#ffffff";
    b.style.color = "#77768a";
    b.style.fontWeight = "600";
  });
  if (btnEl) {
    btnEl.style.borderColor = "#7056d6";
    btnEl.style.background = "#f4f1fd";
    btnEl.style.color = "#7056d6";
    btnEl.style.fontWeight = "800";
  }

  showToast(`Selected receiver ${name} (${phone}). Ready to call!`);
}

function quickDial(phone, name, msg, voice) {
  selectContact(phone, name, msg, voice);
  const panel = document.getElementById("instant-call-panel");
  if (panel) panel.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function callAllReceivers() {
  const banner = document.getElementById("callStatusBanner");
  const btn = document.getElementById("btn-broadcast-all");
  const voice = document.getElementById("instantVoice")?.value || "Polly.Aditi";

  if (!confirm("Start broadcast calls to all 3 receivers (Sritej, Satyam, Pushkar)?")) return;

  btn.disabled = true;
  btn.textContent = "Broadcasting...";

  banner.className = "call-banner ringing";
  banner.innerHTML = "<b>Connecting to Twilio...</b> Dialing Sritej, Satyam, and Pushkar simultaneously...";

  try {
    const res = await fetch(API + "/api/calls/immediate-all", {
      method: "POST",
      headers: H,
      body: JSON.stringify({ voice, message: "Daily check-in verification call" })
    });
    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!res.ok) throw new Error(data.detail || `Server error (HTTP ${res.status})`);

    const sids = (data.results || []).map(r => `<li><b>${esc(r.name)} (${esc(r.phone)}):</b> ${r.twilio_sid ? `Ringing (SID: <code>${r.twilio_sid}</code>)` : `<span style='color:red'>${esc(r.error || 'Failed')}</span>`}</li>`).join("");

    banner.className = "call-banner ringing";
    banner.innerHTML = `<b>📞 Broadcast Initiated!</b> Ringing all 3 receivers:<ul style="margin:6px 0 0 16px;padding:0;">${sids}</ul><small style="display:block;margin-top:6px;">Verification keyword is Birth Year: <b>2007</b>.</small>`;
    showToast("Broadcast calls sent to all receivers!");
    loadAll();
  } catch (err) {
    banner.className = "call-banner error";
    banner.innerHTML = `<b>Broadcast error:</b> ${esc(err.message)}`;
    showToast("Error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "📢 Broadcast Call to All 3";
  }
}

function applyReceiverPreset(val) {
  if (!val) return;
  const parts = val.split("|");
  if (parts.length >= 3) {
    const [phone, name, msg] = parts;
    const pInput = document.getElementById("reminder-phone");
    const nInput = document.getElementById("reminder-name");
    const mInput = document.getElementById("reminder-title");
    if (pInput) pInput.value = phone;
    if (nInput) nInput.value = name;
    if (mInput) mInput.value = msg;
    showToast(`Loaded ${name} preset.`);
  }
}

// ============================================================
// DATA LOADERS
// ============================================================

async function loadHealth() {
  try {
    const res = await fetch(API + "/api/health", { headers: H });
    if (!res.ok) return;
    const h = await res.json();

    const pill = document.getElementById("system-health");
    if (pill) {
      const mode = h.dry_run ? "DRY RUN" : "LIVE";
      pill.innerHTML = `● <b>${mode}</b> · Voice: ${esc(h.default_voice || "Polly.Aditi")} · LLM ${esc(h.llm)}`;
    }

    const todayLabel = document.getElementById("today-label");
    if (todayLabel) {
      const now = new Date();
      todayLabel.textContent = now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
    }
  } catch (err) {
    const pill = document.getElementById("system-health");
    if (pill) pill.innerHTML = `● <span style="color:#b91c1c;">Offline</span>`;
  }
}

async function loadCalls() {
  try {
    const res = await fetch(API + "/api/calls", { headers: H });
    if (!res.ok) return;
    const calls = await res.json();
    allCalls = calls;

    // Stat cards update
    const totalEl = document.getElementById("stat-total");
    if (totalEl) totalEl.textContent = calls.length;

    const navRemCount = document.getElementById("nav-reminders-count");
    if (navRemCount) navRemCount.textContent = calls.filter(c => c.status === "scheduled" || c.status === "calling").length;

    const confirmed = calls.filter(c => c.status === "confirmed").length;
    const failed = calls.filter(c => c.status === "failed" || c.status === "needs_attention").length;
    const denom = confirmed + failed;
    const pct = denom > 0 ? Math.round((confirmed / denom) * 100) : 100;

    const confEl = document.getElementById("stat-confirmed");
    if (confEl) confEl.textContent = pct;

    const barEl = document.getElementById("stat-progress-bar");
    if (barEl) barEl.style.width = pct + "%";

    renderReminders(calls);
    renderCallLogs(calls);
    renderActivities(calls, allAlerts);
  } catch (err) {
    console.error("loadCalls error:", err);
  }
}

function renderReminders(calls) {
  const listEl = document.getElementById("reminder-list");
  if (!listEl) return;

  if (!calls.length) {
    listEl.innerHTML = `<div style="padding:22px;text-align:center;color:var(--muted);font-size:11px;">No upcoming reminders scheduled. Click <b>＋ Create reminder</b> or use <b>Instant Call</b>.</div>`;
    return;
  }

  listEl.innerHTML = calls.slice(0, 5).map(c => {
    const statusClass = `status-${c.status || "scheduled"}`;
    const contactName = c.sender_name || c.phone;
    const timeStr = formatTime(c.scheduled_at);

    return `
      <div class="reminder-item">
        <div class="reminder-time">
          <strong>${timeStr}</strong>
          <small>${formatDate(c.scheduled_at)}</small>
        </div>
        <div class="reminder-copy">
          <strong>${esc(c.spoken_message || c.message)}</strong>
          <small>To: <b>${esc(contactName)}</b> (${esc(c.phone)}) · Voice: <code>${esc(c.voice || "Polly.Aditi")}</code></small>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          <span class="badge-status ${statusClass}">${esc(c.status)}</span>
          <button class="call-sm-btn" onclick="callNow('${esc(c.id)}')">📞 Call</button>
        </div>
      </div>
    `;
  }).join("");
}

function renderCallLogs(calls) {
  const tbody = document.getElementById("call-log-rows");
  if (!tbody) return;

  const query = (document.getElementById("call-search")?.value || "").toLowerCase();
  const filtered = calls.filter(c => 
    (c.sender_name || "").toLowerCase().includes(query) ||
    (c.phone || "").toLowerCase().includes(query) ||
    (c.message || "").toLowerCase().includes(query) ||
    (c.status || "").toLowerCase().includes(query)
  );

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px;">No call logs found.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(c => {
    const statusClass = `status-${c.status || "scheduled"}`;
    const events = c.events || [];
    const eventCount = events.length;

    return `
      <tr>
        <td>
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="avatar avatar-purple" style="width:24px;height:24px;font-size:8px;">${esc((c.sender_name || "SD").slice(0,2).toUpperCase())}</span>
            <div>
              <strong style="color:#29283c;">${esc(c.sender_name || "Contact")}</strong>
              <div style="font-size:9px;color:var(--muted);">${esc(c.phone)}</div>
            </div>
          </div>
        </td>
        <td>
          <span style="font-weight:600;">${formatTime(c.scheduled_at || c.created_at)}</span>
          <div style="font-size:8px;color:var(--muted);">${formatDate(c.scheduled_at || c.created_at)}</div>
        </td>
        <td><span class="badge-status ${statusClass}">${esc(c.status)}</span></td>
        <td><code style="font-size:9px;color:#059669;">${esc(c.twilio_sid || "—")}</code></td>
        <td style="max-width:320px;white-space:normal;">
          <div style="font-weight:600;font-size:10px;color:#29283c;margin-bottom:4px;">“${esc(c.spoken_message || c.message)}”</div>
          <div style="font-size:9px;color:var(--muted);margin-bottom:4px;">TTS Voice: <code>${esc(c.voice || "Polly.Aditi")}</code></div>
          ${c.summary ? `<div style="font-size:9px;color:#43805c;margin-bottom:4px;"><b>Summary:</b> ${esc(c.summary)}</div>` : ""}
          ${eventCount > 0 ? `
            <details>
              <summary style="font-size:9px;color:#7067dc;cursor:pointer;font-weight:700;">Transcript (${eventCount} turns)</summary>
              <div class="transcript-box">
                ${events.map(e => `
                  <div class="transcript-line">
                    <span class="agent-tag agent-${esc(e.agent)}">${esc(e.agent)}</span>
                    <span style="flex:1;">${esc(e.text)}</span>
                    <span style="color:var(--muted);font-size:8px;">${formatTime(e.created_at)}</span>
                  </div>
                `).join("")}
              </div>
            </details>
          ` : `<span style="font-size:8px;color:var(--muted);">No transcript events yet</span>`}
        </td>
        <td>
          <button class="call-sm-btn" onclick="callNow('${esc(c.id)}')">📞 Call</button>
        </td>
      </tr>
    `;
  }).join("");
}

async function loadContacts() {
  try {
    const res = await fetch(API + "/api/contacts", { headers: H });
    if (!res.ok) return;
    const contacts = await res.json();
    allContacts = contacts;

    const countEl = document.getElementById("stat-contacts");
    if (countEl) countEl.textContent = contacts.length;

    const navC = document.getElementById("nav-contacts-count");
    if (navC) navC.textContent = contacts.length;

    const tbody = document.getElementById("contact-rows");
    if (!tbody) return;

    const query = (document.getElementById("contact-search")?.value || "").toLowerCase();
    const filtered = contacts.filter(c =>
      (c.name || "").toLowerCase().includes(query) ||
      (c.phone || "").toLowerCase().includes(query) ||
      (c.reminders || "").toLowerCase().includes(query)
    );

    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px;">No contacts found. Register a contact above.</td></tr>`;
      return;
    }

    tbody.innerHTML = filtered.map(c => `
      <tr>
        <td>
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="avatar avatar-pink" style="width:24px;height:24px;font-size:8px;">${esc((c.name || "C").slice(0,2).toUpperCase())}</span>
            <strong style="color:#29283c;">${esc(c.name)}</strong>
          </div>
        </td>
        <td><code>${esc(c.phone)}</code></td>
        <td>
          <span style="background:#eaf8f1;color:#16794b;padding:2px 7px;border-radius:4px;font-weight:800;font-size:10px;">
            🔑 ${esc(c.secret_check || "2007")}
          </span>
        </td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;" title="${esc(c.reminders)}">${esc(c.reminders || "Daily check-in")}</td>
        <td><span class="badge-status status-scheduled" style="font-size:8px;">${esc(c.voice || "Polly.Aditi")}</span></td>
        <td>
          <button class="call-sm-btn" onclick="quickDial('${esc(c.phone)}', '${esc(c.name)}', '${esc(c.reminders || 'Daily check-in call')}', '${esc(c.voice || 'Polly.Aditi')}')">
            📞 Call Now
          </button>
        </td>
      </tr>
    `).join("");
  } catch (err) {
    console.error("loadContacts error:", err);
  }
}

async function loadAlerts() {
  try {
    const res = await fetch(API + "/api/alerts", { headers: H });
    if (!res.ok) return;
    const alerts = await res.json();
    allAlerts = alerts;

    const alertCountEl = document.getElementById("stat-alerts");
    if (alertCountEl) alertCountEl.textContent = alerts.length;

    const navAlertCount = document.getElementById("nav-alerts-count");
    if (navAlertCount) {
      navAlertCount.textContent = alerts.length;
      navAlertCount.style.display = alerts.length > 0 ? "inline-block" : "none";
    }

    const label = document.getElementById("stat-alerts-label");
    if (label) {
      if (alerts.length > 0) {
        label.textContent = `${alerts.length} Follow-up${alerts.length > 1 ? "s" : ""} Needed`;
        label.style.background = "#fff0f2";
        label.style.color = "#b91c1c";
      } else {
        label.textContent = "All Normal";
        label.style.background = "#eaf8f1";
        label.style.color = "#16794b";
      }
    }

    const listEl = document.getElementById("alerts-list");
    if (!listEl) return;

    if (!alerts.length) {
      listEl.innerHTML = `<div style="padding:16px;color:#16794b;background:#eaf8f1;border-radius:8px;font-size:11px;font-weight:600;">✓ No active caregiver alerts. All phone check-ins are verified and healthy.</div>`;
      return;
    }

    listEl.innerHTML = alerts.map(a => {
      const sev = (a.severity || "high").toLowerCase();
      const reasons = Array.isArray(a.reasons) ? a.reasons.join(" · ") : (a.reasons || "Attention required");

      return `
        <div class="alert-item ${sev}">
          <div class="alert-top">
            <span class="badge-status status-${sev === 'high' ? 'needs_attention' : 'calling'}">${sev.toUpperCase()} ALERT</span>
            <span style="color:var(--muted);">${formatTime(a.created_at)}</span>
          </div>
          <div class="alert-reason">${esc(reasons)}</div>
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:9px;color:var(--muted);margin-top:4px;">
            <span>Contact ID: <code>${esc(a.contact_id || "demo")}</code></span>
            <button class="call-sm-btn" style="background:#475569;" onclick="ackAlert(${a.id})">Acknowledge ✓</button>
          </div>
        </div>
      `;
    }).join("");
  } catch (err) {
    console.error("loadAlerts error:", err);
  }
}

function renderActivities(calls, alerts) {
  const actEl = document.getElementById("activity-list");
  if (!actEl) return;

  const activities = [];

  // Recent alerts
  (alerts || []).slice(0, 3).forEach(a => {
    activities.push({
      kind: "missed",
      icon: "!",
      text: `<strong>Safety Alert (${esc(a.severity)}):</strong> ${esc(Array.isArray(a.reasons) ? a.reasons[0] : a.reasons)}`,
      time: formatTime(a.created_at)
    });
  });

  // Recent call events
  (calls || []).forEach(c => {
    (c.events || []).slice(-2).forEach(e => {
      activities.push({
        kind: e.agent === "GUARDIAN" ? "missed" : (e.agent === "LISTENER" || e.agent === "TWILIO" ? "success" : "created"),
        icon: e.agent === "TWILIO" ? "📞" : (e.agent === "GUARDIAN" ? "!" : "✓"),
        text: `<strong>${esc(e.agent)}:</strong> ${esc(e.text)}`,
        time: formatTime(e.created_at)
      });
    });
  });

  if (!activities.length) {
    actEl.innerHTML = `<div style="padding:15px;color:var(--muted);font-size:10px;">No recent activity recorded yet.</div>`;
    return;
  }

  // Sort and take latest 4
  actEl.innerHTML = activities.slice(0, 4).map(item => `
    <div class="activity-item">
      <span class="activity-symbol ${item.kind}">${item.icon}</span>
      <span class="activity-copy">${item.text}</span>
      <time>${item.time}</time>
    </div>
  `).join("");
}

// ============================================================
// ACTIONS & HANDLERS
// ============================================================

// Trigger manual call now for a reminder
async function callNow(callId) {
  try {
    showToast("Triggering call now...");
    const res = await fetch(`${API}/api/calls/${callId}/call-now`, { method: "POST", headers: H });
    if (!res.ok) throw new Error("Could not trigger call");
    showToast("Call initiated! Twilio is dialing.");
    loadCalls();
  } catch (err) {
    showToast("Error: " + err.message);
  }
}

// Acknowledge alert
async function ackAlert(alertId) {
  try {
    await fetch(`${API}/api/alerts/${alertId}/ack`, { method: "POST", headers: H });
    showToast("Alert acknowledged.");
    loadAlerts();
  } catch (err) {
    showToast("Could not acknowledge: " + err.message);
  }
}

// Reset entire demo
async function resetDemo() {
  if (!confirm("Reset all calls and alerts for demo?")) return;
  try {
    await fetch(API + "/api/calls", { method: "DELETE", headers: H });
    showToast("Demo reset complete. Clean slate ready.");
    loadAll();
  } catch (err) {
    showToast("Reset failed: " + err.message);
  }
}

// ============================================================
// EVENT LISTENERS
// ============================================================

// Instant Outbound Call Form
document.getElementById("instantCallForm")?.addEventListener("submit", async e => {
  e.preventDefault();
  const btn = document.getElementById("instantCallBtn");
  const banner = document.getElementById("callStatusBanner");

  btn.disabled = true;
  btn.textContent = "Connecting Twilio...";

  banner.className = "call-banner ringing";
  banner.innerHTML = "<b>Connecting to Twilio...</b> Dialing receiver number now...";

  const phone = document.getElementById("instantPhone").value.trim();
  const name = document.getElementById("instantName").value.trim();
  const message = document.getElementById("instantMessage").value.trim();
  const voice = document.getElementById("instantVoice")?.value || "Polly.Aditi";

  try {
    const res = await fetch(API + "/api/calls/immediate", {
      method: "POST",
      headers: H,
      body: JSON.stringify({ phone, name, message, voice })
    });
    const text = await res.text();
    let data = {};
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text || `HTTP ${res.status}` };
    }
    if (!res.ok) throw new Error(data.detail || `Server error (HTTP ${res.status})`);

    banner.className = "call-banner ringing";
    banner.innerHTML = `<b>📞 Phone is Ringing!</b> Call SID: <code>${data.twilio_sid}</code> · TTS Voice: <b>${esc(voice)}</b>. Answer phone! Agent will verify Birth Year (<b>2007</b>).`;
    showToast(`Outbound call placed with ${voice}!`);
    loadAll();
  } catch (err) {
    banner.className = "call-banner error";
    banner.innerHTML = `<b>Error placing call:</b> ${esc(err.message)}`;
    showToast("Error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span>📞</span> Call Receiver Now`;
  }
});

// Create Scheduled Reminder Form
document.getElementById("reminder-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  const btn = document.getElementById("save-reminder-btn");
  btn.disabled = true;
  btn.textContent = "Planner agent is thinking…";

  const phone = document.getElementById("reminder-phone").value.trim();
  const name = document.getElementById("reminder-name").value.trim();
  const message = document.getElementById("reminder-title").value.trim();
  const dateVal = document.getElementById("reminder-date").value;
  const timeVal = document.getElementById("reminder-time").value;
  const language = document.getElementById("reminder-lang").value;
  const note = document.getElementById("reminder-note").value.trim();
  const voice = document.getElementById("reminder-voice")?.value || "Polly.Aditi";

  const scheduledAt = new Date(`${dateVal}T${timeVal}:00`).toISOString();

  try {
    const res = await fetch(API + "/api/calls", {
      method: "POST",
      headers: H,
      body: JSON.stringify({
        phone,
        message,
        sender_name: name,
        scheduled_at: scheduledAt,
        language,
        note,
        voice
      })
    });
    const text = await res.text();
    let data = {};
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text || `HTTP ${res.status}` };
    }
    if (!res.ok) throw new Error(data.detail || `Server error (HTTP ${res.status})`);

    showToast(`Reminder scheduled! Priority: ${data.priority.toUpperCase()}`);
    closeModal();
    loadAll();
  } catch (err) {
    showToast("Error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `Save reminder <span>→</span>`;
  }
});

// Add Contact Profile Form
document.getElementById("new-contact-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  const name = document.getElementById("new-contact-name").value.trim();
  const phone = document.getElementById("new-contact-phone").value.trim();
  const secret = document.getElementById("new-contact-secret").value.trim() || "2007";
  const reminders = document.getElementById("new-contact-reminders").value.trim();
  const notes = document.getElementById("new-contact-notes").value.trim();
  const voice = document.getElementById("new-contact-voice")?.value || "Polly.Aditi";

  try {
    const res = await fetch(API + "/api/contacts", {
      method: "POST",
      headers: H,
      body: JSON.stringify({
        name,
        phone,
        secret_check: secret,
        reminders,
        notes,
        caregiver_contact: phone,
        voice
      })
    });
    if (!res.ok) throw new Error("Could not save contact");
    showToast(`Receiver ${name} registered with keyword ${secret}!`);
    closeModal();
    loadContacts();
  } catch (err) {
    showToast("Error: " + err.message);
  }
});

// Navigation & scrolling
document.querySelectorAll("[data-page]").forEach(link => {
  link.addEventListener("click", e => {
    e.preventDefault();
    const page = link.dataset.page;

    document.querySelectorAll(".nav-link").forEach(nav =>
      nav.classList.toggle("active", nav.dataset.page === page)
    );

    const pageNameEl = document.getElementById("page-name");
    if (pageNameEl) {
      pageNameEl.textContent = page.charAt(0).toUpperCase() + page.slice(1).replace("-", " ");
    }

    if (page === "reminders") {
      document.getElementById("reminders-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (page === "contacts") {
      document.getElementById("contacts-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (page === "call-logs") {
      document.getElementById("call-logs-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (page === "alerts") {
      document.getElementById("alerts-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    document.getElementById("sidebar")?.classList.remove("open");
  });
});

// Modal triggers
document.getElementById("new-reminder")?.addEventListener("click", openModal);
document.getElementById("close-modal")?.addEventListener("click", closeModal);
document.getElementById("cancel-modal")?.addEventListener("click", closeModal);

document.getElementById("open-contact-modal")?.addEventListener("click", openContactModal);
document.getElementById("close-contact-modal")?.addEventListener("click", closeModal);
document.getElementById("cancel-contact-modal")?.addEventListener("click", closeModal);

document.getElementById("quick-call-scroll")?.addEventListener("click", () => {
  document.getElementById("instant-call-panel")?.scrollIntoView({ behavior: "smooth", block: "center" });
});

document.getElementById("menu-toggle")?.addEventListener("click", () => {
  document.getElementById("sidebar")?.classList.toggle("open");
});

document.getElementById("contact-search")?.addEventListener("input", loadContacts);
document.getElementById("call-search")?.addEventListener("input", () => renderCallLogs(allCalls));

// ============================================================
// INITIALIZATION & POLLING
// ============================================================

async function loadAll() {
  await Promise.all([loadHealth(), loadCalls(), loadContacts(), loadAlerts()]);
}

setDefaultDateTime();
loadAll();
setInterval(loadAll, 2500);
