"use strict";
/* BhashaRadar dashboard — plain JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { meta: null, page: 1, quick: new Set(), showIneligible: false, showExpired: false, polling: null, lastRunId: null };

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = "/login"; throw new Error("Please log in again"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
  return data;
}
function toast(msg, error = false) {
  const t = document.createElement("div");
  t.className = "toast" + (error ? " error" : "");
  t.setAttribute("role", "status");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), error ? 6000 : 3200);
}
const fail = (e) => toast(e.message || String(e), true);
const fmtDate = (iso) => { if (!iso) return ""; const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" }); };
const ago = (iso) => { if (!iso) return "date unknown"; const days = Math.floor((Date.now() - new Date(iso)) / 864e5); return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`; };
const JT = { full_time: "Full-time", part_time: "Part-time", contract: "Contract", freelance: "Freelance", internship: "Internship", temporary: "Temporary", unknown: "Type n/a" };
const scoreClass = (l) => ({ "Excellent Match": "excellent", "Strong Match": "strong", "Possible Match": "possible", "Low Match": "low", "Not Eligible": "ineligible" }[l] || "low");

/* ---------------- multi-select ---------------- */
function multiSelect(el, options, selected, placeholder, labels = {}) {
  el.innerHTML = `<details class="ms"><summary></summary><div class="menu">
    <div class="menu-actions"><button type="button" class="linkish" data-all>Select all</button><button type="button" class="linkish" data-none>Clear</button></div>
    ${options.map((o) => `<label><input type="checkbox" value="${esc(o)}" ${selected.includes(o) ? "checked" : ""}> ${esc(labels[o] || o)}</label>`).join("")}</div></details>`;
  const det = $("details", el), sum = $("summary", el);
  const update = () => { const v = values(); sum.textContent = v.length === 0 ? placeholder : v.length <= 2 ? v.map((x) => labels[x] || x).join(", ") : `${v.length} selected`; };
  const values = () => $$("input:checked", el).map((i) => i.value);
  el.values = values;
  el.addEventListener("change", update);
  $("[data-all]", el).onclick = () => { $$("input", det).forEach((i) => (i.checked = true)); update(); };
  $("[data-none]", el).onclick = () => { $$("input", det).forEach((i) => (i.checked = false)); update(); };
  document.addEventListener("click", (e) => { if (!el.contains(e.target)) det.open = false; });
  update();
}

/* ---------------- navigation ---------------- */
function show(view) {
  $$("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === view)));
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  ({ tracker: loadTracker, outreach: loadOutreach, sources: loadSources, settings: loadSettings, jobs: loadJobs })[view]?.();
  history.replaceState(null, "", `#${view}`);
}
$$("nav.tabs button").forEach((b) => (b.onclick = () => show(b.dataset.view)));
$$("[data-goto]").forEach((b) => (b.onclick = () => show(b.dataset.goto)));

/* ---------------- status header ---------------- */
async function refreshStatus() {
  const s = await api("/api/status");
  $("#logout-form").hidden = !s.auth;
  S.cloud = s.cloud;
  const pr = $("#pill-resume"), ps = $("#pill-sources"), po = $("#pill-outreach");
  pr.querySelector(".dot").className = "dot " + (s.profile.verified ? "good" : "warn");
  pr.lastElementChild.textContent = s.profile.verified ? "Resume ✓" : "Add resume"; pr.title = s.profile.verified ? s.profile.filename : "No resume uploaded yet";
  const src = s.sources;
  ps.querySelector(".dot").className = "dot " + (src.error ? "bad" : src.ok ? "good" : "warn");
  ps.lastElementChild.textContent = `${src.ok}/${src.enabled} sources` + (src.error ? ` · ${src.error} down` : ""); ps.title = "Working sources / enabled sources";
  const modeName = { draft_only: "Drafts only", review: "Review first", auto: "Auto email" }[s.outreach.mode];
  po.querySelector(".dot").className = "dot " + (s.outreach.stopped ? "bad" : s.outreach.mode === "auto" ? "warn" : "good");
  po.lastElementChild.textContent = s.outreach.stopped ? "Email paused" : modeName; po.title = "Email outreach mode";
  $("#resume-notice").innerHTML = s.profile.verified ? "" :
    `<div class="notice">Matching uses a provisional profile built from your project brief. <button class="linkish" data-goto="settings" onclick="show('settings')">Upload your resume</button> for accurate scores.</div>`;
  return s;
}

/* ---------------- search form ---------------- */
function filters() {
  return {
    categories: $("#f-categories").values(),
    job_types: $("#f-jobtypes").values(),
    locations: $("#f-locations").values(),
    work_modes: $("#f-workmodes").values(),
    experience_level: $("#f-exp").value,
    min_salary: Number($("#f-salary").value) || null,
    min_score: Number($("#f-score").value) || 0,
    posted_within_days: Number($("#f-days").value) || null,
    keywords: $("#f-keywords").value,
  };
}
const QUICK = [
  ["best", "✦ Best matches"], ["remote", "Remote"], ["freelance", "Freelance & contract"], ["recent", "This week"],
  ["no_upskill", "No new skills"], ["ai", "AI training"], ["localization", "Hindi localization"],
  ["salary", "Pay listed"], ["direct", "Apply direct"],
];
function renderQuick() {
  $("#quick").innerHTML = QUICK.map(([k, l]) => `<button type="button" class="chip" data-q="${k}" aria-pressed="${S.quick.has(k)}">${l}</button>`).join("");
  $$("#quick .chip").forEach((c) => (c.onclick = () => { S.quick.has(c.dataset.q) ? S.quick.delete(c.dataset.q) : S.quick.add(c.dataset.q); c.setAttribute("aria-pressed", String(S.quick.has(c.dataset.q))); S.page = 1; loadJobs(); }));
}
async function startSearch() {
  try {
    const run = await api("/api/search", { method: "POST", body: filters() });
    S.lastRunId = run.id;
    renderProgress(run);
    poll();
  } catch (e) { fail(e); }
}
$("#search-form").onsubmit = (e) => { e.preventDefault(); startSearch(); };
$("#cmd").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const v = e.target.value.trim().toUpperCase();
  e.target.value = "";
  if (v === "GO") startSearch();
  else if (v === "STOP") stopAll();
  else toast("Type GO to search or STOP to halt everything", true);
});
function poll() {
  clearInterval(S.polling);
  $("#go").disabled = true;
  S.polling = setInterval(async () => {
    try {
      const st = await api("/api/search/status");
      renderProgress(st);
      if (st.status !== "running") {
        clearInterval(S.polling); $("#go").disabled = false;
        S.page = 1; loadJobs(); refreshStatus();
      }
    } catch (e) { clearInterval(S.polling); $("#go").disabled = false; }
  }, 1000);
}
function renderProgress(st) {
  if (!st) { $("#progress").innerHTML = ""; return; }
  const idx = st.stage_index || 0;
  const stages = st.stages.map((s, i) => `<li class="${i < idx || st.status === "completed" ? "done" : i === idx ? "now" : ""}">${esc(s)}</li>`).join("");
  const srcs = Object.entries(st.sources || {}).map(([id, s]) => {
    const t = s.state === "done" ? `${s.fetched} found${s.kept != null && st.stage_index > 2 ? `, ${s.kept} relevant` : ""}` : s.state === "error" ? "failed" : s.state === "skipped" ? "skipped" : s.state;
    const title = s.error || s.warning || "";
    return `<span class="src ${s.state}" title="${esc(title)}">${esc(s.name)}: ${esc(t)}</span>`;
  }).join("");
  const x = st.stats || {};
  let summary = "";
  if (st.status === "completed") summary = `<div class="summary-line"><strong>${x.eligible_matches} matches for you</strong> <span class="small">· ${x.discovered} scanned · ${x.relevant} relevant · ${x.duplicates_removed} duplicates merged · ${x.new} new${x.expired_marked ? ` · ${x.expired_marked} expired` : ""}</span>${x.outreach_queued ? `<div class="small">${x.outreach_queued} emails queued by your automatic rules.</div>` : ""}</div>`;
  else if (st.status === "cancelled") summary = `<div class="summary-line">Search stopped. <button class="linkish" onclick="resumeSearch('${esc(st.id)}')">Resume it</button></div>`;
  else if (st.status === "failed" || st.status === "interrupted") summary = `<div class="summary-line">Search ${esc(st.status)}${st.error ? ": " + esc(st.error) : ""}. <button class="linkish" onclick="resumeSearch('${esc(st.id)}')">Resume</button></div>`;
  const cancel = st.status === "running" ? ` <button class="linkish" onclick="cancelSearch()">Cancel search</button>` : "";
  const running = st.status === "running";
  const pct = st.status === "completed" ? 100 : Math.round((100 * idx) / (st.stages.length - 1));
  const states = Object.values(st.sources || {});
  const ok = states.filter((s) => s.state === "done").length, bad = states.filter((s) => s.state === "error").length;
  $("#progress").innerHTML = `<div class="progress ${running ? "is-running" : ""}" aria-live="polite">
    ${running ? `<div class="stage-now"><span class="pulse" aria-hidden="true"></span>${esc(st.stage)}…</div>` : ""}
    <div class="bar-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>
    ${summary}
    <details class="more src-details" ${running ? "open" : ""}><summary>Sources · ${ok} answered${bad ? ` · ${bad} failed` : ""}</summary><div class="src-list">${srcs}</div><ul class="stages">${stages}</ul></details>
    ${cancel ? `<div class="summary-line">${cancel}</div>` : ""}</div>`;
}
async function cancelSearch() { await api("/api/search/cancel", { method: "POST" }); toast("Stopping search…"); }
async function resumeSearch(id) { try { const r = await api(`/api/search/resume/${id}`, { method: "POST" }); renderProgress(r); poll(); } catch (e) { fail(e); } }
async function stopAll() {
  const r = await api("/api/stop", { method: "POST" });
  toast(`Stopped. ${r.search_cancelled ? "Search cancelled. " : ""}${r.outreach_cancelled || 0} queued emails cancelled. Sending is paused until you resume it in Outreach.`);
  refreshStatus();
}
$("#stop-all").onclick = stopAll;

/* ---------------- results ---------------- */
async function loadJobs() {
  const f = filters();
  const p = new URLSearchParams({
    categories: f.categories.join("|"), job_types: f.job_types.join(","), work_modes: f.work_modes.join(","),
    locations: f.locations.join("|"), experience_level: f.experience_level, min_salary: f.min_salary || 0,
    min_score: f.min_score, posted_within_days: f.posted_within_days || 0, quick: [...S.quick].join(","),
    include_ineligible: S.showIneligible, include_expired: S.showExpired, sort: $("#sort").value, page: S.page, page_size: 25,
  });
  let d;
  try { d = await api(`/api/jobs?${p}`); } catch (e) { return fail(e); }
  $("#count").textContent = d.total ? `${d.total} job${d.total === 1 ? "" : "s"}` : "";
  if (!d.items.length) {
    const any = d.hidden_ineligible || d.hidden_expired;
    $("#rows").innerHTML = `<li class="empty"><span class="empty-ico" aria-hidden="true">${any ? "◌" : "✦"}</span><strong>${any ? "Nothing matches these filters" : "No jobs yet"}</strong>${any ? "Try loosening a filter or two." : "Press GO and we'll look everywhere."}</li>`;
  } else {
    $("#rows").innerHTML = d.items.map(row).join("");
    $$("#rows .row").forEach((r) => {
      r.onclick = (e) => { if (!e.target.closest("button,a")) openJob(r.dataset.id); };
      r.onkeydown = (e) => { if (e.key === "Enter" && e.target === r) openJob(r.dataset.id); };
    });
    $$("#rows [data-save]").forEach((b) => (b.onclick = async () => { await api(`/api/jobs/${b.dataset.save}/save?saved=${b.dataset.saved !== "1"}`, { method: "POST" }); loadJobs(); }));
    $$("#rows [data-view]").forEach((b) => (b.onclick = () => openJob(b.dataset.view)));
    $$("#rows [data-apply]").forEach((b) => (b.onclick = () => applyFlow(b.dataset.apply, b.dataset.url)));
  }
  const notes = [];
  if (d.hidden_ineligible) notes.push(`<button class="linkish" id="tog-inel">${S.showIneligible ? "Hide" : "Show"} ${S.showIneligible ? "" : d.hidden_ineligible + " "}not-eligible jobs</button>`);
  if (S.showIneligible) notes.push(`<button class="linkish" id="tog-inel">Hide not-eligible jobs</button>`);
  if (d.hidden_expired || S.showExpired) notes.push(`<button class="linkish" id="tog-exp">${S.showExpired ? "Hide expired" : `Show ${d.hidden_expired} expired`}</button>`);
  $("#hidden-note").innerHTML = notes.length ? `<p class="small" style="margin-top:8px">${[...new Set(notes)].join(" · ")}</p>` : "";
  $("#tog-inel")?.addEventListener("click", () => { S.showIneligible = !S.showIneligible; loadJobs(); });
  $("#tog-exp")?.addEventListener("click", () => { S.showExpired = !S.showExpired; loadJobs(); });
  const pages = Math.ceil(d.total / d.page_size);
  $("#pager").innerHTML = pages > 1 ? `<button class="btn" ${S.page <= 1 ? "disabled" : ""} id="pg-prev">Previous</button><span>Page ${S.page} of ${pages}</span><button class="btn" ${S.page >= pages ? "disabled" : ""} id="pg-next">Next</button>` : "";
  $("#pg-prev")?.addEventListener("click", () => { S.page--; loadJobs(); scrollTo(0, $("#rows").offsetTop - 80); });
  $("#pg-next")?.addEventListener("click", () => { S.page++; loadJobs(); scrollTo(0, $("#rows").offsetTop - 80); });
}
function row(j) {
  const meta = [
    j.company && `<span>${esc(j.company)}</span>`,
    `<span>${esc(j.remote_type === "remote" ? "Remote" : j.remote_type === "hybrid" ? "Hybrid" : j.remote_type === "onsite" ? "On-site" : "")}${(() => { const l = (j.location || "").replace(/^\s*remote\s*[-—–·,;:]*\s*/i, "").replace(/\s*[-—–]\s*remote\s*$/i, ""); return l ? (j.remote_type !== "unknown" ? " · " : "") + esc(l) : ""; })()}</span>`,
    `<span>${JT[j.job_type] || ""}</span>`,
    j.salary && `<span>${esc(j.salary)}</span>`,
    `<span>${ago(j.posted_at || j.first_seen_at)}</span>`,
    `<span>via ${esc(j.source)}${j.also_on.length ? ` +${j.also_on.length}` : ""}</span>`,
  ].filter(Boolean).join("");
  const tags = [
    j.is_new && `<span class="tag new">New</span>`,
    j.application_status && `<span class="tag applied">${esc(j.application_status)}</span>`,
    j.status === "expired" && `<span class="tag bad">Expired</span>`,
    j.label === "Not Eligible" && `<span class="tag bad">Not eligible</span>`,
  ].filter(Boolean).join(" ");
  return `<li class="row ${j.status === "expired" ? "expired" : ""}" data-id="${j.id}" tabindex="0">
    <div class="score ${scoreClass(j.label)}" title="${esc(j.label)}" aria-label="Match ${Math.round(j.score)} — ${esc(j.label)}">${Math.round(j.score)}</div>
    <div><p class="title">${esc(j.title)} ${tags}</p><div class="meta">${meta}</div></div>
    <div class="row-actions">
      <button class="btn" data-view="${j.id}">View</button>
      <button class="btn primary" data-apply="${j.id}" data-url="${esc(j.apply_url || "")}">Apply</button>
      <button class="btn" data-save="${j.id}" data-saved="${j.saved ? 1 : 0}" aria-pressed="${j.saved}">${j.saved ? "Saved" : "Save"}</button>
    </div></li>`;
}
$("#sort").onchange = () => { S.page = 1; loadJobs(); };
["#f-exp", "#f-salary", "#f-score", "#f-days"].forEach((s) => $(s).addEventListener("change", () => { S.page = 1; loadJobs(); }));

/* ---------------- job drawer ---------------- */
async function openJob(id) {
  let j;
  try { j = await api(`/api/jobs/${id}`); } catch (e) { return fail(e); }
  const m = j.match || {};
  const bars = Object.values(m.components || {}).map((c) => `<div class="bar"><span>${esc(c.label)}</span><span class="track"><span class="fill" style="width:${(100 * c.points) / (c.max || 1)}%"></span></span><span>${c.points} / ${c.max}</span><span class="note">${esc(c.note)}</span></div>`).join("");
  const list = (arr, empty) => arr && arr.length ? `<ul class="plain">${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="small">${empty}</p>`;
  const statuses = S.meta.statuses.map((s) => `<option ${j.application?.status === s ? "selected" : ""}>${s}</option>`).join("");
  const contacts = (j.contacts?.contacts || []);
  $("#drawer-root").innerHTML = `<div class="drawer-bg" id="dbg"></div>
  <aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="dtitle">
    <button class="close" id="dclose" aria-label="Close">×</button>
    <div style="display:flex;gap:14px;align-items:center">
      <div class="score ${scoreClass(m.label)}">${Math.round(m.score || 0)}</div>
      <div><strong>${esc(m.label || "")}</strong><div class="small">Confidence: ${esc(m.confidence || "?")} · Upskilling: ${esc(m.upskilling || "?")}</div></div>
    </div>
    <h2 id="dtitle">${esc(j.title)}</h2>
    <div class="small">${esc(j.company || "")}${j.status === "expired" ? " · <strong>Expired</strong>" : ""}</div>
    <div class="actions">
      <a class="btn" href="${esc(j.url || "#")}" target="_blank" rel="noopener noreferrer">Open posting</a>
      <button class="btn primary" id="d-apply">Apply on official page</button>
      <button class="btn" id="d-save">${j.saved ? "Saved" : "Save"}</button>
      <button class="btn" id="d-verify">Check it's still open</button>
      <button class="btn" id="d-hide">Hide</button>
    </div>
    ${(m.hard_blockers || []).length ? `<div class="notice" style="background:var(--bad-bg);color:var(--bad)"><strong>Not eligible:</strong> ${m.hard_blockers.map(esc).join("; ")}</div>` : ""}
    <h4>Why this matches</h4>${list(m.reasons, "—")}
    <h4>Your matching skills</h4>${(m.matched_skills || []).length ? `<div class="skills">${m.matched_skills.map((s) => `<span class="tag">${esc(s.replace(/^Tool: /, ""))}</span>`).join("")}</div>` : `<p class="small">No explicit skill overlap detected.</p>`}
    <h4>Missing or uncertain</h4>${list([...(m.missing || []).map((x) => "Missing: " + x), ...(m.uncertain || [])], "Nothing missing detected.")}
    <h4>Score breakdown</h4><div class="bars">${bars}</div>
    <h4>Details</h4>
    <dl class="facts">
      <dt>Location</dt><dd>${esc(m.eligibility?.note || j.location || "—")}</dd>
      <dt>Job type</dt><dd>${JT[j.job_type] || "—"}</dd>
      <dt>Salary</dt><dd>${esc(j.salary || "Not disclosed")}</dd>
      <dt>Posted</dt><dd>${fmtDate(j.posted_at) || "Unknown"}</dd>
      <dt>Deadline</dt><dd>${fmtDate(j.deadline) || "Not stated"}</dd>
      <dt>Application effort</dt><dd>${esc(m.effort || "—")}</dd>
      <dt>Languages asked</dt><dd>${esc((m.required_languages || []).join(", ") || "Not specified")}</dd>
      <dt>First seen</dt><dd>${fmtDate(j.first_seen_at)} · last checked ${fmtDate(j.last_verified_at)}</dd>
      <dt>Also posted on</dt><dd>${j.also_on.length ? j.also_on.map((a) => `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.source)}</a>`).join(", ") : "—"}</dd>
    </dl>
    <h4>Track this application</h4>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <select id="d-status" aria-label="Application status"><option value="">Not tracked</option>${statuses}</select>
      <input type="text" id="d-notes" placeholder="Notes" value="${esc(j.application?.notes || "")}" style="flex:1;min-width:160px;height:34px;border:1px solid var(--line);border-radius:8px;padding:0 8px;background:var(--surface)">
      <button class="btn" id="d-status-save">Update</button>
    </div>
    <h4>Prepare your application</h4>
    <div class="actions" style="margin-top:0">
      <button class="btn" id="d-cover">Cover letter</button>
      <a class="btn" href="/api/jobs/${j.id}/tailored-resume.docx" id="d-resume">Tailored resume (.docx)</a>
      <button class="btn" id="d-form">Form answers</button>
      <button class="btn" id="d-assist">Open form with my details</button>
    </div>
    <h4>Email</h4>
    ${j.contacts?.posting_discourages_email ? `<p class="small">This posting asks candidates not to email — apply through the official page.</p>` :
      contacts.length ? `<p class="small">Published in the posting: ${contacts.map((c) => `<strong>${esc(c.email)}</strong> (${esc(c.type.replace("_", " "))}${c.apply_by_email ? ", accepts CVs by email" : ""})`).join(", ")}</p>
      <div class="actions" style="margin-top:4px"><button class="btn" data-draft="${contacts[0].apply_by_email ? "application" : "inquiry"}">Draft email</button></div>` :
      `<p class="small">No email address is published in this posting. Use the official application page.</p>`}
    ${(j.outreach || []).length ? `<p class="small">Emails for this job: ${j.outreach.map((o) => `${esc(o.template)} → ${esc(o.recipient)} (${esc(o.status)})`).join("; ")}</p>` : ""}
    <h4>Job description</h4><div class="desc">${esc(j.description || "No description provided by the source.")}</div>
    <p class="attrib">${esc(j.attribution)} — <a href="${esc(j.url || "#")}" target="_blank" rel="noopener">view original listing</a>.</p>
  </aside>`;
  const close = () => ($("#drawer-root").innerHTML = "");
  $("#dclose").onclick = close; $("#dbg").onclick = close;
  document.addEventListener("keydown", function k(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", k); } });
  $("#dclose").focus();
  $("#d-apply").onclick = () => applyFlow(j.id, j.apply_url || j.url);
  $("#d-save").onclick = async () => { await api(`/api/jobs/${j.id}/save?saved=${!j.saved}`, { method: "POST" }); openJob(j.id); loadJobs(); };
  $("#d-hide").onclick = async () => { await api(`/api/jobs/${j.id}/hide`, { method: "POST" }); close(); loadJobs(); toast("Hidden from results"); };
  $("#d-verify").onclick = async () => { try { const r = await api(`/api/jobs/${j.id}/verify`, { method: "POST" }); toast(r.status === "expired" ? "This posting is gone — marked expired" : r.status === "active" ? "Still online" : `Couldn't confirm (HTTP ${r.http_status ?? "no response"})`); openJob(j.id); loadJobs(); } catch (e) { fail(e); } };
  $("#d-status-save").onclick = () => setStatus(j.id, $("#d-status").value, $("#d-notes").value);
  $("#d-cover").onclick = async () => { const r = await api(`/api/jobs/${j.id}/cover-letter`); textDialog("Cover letter", r.text, r.provisional ? "Drafted from your provisional profile — upload your resume first." : "Built only from your resume. Edit before using."); };
  $("#d-form").onclick = async () => { const r = await api(`/api/jobs/${j.id}/form-answers`); formDialog(r); };
  $("#d-assist").onclick = async () => { try { const r = await api(`/api/jobs/${j.id}/form-assist`, { method: "POST" }); toast(`Filled: ${r.filled.join(", ") || "nothing"}. Review every field and submit yourself.`); } catch (e) { fail(e); } };
  $("#d-resume").onclick = async (e) => { e.preventDefault(); const r = await fetch(e.currentTarget.href); if (!r.ok) return fail(new Error((await r.json()).detail)); const b = await r.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = "resume-tailored.docx"; a.click(); };
  $$("[data-draft]").forEach((b) => (b.onclick = async () => { try { const msg = await api("/api/outreach/draft", { method: "POST", body: { job_id: j.id, template: b.dataset.draft } }); emailDialog(msg); } catch (e) { fail(e); } }));
}
async function setStatus(id, status, notes, reapply = false) {
  if (!status) return;
  try { await api(`/api/jobs/${id}/status`, { method: "POST", body: { status, notes, reapply } }); toast(`Marked as ${status}`); loadJobs(); if (!$("#drawer-root").innerHTML) return; openJob(id); }
  catch (e) {
    if (/already applied/i.test(e.message) && confirm(e.message + "\n\nRecord another attempt?")) return setStatus(id, status, notes, true);
    fail(e);
  }
}
function applyFlow(id, url) {
  if (!url) return toast("This listing has no application link", true);
  window.open(url, "_blank", "noopener");
  dialog(`<h3>Did you submit the application?</h3>
    <p>The official page opened in a new tab. Only mark it applied once you've actually submitted it.</p>
    <label class="small" for="conf">Confirmation number or note (optional)</label>
    <input type="text" id="conf" style="width:100%;border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--surface)">
    <div class="dlg-actions"><button class="btn" data-close>Not yet</button><button class="btn" id="mk-ready">Mark ready to apply</button><button class="btn primary" id="mk-applied">Yes, I submitted it</button></div>`);
  $("#mk-applied").onclick = async () => { const c = $("#conf").value; $("#dlg").close(); try { await api(`/api/jobs/${id}/status`, { method: "POST", body: { status: "Applied", method: "Official page", confirmation: c || null } }); toast("Marked as applied"); loadJobs(); } catch (e) { if (/already applied/i.test(e.message) && confirm(e.message + "\n\nRecord another attempt?")) { await api(`/api/jobs/${id}/status`, { method: "POST", body: { status: "Applied", method: "Official page", confirmation: c || null, reapply: true } }); loadJobs(); } else fail(e); } };
  $("#mk-ready").onclick = async () => { $("#dlg").close(); await setStatus(id, "Ready to Apply"); };
}

/* ---------------- dialogs ---------------- */
function dialog(html) {
  $("#dlg-body").innerHTML = html;
  $$("[data-close]", $("#dlg-body")).forEach((b) => (b.onclick = () => $("#dlg").close()));
  $("#dlg").showModal();
}
function textDialog(title, text, note) {
  dialog(`<h3>${esc(title)}</h3>${note ? `<p class="small">${esc(note)}</p>` : ""}<textarea id="dlg-text">${esc(text)}</textarea>
  <div class="dlg-actions"><button class="btn" data-close>Close</button><button class="btn primary" id="dlg-copy">Copy</button></div>`);
  $("#dlg-copy").onclick = async () => { await navigator.clipboard.writeText($("#dlg-text").value); toast("Copied"); };
}
function formDialog(r) {
  const pre = Object.entries(r.prefilled).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td>${v == null || (Array.isArray(v) && !v.length) ? `<em class="small">not in your resume</em>` : esc(Array.isArray(v) ? v.join(", ") : v)}</td></tr>`).join("");
  const need = r.needs_your_input.map((n) => `<li><strong>${esc(n.label)}</strong> — <span class="small">${esc(n.why)}</span></li>`).join("");
  dialog(`<h3>Application form answers</h3>${r.warning ? `<div class="notice">${esc(r.warning)}</div>` : ""}
  <table class="t">${pre}</table><h4>Only you can answer these</h4><ul class="plain">${need}</ul>
  <div class="dlg-actions"><button class="btn primary" data-close>Done</button></div>`);
}
function emailDialog(msg) {
  const canSend = msg.status !== "draft";
  dialog(`<h3>Email draft</h3>
  <p class="small">To <strong>${esc(msg.recipient)}</strong> (${esc(msg.recipient_source)}). Status: ${esc(msg.status)}${msg.error ? ` — ${esc(msg.error)}` : ""}.</p>
  <label class="small" for="em-subj">Subject</label><input id="em-subj" value="${esc(msg.subject)}" style="width:100%;border:1px solid var(--line);border-radius:8px;padding:8px;margin-bottom:8px;background:var(--surface)">
  <textarea id="em-body">${esc(msg.body)}</textarea>
  <div class="dlg-actions"><button class="btn" data-close>Close</button><button class="btn" id="em-copy">Copy</button><button class="btn" id="em-save">Save changes</button>
  ${canSend || S.outMode !== "draft_only" ? `<button class="btn primary" id="em-approve">Approve & send</button>` : `<span class="small">Drafts only mode — change mode in Outreach to send.</span>`}</div>`);
  $("#em-copy").onclick = async () => { await navigator.clipboard.writeText(`Subject: ${$("#em-subj").value}\n\n${$("#em-body").value}`); toast("Copied"); };
  $("#em-save").onclick = async () => { try { await api(`/api/outreach/messages/${msg.id}`, { method: "PUT", body: { subject: $("#em-subj").value, body: $("#em-body").value } }); toast("Saved"); } catch (e) { fail(e); } };
  $("#em-approve")?.addEventListener("click", async () => {
    try { await api(`/api/outreach/messages/${msg.id}`, { method: "PUT", body: { subject: $("#em-subj").value, body: $("#em-body").value } });
      await api(`/api/outreach/messages/${msg.id}/approve`, { method: "POST" }); const r = await api("/api/outreach/process", { method: "POST" });
      $("#dlg").close(); toast(r.sent ? "Sent" : r.deferred ? "Queued — will send after quiet hours / daily limit" : "Queued"); } catch (e) { fail(e); }
  });
}

/* ---------------- tracker ---------------- */
async function loadTracker() {
  const sel = $("#tr-filter");
  if (!sel.options.length) { sel.innerHTML = `<option value="">All statuses</option>` + S.meta.statuses.map((s) => `<option>${s}</option>`).join(""); sel.onchange = loadTracker; }
  const rows = await api(`/api/applications${sel.value ? `?status=${encodeURIComponent(sel.value)}` : ""}`);
  $("#tr-table").innerHTML = rows.length ? `<thead><tr><th>Role</th><th>Status</th><th>Score</th><th>Method</th><th>Applied</th><th>Follow-up</th><th>Notes</th><th></th></tr></thead><tbody>` +
    rows.map((a) => `<tr><td><a href="#" data-job="${a.job_id}">${esc(a.title)}</a><div class="small">${esc(a.company || "")} · ${esc(a.source || "")}${a.job_status === "expired" ? " · expired" : ""}</div></td>
      <td><select data-st="${a.job_id}" aria-label="Status">${S.meta.statuses.map((s) => `<option ${a.status === s ? "selected" : ""}>${s}</option>`).join("")}</select>${a.status_display !== a.status ? `<div class="small" style="color:var(--warn)">${esc(a.status_display)}</div>` : ""}</td>
      <td>${Math.round(a.match_score || 0)}</td><td>${esc(a.method || "—")}</td><td>${fmtDate(a.applied_at) || "—"}</td><td>${fmtDate(a.followup_due) || "—"}</td>
      <td>${esc(a.notes || "")}${a.confirmation ? `<div class="small">Ref: ${esc(a.confirmation)}</div>` : ""}</td>
      <td><button class="linkish" data-del="${a.id}">Remove</button></td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty"><strong>Nothing tracked yet</strong>Save or apply to a job and it appears here.</td></tr></tbody>`;
  $$("[data-job]", $("#tr-table")).forEach((a) => (a.onclick = (e) => { e.preventDefault(); openJob(a.dataset.job); }));
  $$("[data-st]", $("#tr-table")).forEach((s) => (s.onchange = async () => { await setStatus(s.dataset.st, s.value); loadTracker(); }));
  $$("[data-del]", $("#tr-table")).forEach((b) => (b.onclick = async () => { if (confirm("Remove from tracker?")) { await api(`/api/applications/${b.dataset.del}`, { method: "DELETE" }); loadTracker(); } }));
}

/* ---------------- outreach ---------------- */
async function loadOutreach() {
  const s = await api("/api/outreach/settings");
  S.outMode = s.mode;
  $("#out-stop").innerHTML = s.stopped
    ? `<div class="notice" style="background:var(--bad-bg);color:var(--bad)"><strong>Sending is stopped.</strong> No email will go out until you resume. <button class="btn" id="out-resume" style="margin-left:8px">Resume sending</button></div>`
    : s.mode === "draft_only" ? `<div class="notice info">Drafts only — nothing is ever sent from this app.</div>`
    : `<div class="notice info">Sending is on (${s.mode === "auto" ? "automatic within your rules" : "after your approval"}). <button class="btn danger" id="out-stopbtn" style="margin-left:8px">Emergency STOP</button></div>`;
  $("#out-resume")?.addEventListener("click", async () => { await api("/api/outreach/resume", { method: "POST" }); loadOutreach(); refreshStatus(); });
  $("#out-stopbtn")?.addEventListener("click", async () => { const r = await api("/api/outreach/stop", { method: "POST" }); toast(`Stopped — ${r.cancelled || 0} queued emails cancelled`); loadOutreach(); refreshStatus(); });
  const modes = [["draft_only", "Drafts only", "Nothing is ever sent. Copy drafts and send yourself."], ["review", "Review before sending", "Each email waits for your approval."], ["auto", "Automatic within rules", "Emails that pass every rule below are sent without asking."]];
  $("#out-modes").innerHTML = modes.map(([v, t, d]) => `<label class="mode-opt"><input type="radio" name="mode" value="${v}" ${s.mode === v ? "checked" : ""} class="sr-only"><strong>${t}</strong><span>${d}</span></label>`).join("");
  $$("#out-modes input").forEach((i) => (i.onchange = async () => { try { await api("/api/outreach/settings", { method: "PUT", body: { mode: i.value } }); toast("Mode saved"); refreshStatus(); } catch (e) { fail(e); } loadOutreach(); }));
  $("#out-account").textContent = s.account_configured ? `Connected (${s.provider === "gmail" ? "Gmail" : "SMTP"}): ${s.from_email}` : S.cloud
    ? "No account connected. Online, use Connect Gmail — SMTP app passwords are blocked by the free host (see DEPLOY.md)."
    : "No account connected. Use an SMTP app password or Connect Gmail (see README).";
  const cats = S.meta.categories;
  const q = s.quiet_hours || {};
  $("#out-rules").innerHTML = `
    <div class="full"><label>Approved job categories</label><div class="ms" id="r-cats"></div></div>
    <div><label for="r-min">Minimum match score</label><input id="r-min" type="number" min="0" max="100" value="${s.min_score}"></div>
    <div><label>Approved recipients</label><div class="ms" id="r-rtypes"></div></div>
    <div><label for="r-day">Maximum emails per day</label><input id="r-day" type="number" min="0" max="25" value="${s.max_per_day}"></div>
    <div><label for="r-co">Maximum emails per company</label><input id="r-co" type="number" min="0" max="3" value="${s.max_per_company}"></div>
    <div><label>Permitted templates</label><div class="ms" id="r-tpl"></div></div>
    <div><label for="r-provider">Send with</label><select id="r-provider"><option value="smtp" ${s.provider === "smtp" ? "selected" : ""}>SMTP</option><option value="gmail" ${s.provider === "gmail" ? "selected" : ""}>Gmail</option></select></div>
    <div><label class="check"><input type="checkbox" id="r-attach" ${s.attach_resume ? "checked" : ""}> Attach my resume</label>
         <label class="check"><input type="checkbox" id="r-invite" ${s.only_when_posting_invites_email ? "checked" : ""}> Send applications only when the posting invites email</label></div>
    <div><label class="check"><input type="checkbox" id="r-fu" ${s.followups_allowed ? "checked" : ""}> Allow follow-ups</label>
         <label class="small" for="r-fumax">Max follow-ups / days to wait</label>
         <div style="display:flex;gap:6px"><input id="r-fumax" type="number" min="0" max="2" value="${s.max_followups}"><input id="r-fudays" type="number" min="3" max="30" value="${s.followup_after_days}"></div></div>
    <div><label class="check"><input type="checkbox" id="r-quiet" ${q.enabled ? "checked" : ""}> Quiet hours</label>
         <div style="display:flex;gap:6px"><input id="r-qs" type="time" value="${esc(q.start || "21:00")}"><input id="r-qe" type="time" value="${esc(q.end || "09:00")}"></div></div>
    <div><label for="r-stop">Auto-stop after consecutive failures</label><input id="r-stop" type="number" min="1" max="10" value="${(s.stop_conditions || {}).max_consecutive_failures || 3}"></div>
    <div class="full"><label for="r-bco">Never contact these companies (one per line)</label><textarea id="r-bco">${esc((s.blocked_companies || []).join("\n"))}</textarea></div>
    <div class="full"><label for="r-bre">Never contact these addresses or domains (one per line)</label><textarea id="r-bre">${esc((s.blocked_recipients || []).join("\n"))}</textarea></div>`;
  multiSelect($("#r-cats"), cats, s.approved_categories, "None — automatic sending disabled");
  multiSelect($("#r-rtypes"), ["careers_inbox", "recruiter", "hiring_contact"], s.recipient_types, "None", { careers_inbox: "Careers/jobs inbox", recruiter: "Recruiter", hiring_contact: "Other hiring contact" });
  multiSelect($("#r-tpl"), ["application", "inquiry", "followup"], s.templates, "None", { application: "Application (CV by email)", inquiry: "Interest inquiry", followup: "Follow-up" });
  const msgs = await api("/api/outreach/messages");
  $("#out-table").innerHTML = msgs.length ? `<thead><tr><th>When</th><th>To</th><th>Company</th><th>Template</th><th>Status</th><th></th></tr></thead><tbody>` +
    msgs.map((m) => `<tr><td>${fmtDate(m.sent_at || m.created_at)}</td><td>${esc(m.recipient)}<div class="small">${esc(m.recipient_source || "")}</div></td><td>${esc(m.company || "")}</td><td>${esc(m.template)}${m.followup_number ? ` #${m.followup_number}` : ""}</td>
      <td>${esc(m.status)}${m.error ? `<div class="small">${esc(m.error)}</div>` : ""}</td>
      <td>${["draft", "pending_review", "blocked"].includes(m.status) ? `<button class="linkish" data-open="${m.id}">Open</button> · ` : ""}${["draft", "pending_review", "queued", "blocked"].includes(m.status) ? `<button class="linkish" data-cancel="${m.id}">Cancel</button> · ` : ""}<button class="linkish" data-opt="${esc(m.recipient)}">Do not contact</button></td></tr>`).join("") + "</tbody>"
    : `<tbody><tr><td class="empty"><strong>No emails yet</strong>Open a job with a published contact and choose “Draft email”.</td></tr></tbody>`;
  $$("[data-open]", $("#out-table")).forEach((b) => (b.onclick = () => emailDialog(msgs.find((m) => m.id == b.dataset.open))));
  $$("[data-cancel]", $("#out-table")).forEach((b) => (b.onclick = async () => { await api(`/api/outreach/messages/${b.dataset.cancel}/cancel`, { method: "POST" }); loadOutreach(); }));
  $$("[data-opt]", $("#out-table")).forEach((b) => (b.onclick = async () => { await api("/api/outreach/optout", { method: "POST", body: { email: b.dataset.opt } }); toast("Added to do-not-contact list"); loadOutreach(); }));
  const sup = await api("/api/outreach/suppression");
  $("#optout-list").innerHTML = sup.map((x) => `<li>${esc(x.email)} — ${esc(x.reason)} (${fmtDate(x.created_at)})</li>`).join("") || "<li>Empty</li>";
}
$("#out-save").onclick = async () => {
  const lines = (id) => $(id).value.split("\n").map((x) => x.trim()).filter(Boolean);
  try {
    await api("/api/outreach/settings", { method: "PUT", body: {
      approved_categories: $("#r-cats").values(), min_score: +$("#r-min").value, recipient_types: $("#r-rtypes").values(),
      max_per_day: +$("#r-day").value, max_per_company: +$("#r-co").value, templates: $("#r-tpl").values(), provider: $("#r-provider").value,
      attach_resume: $("#r-attach").checked, only_when_posting_invites_email: $("#r-invite").checked, followups_allowed: $("#r-fu").checked,
      max_followups: +$("#r-fumax").value, followup_after_days: +$("#r-fudays").value,
      quiet_hours: { enabled: $("#r-quiet").checked, start: $("#r-qs").value, end: $("#r-qe").value, tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata" },
      stop_conditions: { max_consecutive_failures: +$("#r-stop").value, max_failures_per_day: 5 },
      blocked_companies: lines("#r-bco"), blocked_recipients: lines("#r-bre"),
    } });
    toast("Rules saved"); loadOutreach();
  } catch (e) { fail(e); }
};
$("#optout-add").onclick = async () => { const v = $("#optout-email").value.trim(); if (!v) return; await api("/api/outreach/optout", { method: "POST", body: { email: v, reason: "Added by you" } }); $("#optout-email").value = ""; loadOutreach(); };
$("#out-test").onclick = async () => { try { const r = await api("/api/email/test", { method: "POST" }); toast(`Test email sent to ${r.sent_to}`); } catch (e) { fail(e); } };
$("#out-smtp").onclick = () => { show("settings"); setTimeout(() => { const t = $("#keys-table"); t.closest("details")?.setAttribute("open", ""); t.scrollIntoView({ behavior: "smooth" }); }, 250); };

/* ---------------- sources ---------------- */
async function loadSources() {
  const rows = await api("/api/sources");
  $("#src-table").innerHTML = `<thead><tr><th>On</th><th>Source</th><th>Status</th><th>Access & limits</th><th>Terms</th><th></th></tr></thead><tbody>` + rows.map((s) => {
    const listCfg = s.config.boards ? s.config.boards.map((b) => `${b.token}${b.company ? " | " + b.company : ""}${b.eu ? " | eu" : ""}`).join("\n")
      : s.config.feeds ? s.config.feeds.map((f) => `${f.url}${f.name ? " | " + f.name : ""}`).join("\n")
      : s.config.pages ? s.config.pages.map((p) => `${p.url}${p.company ? " | " + p.company : ""}`).join("\n") : null;
    const key = s.config.boards ? "boards" : s.config.feeds ? "feeds" : s.config.pages ? "pages" : null;
    return `<tr>
      <td><input type="checkbox" data-en="${s.id}" ${s.enabled ? "checked" : ""} aria-label="Enable ${esc(s.name)}"></td>
      <td><strong>${esc(s.name)}</strong><div class="small">${esc(s.meta.source_type)} · <a href="${esc(s.meta.docs_url)}" target="_blank" rel="noopener">docs</a></div>
        <div class="small">${esc(s.meta.coverage)}</div>
        ${key ? `<details class="more"><summary>Edit list (${key})</summary><textarea data-cfg="${s.id}" data-key="${key}" rows="4">${esc(listCfg)}</textarea><button class="btn" data-cfgsave="${s.id}">Save list</button><div class="small">One per line: ${key === "boards" ? "token | Company name | eu (optional)" : key === "feeds" ? "feed URL | name" : "page URL | company"}</div></details>` : ""}</td>
      <td><span class="health ${s.health}">${esc({ ok: "Working", error: "Failing", rate_limited: "Rate limited", needs_key: "Needs key", unknown: "Not checked" }[s.health] || s.health)}</span>
        <div class="small">${s.last_success_at ? "Last OK " + fmtDate(s.last_success_at) + ` (${s.last_count})` : ""}</div>
        ${s.last_error ? `<div class="small" title="${esc(s.last_error)}">${esc(s.last_error.slice(0, 90))}</div>` : ""}</td>
      <td class="small">${esc(s.meta.auth)}<br>${esc(s.meta.free_tier)}<br>${esc(s.meta.rate_limit)}${s.missing_keys.length ? `<br><strong>Add in Settings: ${esc(s.missing_keys.join(", "))}</strong>` : ""}</td>
      <td class="small">${esc(s.meta.terms)}<div><em>${esc(s.meta.verification)}</em></div></td>
      <td><button class="btn" data-health="${s.id}">Check now</button>${s.builtin ? "" : ` <button class="linkish" data-rm="${s.id}">Remove</button>`}</td></tr>`;
  }).join("") + "</tbody>";
  $$("[data-en]").forEach((c) => (c.onchange = async () => { await api(`/api/sources/${c.dataset.en}`, { method: "PUT", body: { enabled: c.checked } }); refreshStatus(); }));
  $$("[data-health]").forEach((b) => (b.onclick = async () => { b.disabled = true; b.textContent = "Checking…"; try { const r = await api(`/api/sources/${b.dataset.health}/health`, { method: "POST" }); toast(r.message, !r.ok); } catch (e) { fail(e); } loadSources(); refreshStatus(); }));
  $$("[data-rm]").forEach((b) => (b.onclick = async () => { if (confirm("Remove this source?")) { await api(`/api/sources/${b.dataset.rm}`, { method: "DELETE" }); loadSources(); } }));
  $$("[data-cfgsave]").forEach((b) => (b.onclick = async () => {
    const ta = $(`[data-cfg="${b.dataset.cfgsave}"]`), key = ta.dataset.key;
    const items = ta.value.split("\n").map((l) => l.split("|").map((x) => x.trim())).filter((p) => p[0]);
    const cfg = { [key]: items.map((p) => key === "boards" ? { token: p[0], company: p[1] || p[0], eu: (p[2] || "").toLowerCase() === "eu" } : key === "feeds" ? { url: p[0], name: p[1] || p[0] } : { url: p[0], company: p[1] || "" }) };
    try { await api(`/api/sources/${b.dataset.cfgsave}`, { method: "PUT", body: { config: cfg } }); toast("Saved — press Check now to test it"); loadSources(); } catch (e) { fail(e); }
  }));
  loadManualLinks();
}
const SA_LABEL = { greenhouse: ["Board token", "e.g. acme (from boards.greenhouse.io/acme)"], lever: ["Lever site name", "e.g. acme (from jobs.lever.co/acme)"], ashby: ["Ashby board name", "e.g. acme (from jobs.ashbyhq.com/acme)"], workable: ["Workable account", "e.g. acme (from apply.workable.com/acme)"], smartrecruiters: ["Company identifier", "e.g. Acme1 (from jobs.smartrecruiters.com/Acme1)"], rss: ["Feed URL", "https://…/jobs.rss"], careers: ["Career page URL", "https://company.com/careers"] };
$("#sa-kind").onchange = () => { const [l, p] = SA_LABEL[$("#sa-kind").value]; $("#sa-value-lbl").textContent = l; $("#sa-value").placeholder = p; };
$("#src-add").onsubmit = async (e) => {
  e.preventDefault();
  const kind = $("#sa-kind").value, v = $("#sa-value").value.trim(), name = $("#sa-name").value.trim() || v;
  if (!v) return toast("Enter a value", true);
  const cfg = kind === "rss" ? { feeds: [{ url: v, name }] } : kind === "careers" ? { pages: [{ url: v, company: name }] } : { boards: [{ token: v, company: name }] };
  try { await api("/api/sources", { method: "POST", body: { kind, name, config: cfg } }); toast("Source added — press Check now to test it"); $("#sa-value").value = ""; $("#sa-name").value = ""; loadSources(); } catch (e) { fail(e); }
};
async function loadManualLinks() {
  const links = await api(`/api/manual-links?q=${encodeURIComponent($("#ml-q").value)}`);
  $("#manual-links").innerHTML = links.map((l) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.name)}</a> <span class="small">— ${esc(l.why)}</span></li>`).join("");
}
$("#ml-q").addEventListener("change", loadManualLinks);

/* ---------------- settings ---------------- */
const PROFILE_FIELDS = [
  ["name", "Name", "text"], ["email", "Email", "text"], ["phone", "Phone", "text"], ["location", "Location", "text"],
  ["total_years_experience", "Total years of experience", "number"], ["headline", "Headline", "text"],
  ["languages", "Languages (e.g. Hindi: Native)", "langs"], ["skills", "Skills (one per line)", "list"],
  ["tools", "Tools (one per line)", "list"], ["industries", "Industries (one per line)", "list"],
  ["target_roles", "Target roles (one per line)", "list"], ["job_titles", "Past job titles (one per line)", "list"],
  ["certifications", "Certifications (one per line)", "list"], ["summary", "Summary", "area"],
];
async function loadSettings() {
  const [{ profile: p, status: st }, s] = await Promise.all([api("/api/profile"), api("/api/settings")]);
  $("#resume-status").textContent = st.verified ? `Using ${st.filename}, parsed ${fmtDate(st.updated_at)}.` : "No resume uploaded — a provisional profile from your project brief is being used.";
  $("#profile-form").innerHTML = PROFILE_FIELDS.map(([k, l, t]) => {
    const v = p[k];
    const val = t === "list" ? (v || []).join("\n") : t === "langs" ? (v || []).map((x) => `${x.language}: ${x.level}`).join("\n") : v ?? "";
    const full = ["list", "langs", "area"].includes(t) ? "" : "";
    return `<div class="${t === "area" ? "full" : full}"><label for="pf-${k}">${l}</label>${t === "list" || t === "langs" || t === "area" ? `<textarea id="pf-${k}">${esc(val)}</textarea>` : `<input id="pf-${k}" type="${t}" step="0.5" value="${esc(val)}">`}</div>`;
  }).join("");
  $("#profile-notes").innerHTML = (p.notes || []).map((n) => `<div class="notice">${esc(n)}</div>`).join("") +
    (p.work_history?.length ? `<details class="more"><summary>Work history found (${p.work_history.length})</summary><ul class="plain small">${p.work_history.map((w) => `<li>${esc(w.line)}</li>`).join("")}</ul></details>` : "") +
    (p.education?.length ? `<details class="more"><summary>Education found (${p.education.length})</summary><ul class="plain small">${p.education.map((e) => `<li>${esc(e.text)}</li>`).join("")}</ul></details>` : "");
  const labels = S.meta.component_labels;
  $("#weights-form").innerHTML = Object.entries(s.weights).map(([k, v]) => `<div><label for="w-${k}">${esc(labels[k])}</label><input id="w-${k}" data-w="${k}" type="number" min="0" max="100" value="${v}"></div>`).join("");
  $("#use-emb").checked = s.use_embeddings;
  $("#keys-table").innerHTML = `<tbody>` + Object.entries(s.secrets).map(([k, v]) => `<tr><td><strong>${esc(k)}</strong><div class="small">${esc(v.description)}</div></td>
    <td>${v.set ? (v.from_env ? "Set in .env" : "Saved") : "Not set"}</td>
    <td><div style="display:flex;gap:6px"><input type="${/PASS|KEY|SECRET/.test(k) ? "password" : "text"}" data-secret="${k}" placeholder="${v.set ? "Replace…" : "Enter value"}" aria-label="${esc(k)}" autocomplete="off"><button class="btn" data-secret-save="${k}">Save</button></div></td></tr>`).join("") + `</tbody>`;
  $$("[data-secret-save]").forEach((b) => (b.onclick = async () => { const i = $(`[data-secret="${b.dataset.secretSave}"]`); try { await api("/api/secrets", { method: "PUT", body: { name: b.dataset.secretSave, value: i.value } }); i.value = ""; toast("Saved"); loadSettings(); refreshStatus(); } catch (e) { fail(e); } }));
  $("#sch-on").checked = s.schedule.enabled; $("#sch-time").value = s.schedule.time;
  const audit = await api("/api/audit?limit=80");
  $("#audit-table").innerHTML = `<tbody>${audit.map((a) => `<tr><td class="small">${new Date(a.ts).toLocaleString()}</td><td>${esc(a.category)}</td><td>${esc(a.action)}</td><td class="small">${esc(JSON.stringify(a.detail)).slice(0, 160)}</td></tr>`).join("")}</tbody>`;
}
$("#resume-file").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  toast("Reading your resume…");
  try { const r = await api("/api/resume", { method: "POST", body: fd }); toast(`Resume parsed: ${r.profile.skills.length} skills, ${r.profile.total_years_experience ?? "?"} years. Re-scoring jobs…`);
    await api("/api/jobs/rescore", { method: "POST" }); await init(true); loadSettings(); } catch (err) { fail(err); }
  e.target.value = "";
};
$("#resume-delete").onclick = async () => { if (!confirm("Delete your uploaded resume and parsed profile from this computer?")) return; await api("/api/resume", { method: "DELETE" }); toast("Resume deleted"); loadSettings(); refreshStatus(); };
$("#profile-save").onclick = async () => {
  const body = {};
  PROFILE_FIELDS.forEach(([k, , t]) => {
    const v = $(`#pf-${k}`).value;
    if (t === "list") body[k] = v.split("\n").map((x) => x.trim()).filter(Boolean);
    else if (t === "langs") body[k] = v.split("\n").map((x) => x.split(":")).filter((p) => p[0].trim()).map((p) => ({ language: p[0].trim(), level: (p[1] || "Stated").trim() }));
    else if (t === "number") body[k] = v === "" ? null : Number(v);
    else body[k] = v;
  });
  try { await api("/api/profile", { method: "PUT", body }); toast("Profile saved — press Re-score to update existing jobs"); } catch (e) { fail(e); }
};
$("#rescore").onclick = async () => { const r = await api("/api/jobs/rescore", { method: "POST" }); toast(`Re-scored ${r.rescored} jobs`); loadJobs(); };
$("#weights-save").onclick = async () => { const w = {}; $$("[data-w]").forEach((i) => (w[i.dataset.w] = Number(i.value))); try { await api("/api/settings", { method: "PUT", body: { weights: w, use_embeddings: $("#use-emb").checked } }); toast("Weights saved — re-score to apply"); } catch (e) { fail(e); } };
$("#weights-reset").onclick = async () => { await api("/api/settings", { method: "PUT", body: { weights: S.meta.default_weights } }); loadSettings(); toast("Defaults restored"); };
$("#sch-save").onclick = async () => { await api("/api/settings", { method: "PUT", body: { schedule: { enabled: $("#sch-on").checked, time: $("#sch-time").value, tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata" } } }); toast($("#sch-on").checked ? `Daily search set for ${$("#sch-time").value}` : "Daily search off"); };
$("#del-history").onclick = async () => { if (confirm("Delete all search results and history? Tracked applications are kept.")) { await api("/api/data", { method: "DELETE" }); toast("History deleted"); loadJobs(); } };
$("#del-all").onclick = async () => { if (confirm("Delete ALL jobs, the application tracker and email history? This cannot be undone.")) { await api("/api/data?include_tracker=true", { method: "DELETE" }); toast("Everything deleted"); loadJobs(); } };

/* ---------------- boot ---------------- */
async function init(refreshOnly = false) {
  S.meta = await api("/api/meta");
  const last = await api("/api/status");
  const defaults = S.meta.default_categories.length ? S.meta.default_categories : S.meta.categories.slice(0, 10);
  multiSelect($("#f-categories"), S.meta.categories, defaults, "All categories");
  multiSelect($("#f-jobtypes"), S.meta.job_types, [], "Any type", JT);
  multiSelect($("#f-locations"), S.meta.locations, ["Remote (Worldwide)", "India"], "Anywhere");
  multiSelect($("#f-workmodes"), S.meta.work_modes, ["remote", "hybrid", "onsite"], "Any", { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" });
  $("#f-exp").innerHTML = S.meta.experience_levels.map((l) => `<option value="${l}">${l === "any" ? "Any level" : l[0].toUpperCase() + l.slice(1)}</option>`).join("");
  ["#f-categories", "#f-jobtypes", "#f-locations", "#f-workmodes"].forEach((s) => $(s).addEventListener("change", () => { S.page = 1; loadJobs(); }));
  $("#search-sub").textContent = defaults.length && !refreshOnly ? `Pre-set to ${defaults.length} roles from your ${last.profile.verified ? "resume" : "profile"}. Tweak anything, then hit GO.` : $("#search-sub").textContent;
  if (!refreshOnly) renderQuick();
  await refreshStatus();
  if (last.search) { renderProgress(last.search); if (last.search.status === "running") poll(); }
  loadJobs();
}
init().then(() => { const h = location.hash.slice(1); if (h && h !== "jobs") show(h); });
