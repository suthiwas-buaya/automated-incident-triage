'use strict';

/* ============================================================
   API — real backend now (Laravel). Token is in-memory only
   (module-level, not in `state`) so it's gone on refresh, same
   intent as the old mock's "everything gone on refresh" — except
   now the tickets themselves persist server-side.

   127.0.0.1, not "localhost" — php artisan serve only binds the
   IPv4 socket, but "localhost" resolves to both ::1 and 127.0.0.1;
   Chromium's happy-eyeballs races them and the IPv6 attempt sitting
   there unanswered before falling back added ~700ms to every single
   request. 127.0.0.1 skips DNS/hosts resolution entirely.
   ============================================================ */

const API_BASE = 'http://127.0.0.1:8000';
let authToken = null;
// handle ของ setInterval สำหรับ polling loop (null เมื่อยังไม่ login หรือ logout แล้ว)
let pollingInterval = null;
// Set ของ ticket id ที่ user dismiss overdue banner แล้ว — module-level ไม่ใช่ state
// เพื่อไม่ให้ trigger re-render เมื่อ dismiss (ไม่ต้องการ cycle ผ่าน setState)
const dismissedOverdueBanners = new Set();

// Set ของ ticket id ที่ overdue อยู่แล้ว ณ การเช็คครั้งล่าสุด — ใช้ diff หา "ใบใหม่ที่
// เพิ่งข้ามเกณฑ์" สำหรับยิง desktop notification เท่านั้น (ไม่เกี่ยวกับ badge/banner
// ที่แสดงจำนวนจริงเสมอ) เริ่มเป็น null แปลว่า "ยังไม่เคยมี baseline" — การเช็คครั้งแรก
// หลัง login ต้องแค่บันทึก baseline ห้ามยิง notification เลย ไม่งั้น ticket ค้างเก่าๆ
// ที่มีอยู่แล้ว 225 ใบจะยิง notification รัวๆ ทันทีที่ login
let knownOverdueIds = null;

// incident_number ที่เพิ่งโผล่มาในรอบ poll ล่าสุด — module-level เหมือน Set อื่นๆ ข้างบน
// (ไม่ต้องผ่าน setState เพราะแค่ใช้ highlight แถวใหม่ ~10 วิ ไม่ใช่ค่าที่ diff กับอะไร)
const newTicketIds = new Set();

async function apiFetch(path, options) {
  const opts = Object.assign({}, options);
  opts.headers = Object.assign(
    { 'Accept': 'application/json' },
    authToken ? { 'Authorization': 'Bearer ' + authToken } : {},
    opts.body ? { 'Content-Type': 'application/json' } : {},
    opts.headers || {}
  );

  let response;
  try {
    response = await fetch(API_BASE + path, opts);
  } catch (err) {
    throw new Error('Cannot reach the server: ' + err.message);
  }

  const hasBody = response.status !== 204;
  const data = hasBody ? await response.json().catch(() => null) : null;

  // 401 = token ใช้ไม่ได้แล้ว (หมดอายุจากการไม่ใช้งาน 30 นาที หรือ API ถูก restart)
  // ต้องเด้งกลับหน้า login ทันที ไม่ใช่โยน "Unauthorized" ดิบๆ ไปโผล่กลางฟอร์ม
  // ซึ่งอ่านแล้วไม่รู้ว่าต้องทำอะไรต่อ และดูเหมือนระบบพังทั้งที่เป็นพฤติกรรมที่ถูกต้อง
  if (response.status === 401 && authToken) {
    forceLogout('เซสชันหมดอายุ กรุณาเข้าสู่ระบบใหม่');
    throw new Error('เซสชันหมดอายุ');
  }

  if (!response.ok) {
    throw new Error((data && data.message) || ('Request failed (' + response.status + ')'));
  }
  return data;
}

/* ล้าง session ฝั่ง browser แล้วกลับไปหน้า login พร้อมข้อความอธิบาย
   ไม่เรียก /api/logout เพราะ token ฝั่ง server ตายไปแล้ว เรียกไปก็ได้ 401 ซ้ำ */
function forceLogout(message) {
  authToken = null;
  if (typeof stopPolling === 'function') stopPolling();
  setState({
    view: 'login',
    loginError: message,
    username: '', password: '',
    firstName: '', lastName: '', team: null, isAdmin: false,
    uploadedGroups: null, uploadedUnassigned: [], uploadInfo: '',
    pendingUsers: [], pendingReview: [], activityLog: [],
    selected: {}, confirmDialog: null, manualEntryOpen: false,
    inProgressDetailId: null, analyzeDetailId: null,
    errorMessage: '',
  });
}

/* ============================================================
   State
   ============================================================ */

const state = {
  view: 'login', // 'login' | 'register' | 'dashboard' | 'triage' | 'detail' | 'inprogress' | 'inprogress-detail' | 'admin'
  // TEMP DEV BYPASS (ช่วงกำลังแก้ระบบ) — prefill ด้วย seed admin account (app.py
  // seed_users_if_empty) เพื่อกด Enter เข้าได้เลยไม่ต้องพิมพ์ ลบ default 2 ค่านี้ทิ้ง
  // (คืนเป็น '') ก่อน demo/ส่งงานจริง ไม่งั้น password เห็นเต็มๆ ในหน้า login
  username: 'admin', password: 'admin123', isVisible: false, loginError: '',
  firstName: '', lastName: '', team: null, isAdmin: false, // filled from the /auth/login response, not typed
  sidebarOpen: true,
  detailTag: 'Network-Core',
  selected: {},
  uploadedGroups: null, uploadedUnmatched: 0, uploadedUnassigned: [],
  uploadInfo: '',
  // "แจ้งเหตุด้วยตนเอง" (manual entry, BRIEF ข้อ 7) — แทนที่ import Excel/CSV เดิม
  manualEntryOpen: false,
  manualNode: '', manualInterface: '', manualDescription: '', manualGroup: '', manualPriority: '',
  manualError: '',
  detailSearch: '',
  detailOverdueFilter: false, // true เมื่อ user กด "Show only" ใน overdue banner
  detailPage: 1, triagePage: 1,
  errorMessage: '', // API failures surfaced regardless of which view is open
  busy: false, // true while any request from runBusy() is in flight - drives the top loading bar and blocks re-entrant clicks
  confirmDialog: null, // { message, confirmLabel, onConfirm } - set by askConfirm(), cleared on confirm/cancel
  inProgressDetailId: null, // ticket id currently open on the full detail page ('inprogress-detail' view), or null
  viewingStep: 'received', // which step tab is selected on the detail page - view-only, never changes ticket status
  inProgressDetailEditing: false, // true while the viewed step's fields are unlocked for editing
  detailNoteDraft: '', detailEventTimeDraft: '',
  detailTechnicianName: '', detailTechnicianContact: '',
  // Attach File / Upload Image are client-only previews (no server storage
  // yet) - just the picked file's name (and, for images, an object-URL
  // thumbnail), reset whenever you switch tabs or leave the ticket so a
  // stale preview never lingers on the wrong step.
  detailAttachedFileName: '', detailAttachedImageName: '', detailAttachedImagePreviewUrl: '',
  handoverFormFor: null, // ticket id currently showing its "request handover" form, or null
  handoverToUserId: '', handoverReason: '', handoverCandidates: [],
  handoverReasonOther: false, // true once "Other…" is picked in the reason dropdown, revealing a free-text input
  // Registration form
  regUsername: '', regPassword: '', regFirstName: '', regLastName: '', regTeam: '',
  regError: '', regSuccess: '',
  // Admin: pending-approval queue + activity log
  adminTab: 'pending', // 'pending' | 'log'
  pendingUsers: [],
  activityLog: [],
  // Phase 3 — Analyze (docs/BRIEF_Phase3_Analyze.md)
  pendingReview: [], // TT ที่วินิจฉัยแล้วรอคนตรวจ — จาก GET /api/tickets/pending-review
  analyzePage: 1,
  analyzeDetailId: null, // ticket id ที่เปิดอยู่ในหน้า Analyze-detail
  analyzeGroupDraft: '', // ค่าที่เลือกใน dropdown RESPONSIBLE TEAM (เริ่มจาก t.tag ที่ระบบเสนอ)
  analyzeGroupTouched: false, // true เมื่อ user เปลี่ยนจากค่าที่ระบบเสนอ -> ซ่อน chip "System Suggested"
  // Phase 6 — ช่อง "ประเภทงาน" (disposition) บนหน้า Analyze
  // แกนนี้ตอบคนละคำถามกับ RESPONSIBLE TEAM: ทีมไหนรับผิดชอบ (owner_group) กับ
  // เป็นงานชนิดไหนต้องทำอะไรต่อ (disposition) จึงต้องมีสองช่องแยกกัน ไม่ยุบรวม
  analyzeDispositionDraft: '',    // ค่าที่เลือกใน dropdown (เริ่มจากค่าที่ระบบเสนอ)
  analyzeDispositionTouched: false,
  analyzeAssessing: false,        // true ระหว่างรอผลจาก POST /assess-disposition
  analyzeDrafting: false,         // true ระหว่างรอผลจาก POST /draft-pw
  // ตัวเลขผลกระทบสำหรับชีท PW Info แถว 25-34 — ระบบเสนอมา คนแก้ได้ก่อนยืนยัน
  analyzeImpactDraft: null,       // null = ยังไม่แตะ ให้ใช้ค่าจาก ticket
  analyzeSavingImpact: false,
  analyzeManualDevice: '', analyzeManualCommand: '',
  analyzeManualResult: null, // { device, command, output, success } ของ Run Command ล่าสุด หรือ null
  analyzeManualRunning: false,
  analyzeDiagnosisRuns: [], // ผล 3 คำสั่งอัตโนมัติล่าสุดจาก GET /api/tickets/<id>/diagnosis ทีละคำสั่ง
  // ใช้ทำ collapsible section ของ COMMAND OUTPUT — คนละก้อนกับ analyzeManualResult (ที่นั่น
  // เป็นคำสั่งเดี่ยวที่คนสั่งเอง)
  // เก็บว่า <details> ของคำสั่งไหนถูกกาง/พับไว้ (key = ชื่อคำสั่ง) เพราะ polling ทุก 5 วิ
  // เรียก render() ใหม่ทั้งก้อน (full innerHTML replace) — ถ้าไม่จำค่าไว้ที่นี่ <details>
  // จะรีเซ็ตกลับไปที่ default (ข้อแรกกาง ที่เหลือพับ) ทุกรอบ ทำให้ผู้ใช้เห็นเป็นเปิด/ปิดเอง
  analyzeOpenSections: {},
};

function setState(patch) {
  Object.assign(state, patch);
  render();
}

// Every click/submit/change handler that triggers a network request routes
// through here instead of calling the handler directly - one place to show
// the loading bar and, via the `if (state.busy) return` guard, stop a
// double-click from firing the same request twice.
async function runBusy(fn) {
  if (state.busy) return;
  setState({ busy: true });
  try {
    await fn();
  } finally {
    setState({ busy: false });
  }
}

function askConfirm(message, onConfirm, confirmLabel) {
  setState({ confirmDialog: { message, onConfirm, confirmLabel } });
}

// Object URLs (used for the Upload Image thumbnail preview) leak memory if
// never revoked - call this before clearing/replacing one.
function resetAttachmentState() {
  if (state.detailAttachedImagePreviewUrl) {
    try { URL.revokeObjectURL(state.detailAttachedImagePreviewUrl); } catch (e) { /* ignore */ }
  }
  return { detailAttachedFileName: '', detailAttachedImageName: '', detailAttachedImagePreviewUrl: '' };
}

/* ============================================================
   Theme (light/dark) - stored explicitly once the user picks one;
   until then it follows the OS via prefers-color-scheme (style.css
   handles that half with no JS involved).
   ============================================================ */

function getStoredTheme() {
  try { return localStorage.getItem('csmc-theme'); } catch (e) { return null; }
}

function effectiveTheme() {
  return getStoredTheme() || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
}

function toggleTheme() {
  const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('csmc-theme', next); } catch (e) { /* private browsing etc. - theme just won't persist */ }
  applyTheme(next);
  render();
}

/* ============================================================
   Desktop notifications (OS-level toast) for overdue tickets — opt-in.
   Separate from the always-on in-page badge/banner: this fires a native
   browser Notification so a ticket crossing 5 min unacknowledged gets
   noticed even if the user isn't looking at the CSMC tab right now.
   ============================================================ */

function getNotifyPref() {
  try { return localStorage.getItem('csmc-notify') === '1'; } catch (e) { return false; }
}

function setNotifyPref(on) {
  try { localStorage.setItem('csmc-notify', on ? '1' : '0'); } catch (e) { /* private browsing etc. */ }
}

function toggleDesktopNotifications() {
  if (getNotifyPref()) {
    setNotifyPref(false);
    render();
    return;
  }
  if (!('Notification' in window)) {
    setState({ errorMessage: 'เบราว์เซอร์นี้ไม่รองรับ desktop notification' });
    return;
  }
  if (Notification.permission === 'denied') {
    setState({ errorMessage: 'การแจ้งเตือนถูกบล็อกไว้ในเบราว์เซอร์ — เปิดใน browser settings ก่อน' });
    return;
  }
  Notification.requestPermission().then(function (perm) {
    if (perm === 'granted') {
      setNotifyPref(true);
      render();
    }
  });
}

// เรียกทุกครั้งหลังโหลดข้อมูล ticket ใหม่ (ทั้ง login, polling, refresh) — เทียบ id
// ของ ticket ที่ overdue ตอนนี้กับครั้งก่อน แล้วยิง notification เฉพาะใบที่ "ใหม่"
// (เพิ่งข้ามเกณฑ์ 5 นาทีไปหลังจากเช็คครั้งก่อน) กันไม่ให้ยิงซ้ำทุก poll สำหรับ ticket
// เดิมที่ยัง overdue ต่อเนื่องอยู่
function checkForNewOverdue(uploadedGroups) {
  const currentIds = new Set();
  GROUP_ORDER.forEach(function (tag) {
    (uploadedGroups[tag] || []).forEach(function (t) {
      if (t.overdueTier != null) currentIds.add(t.id);
    });
  });

  if (knownOverdueIds === null) {
    knownOverdueIds = currentIds;
    return;
  }

  const newIds = [];
  currentIds.forEach(function (id) { if (!knownOverdueIds.has(id)) newIds.push(id); });
  knownOverdueIds = currentIds;

  if (!newIds.length || !getNotifyPref() || !('Notification' in window) || Notification.permission !== 'granted') return;

  const title = newIds.length === 1 ? 'Ticket ค้างเกิน 5 นาที' : newIds.length + ' tickets ค้างเกิน 5 นาที';
  const body = newIds.length === 1
    ? newIds[0] + ' ยังไม่ถูก acknowledge'
    : newIds.slice(0, 3).join(', ') + (newIds.length > 3 ? ' และอื่นๆ' : '') + ' ยังไม่ถูก acknowledge';
  try { new Notification(title, { body: body }); } catch (e) { /* e.g. blocked mid-session - ignore */ }
}

function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// Collapse/expand the sidebar through the View Transitions API so the panel
// slides and the content reflows smoothly instead of snapping. The whole app
// re-renders via innerHTML on every state change, so there's no persistent
// element to CSS-transition — startViewTransition snapshots before/after and
// tweens the named regions (see .sidebar / .main in style.css) for us.
// Falls back to an instant swap where the API is missing or motion is reduced.
function toggleSidebar() {
  const apply = () => setState({ sidebarOpen: !state.sidebarOpen });
  if (document.startViewTransition && !prefersReducedMotion()) {
    document.startViewTransition(apply);
  } else {
    apply();
  }
}

applyTheme(effectiveTheme());

/* ============================================================
   Static lookups
   ============================================================ */

const GROUP_ORDER = ['Network-Core', 'Network-Access', 'Transmission', 'NOC-Monitoring'];
const PAGE_SIZE = 50;

// Phase 3 — ต้องตรงกับ lib/mapping.py DEVICE_SSH / MANUAL_COMMANDS เป๊ะ (ฝั่ง server เป็นคน
// validate จริงอีกชั้นอยู่แล้วที่ /api/tickets/<tid>/run-command — รายการนี้ฝั่ง frontend มีไว้
// สร้าง dropdown เฉยๆ ไม่ใช่ที่มาของความปลอดภัย)
const ANALYZE_DEVICES = ['Core', 'Access-Site-A', 'Access-Site-B', 'Access-Site-C'];
// Phase 5 ข้อ C-1 — ต้องตรงกับ lib/mapping.py NODE_INTERFACES เป๊ะ (server validate ซ้ำ
// อีกชั้นที่ /api/tickets/upload) · ไม่มี Ethernet0/3 โดยเจตนา = ทางเข้า lab ห้ามแตะ
const NODE_INTERFACES = {
  'Core': ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2'],
  'Access-Site-A': ['Ethernet0/0'],
  'Access-Site-B': ['Ethernet0/1'],
  'Access-Site-C': ['Ethernet0/2'],
};
const ANALYZE_MANUAL_COMMANDS = [
  'show interface {interface}',
  'show ip interface brief',
  'show logging',
  'show ip route',
  'show cdp neighbors',
  'show version',
];

// 4 Owner Group จริงจาก lib/mapping.py (Phase 2) — ต้องตรงกันเป๊ะกับฝั่ง server
// เพราะ server เป็นคนจำแนก group มาให้แล้ว (ดู BRIEF_Phase4_Dashboard.md ข้อ 4.1)
// owner = short code สำหรับ badge วงกลม, name = ชื่อทีมสั้นๆ, desc = คำอธิบายเต็ม
const GROUP_DATA = {
  'Network-Core':   { tag: 'Network-Core',   owner: 'CORE',   name: 'Core Network Team',   desc: 'ดูแลอุปกรณ์แกนกลางและ routing ระดับ backbone' },
  'Network-Access': { tag: 'Network-Access', owner: 'ACCESS', name: 'Access Network Team', desc: 'ดูแลสายเชื่อม Core กับ Access Site' },
  'Transmission':   { tag: 'Transmission',   owner: 'TRANS',  name: 'Transmission Team',   desc: 'ดูแลชั้นกายภาพ สาย สัญญาณ' },
  'NOC-Monitoring':  { tag: 'NOC-Monitoring', owner: 'NOC',    name: 'NOC Monitoring Team', desc: 'เฝ้าระวังทั่วไป รับเคสที่ยังระบุเจ้าภาพไม่ได้' },
};

const PRIORITY_COLOR = { P1: '#e5484d', P2: '#f5a623', P3: '#e8c547', P4: '#8a8f98' };

function confidenceColor(pct) {
  if (pct == null) return '';
  if (pct >= 80) return '#3dd68c';
  if (pct >= 50) return '#e8c547';
  return '#e5484d';
}

const STATUS_LABELS = ['Assigned', 'In Progress', 'Resolved', 'Closed'];

// หน้าตาแต่ละ diagnose_state ในหน้า Analyze (docs/BRIEF_Phase3_Analyze.md ข้อ 5 ตาราง 4 สถานะ)
const DIAGNOSE_STATE_META = {
  running: { label: 'Checking...', tone: 'running' },
  done: { label: 'Checked', tone: 'done' },
  unreachable: { label: 'Unreachable', tone: 'unreachable' },
  undetermined: { label: 'Undetermined', tone: 'undetermined' },
};

/* ============================================================
   Table columns — reorderable (drag a header) and resizable (drag
   its right edge) for the triage and detail tables. Only the data
   columns are draggable; the leading checkbox and the trailing
   action column stay pinned in place, same on both tables.
   ============================================================ */

const DEFAULT_COLUMNS = {
  triage: [
    { key: 'id', label: 'Incident Number', width: 170 },
    { key: 'group', label: 'Assigned Group', width: 190 },
    { key: 'priority', label: 'Priority', width: 90 },
    { key: 'confidence', label: 'ความมั่นใจ', width: 100 },
    { key: 'node', label: 'อุปกรณ์', width: 150 },
    { key: 'start', label: 'Event_Start_Time', width: 150 },
    { key: 'desc', label: 'Description', width: 340 },
    { key: 'status', label: 'Status', width: 110 },
  ],
  detail: [
    { key: 'id', label: 'Incident Number', width: 160 },
    { key: 'group', label: 'Assigned Group', width: 180 },
    { key: 'priority', label: 'Priority', width: 90 },
    { key: 'confidence', label: 'ความมั่นใจ', width: 100 },
    { key: 'node', label: 'อุปกรณ์', width: 150 },
    { key: 'start', label: 'Event_Start_Time', width: 135 },
    { key: 'end', label: 'Event_End_Time', width: 135 },
    { key: 'desc', label: 'Description', width: 300 },
    { key: 'status', label: 'Status', width: 110 },
  ],
  inprogress: [
    { key: 'id', label: 'ID', width: 150 },
    { key: 'desc', label: 'Description', width: 320 },
    { key: 'start', label: 'Event Start Time', width: 150 },
    { key: 'owner', label: 'Owner', width: 110 },
    { key: 'handover', label: 'Handover', width: 170 },
    { key: 'step', label: 'Current step', width: 140 },
    { key: 'nextUpdate', label: 'Next Update', width: 120 },
  ],
  analyze: [
    { key: 'id', label: 'Incident Number', width: 160 },
    { key: 'node', label: 'Device', width: 150 },
    { key: 'priority', label: 'Priority', width: 90 },
    { key: 'desc', label: 'Description', width: 280 },
    { key: 'diagnoseState', label: 'Diagnosis', width: 150 },
    { key: 'cause', label: 'Cause', width: 260 },
    { key: 'start', label: 'Event Start Time', width: 150 },
  ],
};

function getColumnLayout(table) {
  return DEFAULT_COLUMNS[table];
}

function renderDiagnoseStateBadge(diagnoseState) {
  if (!diagnoseState) return `<span class="diagnose-badge diagnose-badge-pending">Pending</span>`;
  const meta = DIAGNOSE_STATE_META[diagnoseState] || { label: diagnoseState, tone: 'pending' };
  return `<span class="diagnose-badge diagnose-badge-${meta.tone}">${escapeHtml(meta.label)}</span>`;
}

// Renders one <td> for a data column, shared by the triage and detail
// tables so a column means the same thing (same cell markup) in both.
function ticketColumnCell(key, t) {
  switch (key) {
    case 'id': return `<td class="ticket-id ellipsis">${escapeHtml(t.id)}</td>`;
    case 'group': return `<td class="ellipsis">${escapeHtml(t.assignedGroup)}</td>`;
    case 'start': return `<td class="ellipsis">${escapeHtml(formatEventTime(t.startTime))}</td>`;
    case 'end': return `<td class="ellipsis">${escapeHtml(formatEventTime(t.endTime))}</td>`;
    case 'desc': return `<td class="ellipsis">${escapeHtml(t.description)}</td>`;
    case 'status': return `<td><span class="badge" data-status="${escapeHtml(t.status)}">${escapeHtml(t.status)}</span></td>`;
    case 'priority': return `<td>${t.priority ? `<span class="priority-chip" style="color:${PRIORITY_COLOR[t.priority] || ''};border-color:${PRIORITY_COLOR[t.priority] || ''}">${escapeHtml(t.priority)}</span>` : ''}</td>`;
    case 'confidence': return `<td>${t.confidence != null ? `<span style="color:${confidenceColor(t.confidence)};font-weight:600">${t.confidence}%</span>` : ''}</td>`;
    case 'node': return `<td class="ellipsis">${escapeHtml(t.node || '')}${t.interface ? ` <span style="color:var(--text-muted)">(${escapeHtml(t.interface)})</span>` : ''}</td>`;
    // In Progress-only columns - defined here too (rather than a second
    // per-table cell function) so 'id'/'start'/'desc' above stay the one
    // shared definition across every table that uses them.
    case 'owner': return `<td>${escapeHtml(t.acknowledgedBy || '')}</td>`;
    case 'handover': return `<td>${isIncomingHandover(t) ? renderIncomingHandoverCell(t)
      : (t.pendingHandoverTo ? `<span class="handover-pending">Awaiting ${escapeHtml(t.pendingHandoverTo)}</span>` : '')}</td>`;
    case 'step': return `<td>${escapeHtml((SUB_STATUS_STEPS.find(s => s.key === t.currentSubStatus) || SUB_STATUS_STEPS[0]).label)}</td>`;
    case 'nextUpdate': return `<td>${renderSlaBadge(t)}</td>`;
    // Analyze-only columns (docs/BRIEF_Phase3_Analyze.md)
    case 'diagnoseState': return `<td>${renderDiagnoseStateBadge(t.diagnoseState)}</td>`;
    case 'cause': return `<td class="ellipsis">${escapeHtml(t.causeSummary || (t.diagnoseState ? '' : 'Pending diagnosis...'))}</td>`;
    default: return '<td></td>';
  }
}

function renderColumnHeader(table, col) {
  return `<th class="col-header"><span class="col-header-label">${escapeHtml(col.label)}</span></th>`;
}

function renderColGroup(leadingWidths, columns, trailingWidths) {
  return `<colgroup>
    ${leadingWidths.map(w => `<col style="width:${w}px">`).join('')}
    ${columns.map(c => `<col style="width:${c.width}px" data-col="${c.key}">`).join('')}
    ${trailingWidths.map(w => `<col style="width:${w}px">`).join('')}
  </colgroup>`;
}

/* ============================================================
   Ticket loading + stats — classification ทั้งหมดมาจาก server แล้ว
   (lib/classifier.py) ฝั่งนี้แค่ดึงมาแสดง ไม่มี logic จำแนกซ้ำ
   ============================================================ */

async function loadTickets() {
  const [snapshot, pendingReviewSnapshot] = await Promise.all([
    apiFetch('/api/tickets', { method: 'GET' }),
    apiFetch('/api/tickets/pending-review', { method: 'GET' }),
  ]);
  checkForNewOverdue(snapshot.uploadedGroups || {});

  // หา TT ที่เพิ่งโผล่มาใหม่ (เทียบกับรอบที่แล้ว) เพื่อ highlight ~10 วิ
  const prevIds = new Set(GROUP_ORDER.flatMap(tag => (state.uploadedGroups ? state.uploadedGroups[tag] || [] : []))
    .concat(state.uploadedUnassigned || []).map(t => t.id));
  const allNextTickets = GROUP_ORDER.flatMap(tag => snapshot.uploadedGroups[tag] || [])
    .concat(snapshot.uploadedUnassigned || []);
  if (state.uploadedGroups) { // ข้ามรอบแรกหลัง login กันไม่ให้ TT เก่าทั้งหมดถูกนับเป็น "ใหม่"
    const freshIds = allNextTickets.map(t => t.id).filter(id => !prevIds.has(id));
    freshIds.forEach(id => {
      newTicketIds.add(id);
      setTimeout(() => { newTicketIds.delete(id); render(); }, 10000);
    });
  }

  setState({
    uploadedGroups: snapshot.uploadedGroups,
    uploadedUnassigned: snapshot.uploadedUnassigned,
    uploadedUnmatched: (snapshot.uploadedUnassigned || []).length,
    uploadInfo: snapshot.uploadInfo,
    // Phase 3 — TT ที่วินิจฉัยแล้วรอคนตรวจในหน้า Analyze โหลดคู่กับ tickets เสมอ (Promise.all
    // ด้านบน) เพราะทุกที่ที่ต้อง refresh tickets ก็ต้องการ badge/list ของ Analyze สดด้วย
    pendingReview: pendingReviewSnapshot.tickets || [],
  });
}

/* ============================================================
   Polling loop — โหลดข้อมูล ticket ซ้ำทุก 5 วินาที (BRIEF ข้อ 4.5)
   เริ่มหลัง login สำเร็จ, เคลียร์เมื่อ logout, หยุดเมื่อ tab ไม่ active
   กันยิง request ทิ้งเปล่าเวลาผู้ใช้สลับไปแท็บอื่น
   ============================================================ */

function startPolling() {
  stopPolling(); // เคลียร์ interval เก่าก่อนเสมอ ป้องกัน double-poll
  pollingInterval = setInterval(function () {
    if (document.visibilityState !== 'visible') return;
    // ข้ามรอบนี้ถ้ามี modal/confirm dialog เปิดอยู่
    if (state.confirmDialog) return;
    // ข้ามรอบนี้ถ้า user กำลังพิมพ์ใน input/textarea/select
    const ae = document.activeElement;
    if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.tagName === 'SELECT')) return;
    // fetch เงียบๆ — fail แบบ silent ไม่เซ็ต errorMessage (ต่างจาก action ที่ user สั่งเอง)
    loadTickets().catch(function () {});
    // อยู่หน้า Analyze-detail อยู่ -> รีเฟรชผล 3 คำสั่งอัตโนมัติด้วย เผื่อ diagnose_worker.py
    // เพิ่งทำ Re-check เสร็จระหว่างที่เปิดหน้านี้ค้างไว้
    if (state.view === 'analyze-detail' && state.analyzeDetailId) {
      loadAnalyzeDiagnosis(state.analyzeDetailId).catch(function () {});
    }
  }, 5000);
}

function stopPolling() {
  if (pollingInterval) { clearInterval(pollingInterval); pollingInterval = null; }
}

/* ============================================================
   "แจ้งเหตุด้วยตนเอง" — manual entry (BRIEF ข้อ 7), แทนที่ import Excel/CSV เดิม
   Owner Group/Priority มาจากการเลือกของ agent เอง (human judgement ตรงๆ)
   ไม่ใช่ auto-classify ฝั่ง frontend — server แค่บันทึกสิ่งที่ agent เลือก
   ============================================================ */

function openManualEntry() {
  setState({
    manualEntryOpen: true, manualNode: '', manualInterface: '',
    manualDescription: '', manualGroup: '', manualPriority: '', manualError: '',
  });
}

function closeManualEntry() {
  setState({ manualEntryOpen: false });
}

async function submitManualEntry() {
  if (!state.manualNode.trim() || !state.manualDescription.trim() || !state.manualGroup || !state.manualPriority) {
    setState({ manualError: 'กรอกอุปกรณ์ / คำอธิบาย / เลือก Owner Group และ Priority ให้ครบ' });
    return;
  }
  try {
    await apiFetch('/api/tickets/upload', {
      method: 'POST',
      body: JSON.stringify({
        node: state.manualNode, interface: state.manualInterface,
        description: state.manualDescription, ownerGroup: state.manualGroup, priority: state.manualPriority,
      }),
    });
    await loadTickets();
    setState({ manualEntryOpen: false, errorMessage: '' });
  } catch (err) {
    setState({ manualError: err.message });
  }
}

/* ============================================================
   Action handlers
   ============================================================ */

// For a triage ticket, the server itself decides whether this actually
// assigns it or just records a proposal (depends on whether the caller is
// an admin) - the frontend doesn't need to know which happened, it just
// refetches and renders whatever state.isAdmin + the ticket's proposedTag
// say to show.
async function assignFromTriage(ticketId, tag) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(ticketId) + '/assign', {
      method: 'PATCH',
      body: JSON.stringify({ tag }),
    });
    // Refetch rather than hand-merge local state — a successful assign
    // moves a ticket between the triage and group views, so a full
    // refresh is simpler and safer than patching state in place.
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function confirmProposal(id) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/confirm-assign', { method: 'PATCH' });
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function rejectProposal(id) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/reject-assign', { method: 'PATCH' });
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

function toggleSelectTicket(id) {
  const sel = Object.assign({}, state.selected);
  if (sel[id]) delete sel[id]; else sel[id] = true;
  setState({ selected: sel });
}

async function assignMultiple(ids, tag) {
  if (!ids || !ids.length) return;
  try {
    await Promise.all(ids.map(id => apiFetch(
      '/api/tickets/' + encodeURIComponent(id) + '/assign',
      { method: 'PATCH', body: JSON.stringify({ tag }) }
    )));
    const sel = Object.assign({}, state.selected);
    ids.forEach(id => { delete sel[id]; });
    await loadTickets();
    setState({ selected: sel, errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

// Fast path back to the Detail table's "Acknowledge" button, alongside the
// newer "Analyze" button - not every ticket needs a full SSH diagnosis/review
// before going to In Progress, and requiring one for every single ticket
// turned out to be too heavy-handed for routine cases (2026-08-06 feedback).
// Acknowledge here does NOT navigate anywhere (unlike the dblclick/Analyze
// path) - it just marks the row done and stays on the same table.
async function acknowledgeTickets(ids) {
  if (!ids || !ids.length) return;
  try {
    // acknowledgedBy is derived server-side from the authenticated user, not
    // sent from here - closes a spoofing gap a client-supplied value would open.
    await Promise.all(ids.map(id => apiFetch(
      '/api/tickets/' + encodeURIComponent(id) + '/acknowledge',
      { method: 'PATCH', body: JSON.stringify({}) }
    )));
    const sel = Object.assign({}, state.selected);
    ids.forEach(id => { delete sel[id]; });
    await loadTickets();
    setState({ selected: sel, errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

function acknowledgeFromRow(id) {
  const ids = (state.selected[id] && Object.keys(state.selected).length) ? Object.keys(state.selected) : [id];
  askConfirm(
    ids.length > 1 ? 'Acknowledge ' + ids.length + ' selected tickets?' : 'Acknowledge ticket ' + ids[0] + '?',
    () => acknowledgeTickets(ids),
    'Acknowledge'
  );
}

// Saves notes/ETA/technician info for whichever step is currently being
// viewed WITHOUT touching the ticket's real current_sub_status (advance:
// false) - safe to call from any step's tab, including one already passed
// or not yet reached, since it can never move the ticket's actual progress.
async function saveInProgressStepDetails(id) {
  const step = state.viewingStep;
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/sub-status', {
      method: 'PATCH',
      body: JSON.stringify({
        sub_status: step,
        advance: false,
        note: state.detailNoteDraft || null,
        // The datetime-local input's value is local-time "YYYY-MM-DDTHH:mm" -
        // new Date() parses that as local time correctly, then toISOString()
        // converts it to the UTC ISO string the backend stores.
        event_time: state.detailEventTimeDraft ? new Date(state.detailEventTimeDraft).toISOString() : null,
        technician_name: state.detailTechnicianName || null,
        technician_contact: state.detailTechnicianContact || null,
      }),
    });
    await loadTickets();
    setState({
      errorMessage: '', inProgressDetailEditing: false,
      detailNoteDraft: '', detailEventTimeDraft: '',
    });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

// Global (not tied to whichever tab is being viewed) - Confirm always moves
// the ticket's real current_sub_status one step forward from wherever it
// actually is; Back always moves it one step backward. Both live on every
// tab's Description card since they act on the ticket, not the tab.
//
// Carries forward whatever note/event time already exists for the target
// step (if a Save was done earlier via Edit) so this transition's new
// timeline entry doesn't blank out a note the user already typed - it just
// timestamps "the ticket officially reached this stage now" on top of it.
async function setTicketStage(id, stepKey, options) {
  const t = inProgressTickets().find(x => x.id === id);
  const existing = t ? latestTimelineEntryFor(t.subStatusTimeline, stepKey) : null;
  // resetCustomerUpdate: true only from the "Confirm" (forward) button below,
  // never from "Back" (revert-inprogress-stage) - moving a stage backward
  // isn't a customer contact, so it shouldn't reset the 1-hour SLA clock.
  const resetCustomerUpdate = !!(options && options.resetCustomerUpdate);
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/sub-status', {
      method: 'PATCH',
      body: JSON.stringify({
        sub_status: stepKey,
        advance: true,
        note: (existing && existing.note) || null,
        event_time: (existing && existing.eventTime) || null,
        reset_customer_update: resetCustomerUpdate,
      }),
    });
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function logCustomerUpdate(id) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/log-update', { method: 'PATCH' });
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

/* ============================================================
   Phase 3 — Analyze actions (docs/BRIEF_Phase3_Analyze.md ข้อ 4)
   ============================================================ */

// ปุ่ม Re-check — server แค่ตั้ง diagnose_state='running' แล้วคืนทันที ผลจริงมาจาก
// diagnose_worker.py ที่ poll เจอเอง รอบ poll ปกติของ loadTickets() (ทุก 5 วิ) จะดึงผลมาเอง
async function recheckDiagnosis(id) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/diagnose', { method: 'POST' });
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function runManualCommand(id) {
  setState({ analyzeManualRunning: true });
  try {
    const result = await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/run-command', {
      method: 'POST',
      body: JSON.stringify({ device: state.analyzeManualDevice, command: state.analyzeManualCommand }),
    });
    setState({ analyzeManualResult: result, analyzeManualRunning: false, errorMessage: '' });
  } catch (err) {
    setState({ analyzeManualRunning: false, errorMessage: err.message });
  }
}

// Phase 6 — ปุ่ม "ประเมินประเภทงาน" บนการ์ดประเภทงาน
//
// endpoint นี้ SSH เข้า Core จริงแล้วรอผล จึงใช้เวลาหลายวินาที — ตั้ง analyzeAssessing ไว้
// กันคนกดซ้ำระหว่างรอ (ถ้ากดซ้ำจะได้ผลสองชุดทับกันแล้วไม่รู้ว่าอันไหนของรอบไหน)
//
// หลังได้ผลมา **ไม่ตั้ง analyzeDispositionTouched** เพราะค่านี้มาจากระบบ ไม่ใช่คนเลือก
// chip "System Suggested" จึงยังต้องขึ้นอยู่ จนกว่าคนจะไปเปลี่ยนเอง
async function assessDisposition(id) {
  setState({ analyzeAssessing: true });
  try {
    const result = await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/assess-disposition', {
      method: 'POST',
    });
    await loadTickets();
    setState({
      analyzeAssessing: false, errorMessage: '',
      analyzeDispositionDraft: result.disposition === 'undetermined' ? '' : (result.disposition || ''),
      analyzeDispositionTouched: false,
    });
  } catch (err) {
    setState({ analyzeAssessing: false, errorMessage: err.message });
  }
}

// ยืนยันตัวเลขผลกระทบ — ฝั่ง server จะร่าง PW ใหม่ให้เองถ้าร่างไปแล้ว
// จึงต้อง loadTickets() หลังบันทึก เพื่อให้ชื่อไฟล์ร่างบนหน้าจอตรงกับไฟล์จริง
async function saveImpact(id) {
  const t = flatAllTickets().find(x => x.id === id);
  const values = state.analyzeImpactDraft || (t && (t.impactValues || t.impactSuggested)) || {};
  setState({ analyzeSavingImpact: true });
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/impact', {
      method: 'PATCH', body: JSON.stringify({ values }),
    });
    await loadTickets();
    setState({ analyzeSavingImpact: false, analyzeImpactDraft: null, errorMessage: '' });
  } catch (err) {
    setState({ analyzeSavingImpact: false, errorMessage: err.message });
  }
}

// สั่งให้ระบบร่าง PW ใหม่ — ใช้ได้แม้ TT ผ่าน Review ไปแล้ว
async function draftPw(id) {
  setState({ analyzeDrafting: true });
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/draft-pw', { method: 'POST' });
    await loadTickets();
    setState({ analyzeDrafting: false, errorMessage: '' });
  } catch (err) {
    setState({ analyzeDrafting: false, errorMessage: err.message });
  }
}

// ดาวน์โหลดร่าง PW — ต้องดึงผ่าน fetch พร้อม token เอง แล้วค่อยแปลงเป็นไฟล์
// ใช้ <a href> ตรงๆ ไม่ได้ เพราะเบราว์เซอร์จะไม่ใส่ Authorization header ไปด้วย
// แล้ว endpoint จะตอบ 401 (ระบบนี้ใช้ token ไม่ใช่ cookie)
async function downloadPwDraft(id) {
  try {
    const response = await fetch(API_BASE + '/api/tickets/' + encodeURIComponent(id) + '/pw-draft', {
      headers: authToken ? { 'Authorization': 'Bearer ' + authToken } : {},
    });
    if (!response.ok) {
      let message = 'ดาวน์โหลดร่าง PW ไม่สำเร็จ';
      try { message = (await response.json()).message || message; } catch (e) { /* ไม่ใช่ JSON */ }
      setState({ errorMessage: message });
      return;
    }
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = match ? match[1] : (id + '-PW-draft.xlsx');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

// ปุ่ม "ตรวจสอบแล้ว · ส่งเข้า In Progress" — ส่ง group เสมอ (ไม่ใช่แค่ตอน user แก้เอง) ให้
// mark_reviewed() ฝั่ง server เคลียร์/ตั้ง corrected_group ให้ตรงความจริงทุกครั้งที่ตรวจ
// เปลี่ยนหน้าไปหน้า In Progress-detail ของ TT ใบนี้โดยตรงหลัง submit สำเร็จ (ไม่ใช่หน้า
// In Progress list รวม — ตามคำขอ 2026-08-07 รอบสอง) ใช้ shape เดียวกับ openInProgressDetail()
// ด้านบนเพื่อไม่ให้ query ตรงนี้ค้างจาก ticket ใบก่อนหน้า ตัวปุ่มเองมี askConfirm คั่นก่อนเรียก
// ฟังก์ชันนี้ (ดู root click delegation, action 'submit-review')
async function submitReview(id) {
  const t = flatAllTickets().find(x => x.id === id);
  const group = state.analyzeGroupDraft || (t && t.tag) || '';
  // Phase 6 — ยืนยันประเภทงานก่อน แล้วค่อยปิดการตรวจ
  //
  // ยิงสองคำขอแยกกันโดยตั้งใจ ไม่ยัดรวมเป็นคำขอเดียว เพราะสองแกนนี้บันทึกคนละที่และ
  // มีกติกาคนละชุด: /review เขียน owner_group ส่วน /disposition เขียนเฉพาะคอลัมน์
  // disposition_* ซึ่งฝั่ง server บังคับไว้ว่าแตะ status/owner_group ไม่ได้เลย
  // ถ้ารวมเป็น endpoint เดียว ข้อรับประกันนั้นจะหายไปทันที
  const disposition = state.analyzeDispositionDraft
    || (t && t.confirmedDisposition)
    || (t && t.disposition !== 'undetermined' ? t.disposition : '')
    || '';
  try {
    if (disposition) {
      await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/disposition', {
        method: 'PATCH', body: JSON.stringify({ disposition }),
      });
    }
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/review', {
      method: 'PATCH', body: JSON.stringify({ group }),
    });
    await loadTickets();
    setState({
      errorMessage: '', selected: {},
      view: 'inprogress-detail', inProgressDetailId: id,
      viewingStep: 'received', inProgressDetailEditing: false,
      ...resetAttachmentState(),
    });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function requestHandover(id, toUserId, reason) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/handover', {
      method: 'POST',
      body: JSON.stringify({ to_user_id: toUserId, reason: reason }),
    });
    await loadTickets();
    setState({ errorMessage: '', handoverFormFor: null });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function loadHandoverCandidates(id) {
  try {
    const candidates = await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/handover-candidates', { method: 'GET' });
    setState({ handoverCandidates: candidates });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function confirmHandover(id) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/handover/confirm', { method: 'PATCH' });
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function rejectHandover(id) {
  try {
    await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/handover/reject', { method: 'PATCH' });
    await loadTickets();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function handleLoginSubmit() {
  try {
    const data = await apiFetch('/api/login', {
      method: 'POST',
      body: JSON.stringify({ username: state.username, password: state.password }),
    });
    authToken = data.token;
    setState({
      view: 'dashboard', loginError: '',
      firstName: data.firstName, lastName: data.lastName,
      team: data.team, isAdmin: !!data.isAdmin,
    });
    await loadTickets();
    startPolling(); // เริ่ม polling หลัง login สำเร็จ
  } catch (err) {
    setState({ loginError: err.message });
  }
}

async function handleRegisterSubmit() {
  try {
    const data = await apiFetch('/api/register', {
      method: 'POST',
      body: JSON.stringify({
        username: state.regUsername,
        password: state.regPassword,
        firstName: state.regFirstName,
        lastName: state.regLastName,
        team: state.regTeam,
      }),
    });
    setState({
      view: 'login', regError: '',
      regUsername: '', regPassword: '', regFirstName: '', regLastName: '', regTeam: '',
      loginError: '', regSuccess: data.message,
    });
  } catch (err) {
    setState({ regError: err.message });
  }
}

// Admin: pending-approval queue. Loaded on entering the admin view and
// refreshed after every approve/reject so the list never shows a stale row.
async function loadPendingUsers() {
  const list = await apiFetch('/api/admin/users/pending', { method: 'GET' });
  setState({ pendingUsers: list });
}

async function approveUser(id) {
  try {
    await apiFetch('/api/admin/users/' + id + '/approve', { method: 'PATCH' });
    await loadPendingUsers();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function rejectUser(id) {
  try {
    await apiFetch('/api/admin/users/' + id + '/reject', { method: 'PATCH' });
    await loadPendingUsers();
    setState({ errorMessage: '' });
  } catch (err) {
    setState({ errorMessage: err.message });
  }
}

async function loadActivityLog() {
  const list = await apiFetch('/api/admin/activity-log', { method: 'GET' });
  setState({ activityLog: list });
}

/* ============================================================
   Derived data helpers
   ============================================================ */

function parseTicketTime(v) {
  const s = String(v || '').trim();
  if (!s) return -Infinity;
  // Try Date.parse on the raw string first (handles M/D/YYYY H:MM:SS AM/PM as in US-locale Excel exports)
  let d = Date.parse(s);
  if (!isNaN(d)) return d;
  // ISO / YYYY-MM-DD HH:MM with a space
  d = Date.parse(s.replace(' ', 'T'));
  if (!isNaN(d)) return d;
  // DD/MM/YYYY HH:MM(:SS), including Buddhist-era years
  const m = s.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
  if (m) {
    let [, dd, mm, yy, hh, mi, ss] = m;
    yy = String(yy);
    if (yy.length === 2) yy = '20' + yy;
    if (Number(yy) > 2400) yy = String(Number(yy) - 543);
    const iso = yy + '-' + String(mm).padStart(2, '0') + '-' + String(dd).padStart(2, '0') + 'T' + String(hh || '00').padStart(2, '0') + ':' + (mi || '00') + ':' + (ss || '00');
    d = Date.parse(iso);
    if (!isNaN(d)) return d;
  }
  return -Infinity;
}

// Raw start/end times come straight from whatever the uploaded source used -
// ISO, US M/D/YYYY, Buddhist-era DD/MM/YYYY, with or without seconds -
// parseTicketTime() above already tolerates all of them for sorting, but
// ticketColumnCell() used to print the raw string verbatim, so two different
// upload batches (or a hand-entered fixture) could show two different-looking
// formats side by side in the same column. Routing display through the same
// parser first, then one consistent locale format, fixes that regardless of
// which format the underlying value happens to be in.
function formatEventTime(raw) {
  const ms = parseTicketTime(raw);
  if (!isFinite(ms)) return '-';
  return new Date(ms).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function byNewest(a, b) { return parseTicketTime(b.startTime) - parseTicketTime(a.startTime); }

// Age-based row colour, tied to the team's SLA (4 hours) rather than a
// generic "old ticket" heuristic: orange once it's been open past half the
// SLA (2h), red once it's blown the SLA entirely (4h). Closed/resolved
// tickets are excluded — the clock stops mattering once someone's dealt
// with it. Not a stored field; recomputed on each render from start_time.
const SLA_WARNING_HOURS = 2;
const SLA_BREACH_HOURS = 4;
function ticketPriority(t) {
  if (t.status === 'Resolved' || t.status === 'Closed') return 'none';
  const started = parseTicketTime(t.startTime);
  if (!isFinite(started)) return 'none';
  const hours = (Date.now() - started) / 3600000;
  if (hours >= SLA_BREACH_HOURS) return 'critical';
  if (hours >= SLA_WARNING_HOURS) return 'warning';
  return 'none';
}

// นับจำนวน ticket overdue รวมทุกกลุ่ม — ใช้อัพเดต browser tab title
function countTotalOverdue() {
  const up = state.uploadedGroups;
  if (!up) return 0;
  return GROUP_ORDER.reduce(function (sum, tag) {
    return sum + (up[tag] || []).filter(function (t) { return t.overdueTier != null; }).length;
  }, 0);
}

function escapeHtml(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatTimestamp(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// The per-step "Event Time" field (2026-07-24: upgraded from free text to a
// native <input type="datetime-local">) - that input type needs its value in
// local "YYYY-MM-DDTHH:mm" form, NOT a UTC ISO string with a Z/offset suffix
// (Date's own toISOString() is UTC-only, which would show the wrong clock
// time in the picker), hence this dedicated pair of helpers.
const EVENT_TIME_STEP_MINUTES = 5;

function toDatetimeLocalValue(date) {
  const pad = n => String(n).padStart(2, '0');
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
    + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
}

function roundToStepMinutes(date, stepMinutes) {
  const ms = stepMinutes * 60000;
  return new Date(Math.round(date.getTime() / ms) * ms);
}

// The API returns timestamps as ISO 8601 with a UTC offset (e.g.
// "...T12:23:30+00:00"). new Date() parses that correctly, but the old code
// in this file just string-sliced the raw ISO text and displayed it as-is -
// that's the UTC clock time mislabeled as local, off by a fixed 7 hours in
// Thailand. Using the Date object's local getters here converts it properly.
function formatLocalDateTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const pad = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
    pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}

/* ============================================================
   Icons (static SVGs used across views)
   ============================================================ */

const ICONS = {
  sidebar: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"></rect><line x1="9" y1="4" x2="9" y2="20"></line></svg>',
  sidebarSmall: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"></rect><line x1="9" y1="4" x2="9" y2="20"></line></svg>',
  eyeOff: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 20C5 20 1 12 1 12a20.3 20.3 0 0 1 5.06-6.06M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a20.3 20.3 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24"></path><path d="M1 1l22 22"></path></svg>',
  eye: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z"></path><circle cx="12" cy="12" r="3"></circle></svg>',
  logout: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><path d="M16 17l5-5-5-5"></path><path d="M21 12H9"></path></svg>',
  refresh: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-2.64-6.36"></path><path d="M21 3v6h-6"></path></svg>',
  upload: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><path d="M17 8l-5-5-5 5"></path><path d="M12 3v12"></path></svg>',
  search: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"></circle><path d="m21 21-4.3-4.3"></path></svg>',
  sun: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path></svg>',
  moon: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"></path></svg>',
  bell: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>',
  bellOff: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13.73 21a2 2 0 0 1-3.46 0"></path><path d="M18.63 13A17.89 17.89 0 0 1 18 8"></path><path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14"></path><path d="M18 8a6 6 0 0 0-9.33-5"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>',
  grid: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"></rect><rect x="14" y="3" width="7" height="7" rx="1"></rect><rect x="3" y="14" width="7" height="7" rx="1"></rect><rect x="14" y="14" width="7" height="7" rx="1"></rect></svg>',
  inbox: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-6l-2 3h-4l-2-3H2"></path><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"></path></svg>',
  shield: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"></path></svg>',
  fileText: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>',
  userCheck: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="8.5" cy="7" r="4"></circle><polyline points="17 11 19 13 23 9"></polyline></svg>',
  truck: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="3" width="15" height="13"></rect><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon><circle cx="5.5" cy="18.5" r="2.5"></circle><circle cx="18.5" cy="18.5" r="2.5"></circle></svg>',
  mapPin: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>',
  tool: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>',
  checkCircle: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>',
  activity: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>',
};

// English labels/order for the customer-facing "Shopee-style" progress
// tracker - team decision (2026-07-20): English throughout so every
// customer understands it, not just Thai speakers.
//
// Revised 2026-07-23: only "received" is still "free" (derived purely from
// assigned_at - the ticket landing in the queue). "assigned" used to be
// automatic too (tied to acknowledged), but that was wrong - acknowledging
// a ticket just means an agent opened it to look at; they still need to
// diagnose the root cause before deciding a technician is even needed.
// "Technician Assigned" is now a real manual step, confirmed the same way
// en_route/on_site/working/completed are, once that diagnosis is done.
const SUB_STATUS_STEPS = [
  { key: 'received', label: 'Ticket Received', icon: 'fileText', auto: true },
  { key: 'assigned', label: 'Technician Assigned', icon: 'userCheck', auto: false },
  { key: 'en_route', label: 'En Route', icon: 'truck', auto: false },
  { key: 'on_site', label: 'On-Site', icon: 'mapPin', auto: false },
  { key: 'working', label: 'Resolving', icon: 'tool', auto: false },
  { key: 'completed', label: 'Resolved', icon: 'checkCircle', auto: false },
];
const MANUAL_SUB_STATUS_ORDER = ['assigned', 'en_route', 'on_site', 'working', 'completed'];

function subStatusStepDone(t, step) {
  // "Received" หมายถึงตอนที่ agent กด Acknowledge (คนรับงานแล้ว) — ไม่ใช่ assignedAt
  // (เวลาที่ group ถูก confirm ซึ่งมักจะว่างเปล่าตลอดสำหรับ TT ที่จำแนกอัตโนมัติ
  // แล้วไม่มีใครแก้ group เลย) ไม่งั้นวงกลม "Ticket Received" จะไม่ขึ้นสีน้ำเงิน
  // (done) แม้ acknowledge ไปแล้วก็ตาม — ให้ตรงกับที่ renderServiceTimelineSummary
  // ใช้ t.acknowledgedAt อยู่แล้ว
  if (step.key === 'received') return !!t.acknowledgedAt;
  const currentIndex = MANUAL_SUB_STATUS_ORDER.indexOf(t.currentSubStatus);
  const stepIndex = MANUAL_SUB_STATUS_ORDER.indexOf(step.key);
  return currentIndex >= 0 && stepIndex <= currentIndex;
}

// The ticket's real stage only ever lives in MANUAL_SUB_STATUS_ORDER - null
// means "not started yet" (still just Received, root cause not diagnosed).
// null returned here means "nothing to move to" (already at the very
// start/end).
function nextManualStage(t) {
  const currentIndex = MANUAL_SUB_STATUS_ORDER.indexOf(t.currentSubStatus);
  if (currentIndex === -1) return MANUAL_SUB_STATUS_ORDER[0];
  return currentIndex < MANUAL_SUB_STATUS_ORDER.length - 1 ? MANUAL_SUB_STATUS_ORDER[currentIndex + 1] : null;
}
// Going back from "assigned" (the very first manual stage) returns
// 'received' rather than null - that's the sentinel the backend recognizes
// as "reset current_sub_status to null" (see updateSubStatus). Going back
// from "not started at all" (currentIndex -1) has nowhere to go, so null.
function prevManualStage(t) {
  const currentIndex = MANUAL_SUB_STATUS_ORDER.indexOf(t.currentSubStatus);
  if (currentIndex === -1) return null;
  return currentIndex > 0 ? MANUAL_SUB_STATUS_ORDER[currentIndex - 1] : 'received';
}

// subStatusTimeline is ordered oldest-first (backend orderBy('created_at')).
// Nothing prevents the same subStatus from being logged more than once for a
// ticket, so a naive .find() would surface the OLDEST entry for a step
// instead of the current one. Always take the last (most recent) match.
function latestTimelineEntryFor(timeline, stepKey) {
  const matches = (timeline || []).filter(ev => ev.subStatus === stepKey);
  return matches.length ? matches[matches.length - 1] : undefined;
}

// ชื่อ/เบอร์ช่างเป็นคุณสมบัติของ "ใครกำลังทำเคสนี้" ไม่ใช่ของ step ใด step หนึ่ง —
// เคยกรอกไว้ตอน "Technician Assigned" แล้วต้องยังติดไปกับ step ถัดๆ ไป (En Route,
// On-Site, ...) โดยไม่ต้องพิมพ์ซ้ำ ไล่หา entry ล่าสุดที่มีชื่อช่างย้อนจากท้ายสุด
// (timeline เรียงเก่า->ใหม่) ไม่จำกัดแค่ step ปัจจุบัน
function latestTechnicianInfo(timeline) {
  const list = timeline || [];
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].technicianName) return { name: list[i].technicianName, contact: list[i].technicianContact };
  }
  return { name: null, contact: null };
}

function inProgressTickets() {
  // Deliberately NOT detailTickets() - that helper is scoped to whichever
  // single team is currently selected in state.detailTag. In Progress is a
  // cross-team view (that's the whole point of separating it from the
  // per-team table), so it scans every team's uploaded list itself.
  //
  // The API response groups tickets by team but doesn't stamp `tag` onto
  // each ticket object itself - stamp it here (only if missing) so anything
  // downstream of this flattened list (e.g. the Service Timeline Summary's
  // team badge) still knows which of the 5 teams a ticket belongs to.
  const up = state.uploadedGroups;
  if (!up) return [];
  const all = GROUP_ORDER.flatMap(tag => (up[tag] || []).map(t => t.tag ? t : Object.assign({ tag }, t)));
  const acknowledgedInProgress = all.filter(t => t.status === 'In Progress' && t.acknowledged);
  // 2026-07-23: it used to be automatic (tied to `acknowledged`), but the
  // user was explicit: nobody wants to see teammates' In Progress tickets,
  // or have teammates see theirs - each person's own workload only. Admin
  // is the one exception, unchanged, since they need the full picture.
  // A ticket mid-handover TO this user isn't theirs yet (still owned by the
  // sender until Accept), so it's kept visible by isIncomingHandover() -
  // otherwise it'd vanish from view the moment a colleague sends it over.
  const visible = state.isAdmin
    ? acknowledgedInProgress
    : acknowledgedInProgress.filter(t => t.acknowledgedBy === state.username || isIncomingHandover(t));
  return visible.sort(byNewest);
}

// เหมือน inProgressTickets() แต่ไม่กรอง status === 'In Progress' — ใช้เปิดหน้ารายละเอียด
// (stepper + ประวัติ) ของ ticket ที่ทำเสร็จ/ปิดไปแล้วได้ ไม่ให้ประวัติหายไปหลัง Resolved/Closed
function flatAllTickets() {
  const up = state.uploadedGroups;
  if (!up) return [];
  return GROUP_ORDER.flatMap(tag => (up[tag] || []).map(t => t.tag ? t : Object.assign({ tag }, t)))
    .concat(state.uploadedUnassigned || []);
}

function findTicketForDetail(id) {
  const t = flatAllTickets().find(x => x.id === id);
  if (!t || !t.acknowledged) return null;
  if (!state.isAdmin && t.acknowledgedBy !== state.username && !isIncomingHandover(t)) return null;
  return t;
}

// เปิดหน้า Analyze-detail ของ ticket ใบหนึ่ง (Phase 3) — เรียกจากปุ่ม/ดับเบิลคลิก/คลิกขวา
// บนแถวในตาราง Detail ต่อทีม ต่างจาก findTicketForDetail() ตรงที่ไม่ต้องรอ acknowledged
// (Analyze เกิด "ก่อน" acknowledge เสมอในลำดับงานใหม่ — Triage -> Analyze -> In Progress)
// รีเซ็ต draft ต่างๆ ของหน้า Analyze ทุกครั้งที่เปิด กันค่าเก่าจาก ticket ใบก่อนหน้าค้าง
function openAnalyzeDetail(id) {
  const t = flatAllTickets().find(x => x.id === id);
  setState({
    view: 'analyze-detail', analyzeDetailId: id,
    analyzeGroupDraft: (t && t.tag) || '', analyzeGroupTouched: false,
    analyzeDispositionDraft: (t && (t.confirmedDisposition || t.disposition)) || '',
    analyzeDispositionTouched: false, analyzeAssessing: false, analyzeDrafting: false,
    analyzeImpactDraft: null, analyzeSavingImpact: false,
    analyzeManualDevice: '', analyzeManualCommand: '', analyzeManualResult: null, analyzeManualRunning: false,
    analyzeDiagnosisRuns: [], analyzeOpenSections: {},
  });
  loadAnalyzeDiagnosis(id).catch(() => {});
}

// เปิดหน้า In Progress-detail ของ ticket ใบหนึ่ง — เดิมเรียกได้จาก dblclick แถวในตาราง In
// Progress เท่านั้น (ดู root dblclick delegation) ดึงมาเป็นฟังก์ชันแยกเพื่อให้ breadcrumb
// segment "In Progress" (breadcrumbInProgressSegment ด้านล่าง) เรียกใช้ตัวเดียวกันได้
// ไม่ต้องก็อปโค้ด setState ซ้ำ
function openInProgressDetail(id) {
  setState({
    view: 'inprogress-detail', inProgressDetailId: id,
    viewingStep: 'received', inProgressDetailEditing: false,
    ...resetAttachmentState(),
  });
}

// "Analyze" / "In Progress" เป็น breadcrumb segment ธรรมดาต่อจากกัน (2026-08-07 แก้จาก
// pill component แยกก้อนที่ทำรอบก่อน — user ชี้ว่าอยากให้มันเป็นส่วนหนึ่งของแถว breadcrumb
// เดิมเลย ไม่ใช่กล่องลอยแยกต่างหาก) ใช้ในทั้งหน้า Analyze และหน้า In Progress ของ ticket
// เดียวกัน ตัว segment ของหน้าที่ "ไม่ใช่" หน้าปัจจุบันจะเป็นลิงก์กดข้ามไปได้เสมอ
// "In Progress" ไม่ render เลยถ้ายังไม่ผ่าน Confirm จริง (!t.reviewedBy) — เพราะยังไม่มีหน้า
// In Progress ของ ticket นี้ให้ไปดู (findTicketForDetail ต้องการ t.acknowledged ซึ่ง
// mark_reviewed() ฝั่ง server เซ็ตพร้อมกับ reviewed_by เสมอ) เดิมโชว์เป็น plain text ค้างไว้
// ตลอดแม้ยังไม่ confirm ทำให้ดูเหมือน TT อยู่ใน In Progress ไปแล้วทั้งที่ยังอยู่หน้า Analyze
// (2026-08-07 แก้ตามที่ user ชี้ในภาพหน้า Analyze ของ INC-20260807-0009)
function breadcrumbAnalyzeSegment(t) {
  return `<span class="breadcrumb-link" data-action="open-analyze" data-id="${escapeHtml(t.id)}">Analyze</span><span>/</span>`;
}
function breadcrumbInProgressSegment(t) {
  if (!t.reviewedBy) return '';
  return `<span class="breadcrumb-link" data-action="open-inprogress-from-stage" data-id="${escapeHtml(t.id)}">In Progress</span><span>/</span>`;
}

// ผล 3 คำสั่งอัตโนมัติล่าสุด (ทีละคำสั่ง) สำหรับ collapsible section ของ COMMAND OUTPUT —
// แยกจาก ticket.rawOutput (string ก้อนเดียว) เพราะต้องรู้ขอบเขตของแต่ละคำสั่งเพื่อพับ/กาง
// ทีละหัวข้อได้ ไม่ใช่พับทั้งก้อน
async function loadAnalyzeDiagnosis(id) {
  const data = await apiFetch('/api/tickets/' + encodeURIComponent(id) + '/diagnosis', { method: 'GET' });
  const runs = (data.runs || []).filter(r => !r.runBy); // เฉพาะที่ diagnose_worker.py รันเอง ไม่รวม Manual Investigation
  // เอาแค่ batch ล่าสุด (3 คำสั่ง) — ถ้า fallback ไป Core ด้วย runs ท้ายสุดจะเป็นของ Core
  // ซึ่งเป็นชุดที่ใช้สรุปสาเหตุจริง (ตรงกับ cause_output ฝั่ง lib/diagnostics.py)
  const latest = runs.slice(-3);
  if (state.analyzeDetailId === id) setState({ analyzeDiagnosisRuns: latest });
}

/* ============================================================
   Render: Login
   ============================================================ */

function renderLogin() {
  return `
  <div class="login-page">
    <form id="login-form" class="login-card">
      <div class="login-title">Please sign in</div>
      ${state.regSuccess ? `<div class="login-success">${escapeHtml(state.regSuccess)}</div>` : ''}
      ${state.loginError ? `<div class="login-error">${escapeHtml(state.loginError)}</div>` : ''}

      <div class="field" style="margin-top:22px">
        <label class="field-label">Username</label>
        <input id="login-username" class="text-input" name="username" type="text" placeholder="vdesxxxx" value="${escapeHtml(state.username)}" data-bind="username" data-clear-error="1">
      </div>

      <div class="field">
        <label class="field-label">Password</label>
        <div class="password-field">
          <input id="login-password" class="text-input" name="password" type="${state.isVisible ? 'text' : 'password'}" placeholder="Password" required value="${escapeHtml(state.password)}" data-bind="password" data-clear-error="1">
          <button type="button" class="visibility-toggle" data-action="toggle-visibility" aria-label="Toggle password visibility">
            ${state.isVisible ? ICONS.eyeOff : ICONS.eye}
          </button>
        </div>
      </div>

      <div class="login-actions">
        <button type="button" class="btn" data-action="login-submit">Cancel</button>
        <button type="submit" class="btn btn-primary">Login</button>
      </div>

      <div class="login-footer-link">
        <span data-action="show-register">Don't have an account? Register</span>
      </div>
    </form>
  </div>`;
}

function renderRegister() {
  const teamOptions = GROUP_ORDER.map(tag =>
    `<option value="${tag}" ${state.regTeam === tag ? 'selected' : ''}>${tag}</option>`
  ).join('');
  return `
  <div class="login-page">
    <form id="register-form" class="login-card">
      <div class="login-title">Create an account</div>
      <div class="login-subtitle">Your account needs admin approval before you can sign in.</div>
      ${state.regError ? `<div class="login-error">${escapeHtml(state.regError)}</div>` : ''}

      <div class="field" style="margin-top:22px">
        <label class="field-label">Username</label>
        <input class="text-input" type="text" value="${escapeHtml(state.regUsername)}" data-bind="regUsername" data-clear-error="reg">
      </div>
      <div class="field">
        <label class="field-label">Password</label>
        <input class="text-input" type="password" value="${escapeHtml(state.regPassword)}" data-bind="regPassword" data-clear-error="reg">
        <div class="field-hint">At least 8 characters, upper &amp; lower case, and a number.</div>
      </div>
      <div class="field">
        <label class="field-label">First name</label>
        <input class="text-input" type="text" value="${escapeHtml(state.regFirstName)}" data-bind="regFirstName" data-clear-error="reg">
      </div>
      <div class="field">
        <label class="field-label">Last name</label>
        <input class="text-input" type="text" value="${escapeHtml(state.regLastName)}" data-bind="regLastName" data-clear-error="reg">
      </div>
      <div class="field">
        <label class="field-label">Team</label>
        <div class="select-wrap" style="width:100%">
          <select class="raw-select" style="width:100%;margin-top:7px" data-bind="regTeam" data-clear-error="reg">
            <option value="">Select…</option>
            ${teamOptions}
          </select>
          <span class="select-arrow" style="top:26px">▾</span>
        </div>
      </div>

      <div class="login-actions">
        <button type="button" class="btn" data-action="show-login">Back to login</button>
        <button type="submit" class="btn btn-primary">Register</button>
      </div>
    </form>
  </div>`;
}

/* ============================================================
   Render: App shell pieces
   ============================================================ */

function renderSidebar() {
  if (!state.sidebarOpen) return '';
  const fullName = (state.firstName || state.lastName) ? (state.firstName + ' ' + state.lastName).trim() : (state.username || 'User');
  const avatarInitial = ((state.firstName || state.username || 'V').trim().charAt(0) || 'V').toUpperCase();
  const triageCount = state.uploadedGroups ? (state.uploadedUnmatched || 0) : 0;
  const onDashboard = state.view === 'dashboard';
  const onTriage = state.view === 'triage';
  const onAnalyze = state.view === 'analyze' || state.view === 'analyze-detail';
  const onInProgress = state.view === 'inprogress';
  const inProgressCount = state.uploadedGroups ? inProgressTickets().length : 0;
  const pendingReviewCount = (state.pendingReview || []).length;
  return `
    <div class="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-title">Incident Triage Console</div>
        <div class="sidebar-header-actions">
          <button class="icon-btn ${getNotifyPref() ? 'icon-btn-active' : ''}" data-action="toggle-notifications" aria-label="${getNotifyPref() ? 'Disable' : 'Enable'} desktop notifications" title="Desktop notification เมื่อ ticket ค้างเกิน 5 นาที">${getNotifyPref() ? ICONS.bell : ICONS.bellOff}</button>
          <button class="icon-btn" data-action="toggle-theme" aria-label="Toggle dark mode">${effectiveTheme() === 'dark' ? ICONS.sun : ICONS.moon}</button>
          <button class="icon-btn" data-action="toggle-sidebar" aria-label="Collapse sidebar">${ICONS.sidebar}</button>
        </div>
      </div>
      <div class="sidebar-nav">
        <div class="sidebar-nav-item ${onDashboard ? 'active' : ''}" data-action="back-to-dashboard">
          <span class="sidebar-nav-icon">${ICONS.grid}</span> Dashboard
        </div>
        <div class="sidebar-nav-item ${onTriage ? 'active' : ''}" data-action="open-triage">
          <span class="sidebar-nav-icon">${ICONS.inbox}</span> Triage${triageCount ? ` <span class="nav-badge">${triageCount}</span>` : ''}
        </div>
        <div class="sidebar-nav-item ${onAnalyze ? 'active' : ''}" data-action="open-analyze-list">
          <span class="sidebar-nav-icon">${ICONS.activity}</span><span>Analyze</span>${pendingReviewCount ? `<span class="nav-badge">${pendingReviewCount}</span>` : ''}
        </div>
        <div class="sidebar-nav-item ${onInProgress ? 'active' : ''}" data-action="open-inprogress">
          <span class="sidebar-nav-icon">${ICONS.tool}</span> In Progress${inProgressCount ? ` <span class="nav-badge">${inProgressCount}</span>` : ''}
        </div>
        ${state.isAdmin ? `
        <div class="sidebar-nav-item ${state.view === 'admin' ? 'active' : ''}" data-action="open-admin">
          <span class="sidebar-nav-icon">${ICONS.shield}</span> Admin${state.pendingUsers.length ? ` <span class="nav-badge">${state.pendingUsers.length}</span>` : ''}
        </div>` : ''}
      </div>
      <div class="sidebar-footer">
        <div class="sidebar-user">
          <div class="avatar">${escapeHtml(avatarInitial)}</div>
          <div style="min-width:0">
            <div class="user-name">${escapeHtml(fullName)}</div>
            <div class="user-role">${state.team ? `<span class="team-chip">${escapeHtml(state.team)}</span>` : (state.isAdmin ? `<span class="team-chip team-chip-admin">Admin</span>` : '')}</div>
          </div>
        </div>
        <div class="logout" data-action="logout">
          <span class="logout-icon">${ICONS.logout}</span>
          Log out
        </div>
      </div>
    </div>`;
}

function expandSidebarButton() {
  if (state.sidebarOpen) return '';
  return `<button class="icon-btn-outline" data-action="toggle-sidebar" aria-label="Expand sidebar">${ICONS.sidebarSmall}</button>`;
}

function renderErrorBanner() {
  if (!state.errorMessage) return '';
  return `<div class="api-error">${escapeHtml(state.errorMessage)} <button type="button" data-action="dismiss-error" aria-label="Dismiss">✕</button></div>`;
}

/* ============================================================
   Ticket list helpers — shared between rendering and CSV export so
   the two never drift (export always matches what's on screen).
   ============================================================ */

function triageTickets() {
  return (state.uploadedUnassigned || []).slice().sort(byNewest);
}

function detailTickets() {
  const up = state.uploadedGroups;
  const source = up ? (up[state.detailTag] || []) : [];
  const q = (state.detailSearch || '').trim().toLowerCase();
  return source
    .slice()
    .sort(byNewest)
    .filter(t => {
      // ถ้า user กด "Show only" จะกรองเฉพาะ ticket ที่ overdue
      if (state.detailOverdueFilter && t.overdueTier == null) return false;
      return !q || String(t.id).toLowerCase().indexOf(q) !== -1 || String(t.description).toLowerCase().indexOf(q) !== -1;
    });
}

/* ============================================================
   Render: Dashboard
   ============================================================ */

function computeGroups() {
  const up = state.uploadedGroups;
  return GROUP_ORDER.map(tag => {
    const g = GROUP_DATA[tag];
    const list = up ? (up[tag] || []) : [];
    const total = list.length;
    const statuses = STATUS_LABELS.map(label => ({ label, count: list.filter(t => t.status === label).length }));
    // นับ ticket ที่ assign แล้วยังไม่ acknowledge เกิน 5 นาที (overdueTier != null)
    const overdueCount = list.filter(t => t.overdueTier != null).length;
    return { tag, owner: g.owner, total, statuses, overdueCount };
  });
}

/* ============================================================
   Render: In Progress (fully separate from the per-team Triage/Detail
   tables above - ownership/handover/timeline controls never appear there,
   by explicit team decision after reviewing a mockup on 2026-07-20)
   ============================================================ */

// True when a colleague has proposed handing THIS ticket to the currently
// logged-in user specifically (not just any pending handover - those are
// visible to everyone since In Progress is a shared cross-team view).
function isIncomingHandover(t) {
  return !!t.pendingHandoverToUserId && t.pendingHandoverTo === state.username;
}

// Tickets waiting on the current user's Accept/Reject - previously the only
// signal for these was a barely-there row tint inside the full cross-team In
// Progress list, easy to miss entirely if a colleague sends one over. This
// backs both the Dashboard banner and the sidebar count so it's impossible
// to not notice.
function incomingHandoverTickets() {
  if (!state.uploadedGroups) return [];
  return inProgressTickets().filter(isIncomingHandover);
}

function renderIncomingHandoverCell(t) {
  return `
    <span class="handover-incoming">
      <span class="handover-incoming-label">${ICONS.userCheck} From ${escapeHtml(t.acknowledgedBy || '')}</span>
      <button type="button" class="ack-btn" data-action="confirm-handover" data-id="${escapeHtml(t.id)}">Accept</button>
      <button type="button" class="btn-outline" data-action="reject-handover" data-id="${escapeHtml(t.id)}">Reject</button>
    </span>`;
}

// 1-hour recurring customer-update SLA (dashboard-only, 2026-07-24) - a
// technician is expected to update the customer at least once an hour while
// a ticket is In Progress. No push notifications yet: that channel is the
// already-deferred Phase 2 of the unacknowledged-ticket-alert feature and
// needs its own separate go-ahead, so this stays a table badge + sort order.
//
// Clock baseline is the last logged update, or the technician's Acknowledge
// time if none has been logged yet - see the "Log Update" action below.
const CUSTOMER_UPDATE_INTERVAL_MS = 60 * 60 * 1000;
const CUSTOMER_UPDATE_WARNING_MS = 15 * 60 * 1000;

function nextReportDueMs(t) {
  const baseline = t.lastReportedAt || t.acknowledgedAt;
  return baseline ? new Date(baseline).getTime() + CUSTOMER_UPDATE_INTERVAL_MS : null;
}

// Tier is recomputed on every render, not a per-second countdown - this
// app's fastest clock is the existing 30s ticket poll (startPolling()),
// which already re-renders regardless of whether the fetched data changed.
// That's plenty of resolution for a 1-hour SLA and avoids a busy timer.
function customerUpdateSla(t) {
  const dueMs = nextReportDueMs(t);
  if (dueMs == null) return null;
  const remainingMs = dueMs - Date.now();
  return {
    remainingMs,
    tier: remainingMs <= 0 ? 'overdue' : (remainingMs <= CUSTOMER_UPDATE_WARNING_MS ? 'warning' : 'safe'),
  };
}

function renderSlaBadge(t) {
  const sla = customerUpdateSla(t);
  if (!sla) return '';
  const mins = Math.max(0, Math.round(Math.abs(sla.remainingMs) / 60000));
  const label = sla.tier === 'overdue' ? `Overdue by ${mins}m` : sla.tier === 'warning' ? `Due in ${mins}m!` : `In ${mins}m`;
  return `<span class="sla-badge sla-badge-${sla.tier}">${escapeHtml(label)}</span>`;
}

function renderInProgress() {
  // Sort (stably) so tickets awaiting the current user's Accept/Reject float
  // to the top first (that decision blocks all other progress on the
  // ticket); within the rest, most recent Event Start Time first (2026-07-24:
  // reverted from SLA-urgency-first per explicit user feedback - the SLA
  // badge/column still shows the customer-update tier per row, it just
  // doesn't drive row order anymore).
  const tickets = inProgressTickets()
    .slice()
    .sort((a, b) => {
      const handoverDiff = (isIncomingHandover(b) ? 1 : 0) - (isIncomingHandover(a) ? 1 : 0);
      if (handoverDiff !== 0) return handoverDiff;
      return parseTicketTime(b.startTime) - parseTicketTime(a.startTime);
    });
  const handoverCount = incomingHandoverTickets().length;
  const columns = getColumnLayout('inprogress');

  return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb"><span class="breadcrumb-current">In Progress</span></div>
      <div class="page-header-actions">
        ${handoverCount ? `
        <span class="btn-outline handover-btn" title="Highlighted at the top of the table below">
          ${ICONS.userCheck} Handover <span class="nav-badge">${handoverCount}</span>
        </span>` : ''}
        <button class="btn-outline" data-action="refresh">${ICONS.refresh} Refresh</button>
      </div>
    </div>
    <div class="table-card">
      <div class="table-scroll">
        <table class="table-resizable">
          ${renderColGroup([], columns, [])}
          <thead><tr>${columns.map(c => renderColumnHeader('inprogress', c)).join('')}</tr></thead>
          <tbody>
            ${tickets.map(t => `
              <tr class="inprogress-row${isIncomingHandover(t) ? ' inprogress-row-incoming' : ''}" data-dblclick-action="open-inprogress-detail" data-id="${escapeHtml(t.id)}">
                ${columns.map(c => ticketColumnCell(c.key, t)).join('')}
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      ${tickets.length === 0 ? `<div class="empty-state">No tickets are In Progress right now</div>` : ''}
    </div>`;
}

// The most common reasons a handover actually happens - picking one is a
// single click instead of typing (and re-typing, before the render() fix
// above, one character at a time). "Other…" still allows a free-text reason
// for anything not covered here.
const HANDOVER_REASON_PRESETS = ['Shift ended', 'On break', 'Restroom', 'Meal', 'Urgent personal matter'];

function renderHandoverForm(t) {
  const candidates = state.handoverCandidates || [];
  const options = candidates.map(c =>
    `<option value="${c.id}" ${String(state.handoverToUserId) === String(c.id) ? 'selected' : ''}>${escapeHtml(c.firstName + ' ' + c.lastName + ' (' + c.username + ')')}</option>`
  ).join('');

  const reasonSelectValue = state.handoverReasonOther ? '__other__' : state.handoverReason;
  const reasonOptionsHtml = HANDOVER_REASON_PRESETS.map(r =>
    `<option value="${escapeHtml(r)}" ${reasonSelectValue === r ? 'selected' : ''}>${escapeHtml(r)}</option>`
  ).join('');

  return `
    <span class="handover-form">
      <select class="raw-select" data-bind="handoverToUserId" style="width:200px" ${candidates.length === 0 ? 'disabled' : ''}>
        <option value="" disabled hidden>${candidates.length === 0 ? 'No eligible teammates' : 'Select a teammate…'}</option>
        ${options}
      </select>
      <select class="raw-select" data-action="pick-handover-reason" style="width:170px">
        <option value="" disabled hidden ${reasonSelectValue === '' ? 'selected' : ''}>Reason…</option>
        ${reasonOptionsHtml}
        <option value="__other__" ${reasonSelectValue === '__other__' ? 'selected' : ''}>Other…</option>
      </select>
      ${state.handoverReasonOther
        ? `<input type="text" placeholder="Type a reason" value="${escapeHtml(state.handoverReason)}" data-bind="handoverReason" style="width:220px">`
        : ''}
      <button type="button" class="ack-btn" data-action="submit-handover" data-id="${escapeHtml(t.id)}" ${state.handoverToUserId && state.handoverReason ? '' : 'disabled'}>Send request</button>
      <button type="button" class="btn-outline" data-action="cancel-handover-form">Cancel</button>
    </span>`;
}

function renderInProgressDetail() {
  const t = findTicketForDetail(state.inProgressDetailId);
  if (!t) {
    return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb">
        <span class="breadcrumb-link" data-action="back-to-inprogress-list">In Progress</span>
        <span>/</span>
        <span class="breadcrumb-current">Not found</span>
      </div>
    </div>
    <div class="empty-state">Ticket not found.</div>`;
  }

  const isActive = t.status === 'In Progress';
  const viewingStep = SUB_STATUS_STEPS.find(s => s.key === state.viewingStep) || SUB_STATUS_STEPS[0];

  return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb">
        ${t.tag ? `<span class="breadcrumb-link" data-action="open-group" data-tag="${escapeHtml(t.tag)}">${escapeHtml(t.tag)}</span><span>/</span>` : ''}
        ${breadcrumbAnalyzeSegment(t)}
        <span class="breadcrumb-link" data-action="back-to-inprogress-list">In Progress</span>
        <span>/</span>
        <span class="breadcrumb-current">${escapeHtml(t.id)}</span>
      </div>
      <span class="detail-owner-label" style="margin-left:auto">Owner: ${escapeHtml(t.acknowledgedBy || '')}</span>
      ${isActive ? renderSlaBadge(t) : ''}
      ${!isActive ? '' : isIncomingHandover(t)
        ? renderIncomingHandoverCell(t)
        : (t.pendingHandoverTo
          ? `<span class="handover-pending">Awaiting ${escapeHtml(t.pendingHandoverTo)}'s confirmation to accept this handover</span>`
          : (state.handoverFormFor === t.id
            ? renderHandoverForm(t)
            : `<button type="button" class="btn-outline" style="margin-left:0" data-action="open-handover-form" data-id="${escapeHtml(t.id)}">${ICONS.userCheck} Request Handover</button>`))}
    </div>
    ${renderStepper(t)}
    <div class="detail-cards">${renderStatusCard(t, viewingStep)}${renderDescriptionCard(t, viewingStep)}</div>
    ${viewingStep.key === 'completed' ? renderServiceTimelineSummary(t) : ''}`;
}

function renderStepper(t) {
  const stepsHtml = SUB_STATUS_STEPS.map(step => {
    const done = subStatusStepDone(t, step);
    const viewing = state.viewingStep === step.key;
    return `
      <button type="button" class="stepper-step" data-action="view-substatus-tab" data-step="${step.key}">
        <span class="stepper-circle${done ? ' done' : ''}${viewing ? ' viewing' : ''}">${ICONS[step.icon]}</span>
        <span class="stepper-label${viewing ? ' viewing' : ''}">${escapeHtml(step.label)}</span>
      </button>`;
  }).join('');

  return `
    <div class="stepper">
      <div class="stepper-track">
        <div class="stepper-line-base"></div>
        <div class="stepper-line-fill"></div>
        ${stepsHtml}
      </div>
    </div>`;
}

// The circles are NOT evenly spaced by index (space-between with
// content-sized labels - "Technician Assigned" is much wider than
// "En Route" - pushes them around), so a percentage formula assuming even
// spacing was drifting away from the real circle positions the more the
// label widths differed - the fill line would stop short of (or overshoot)
// the actual "done" circle. Measuring real rendered positions instead is
// the only way this is guaranteed accurate regardless of label widths.
function layoutSteppers() {
  document.querySelectorAll('.stepper-track').forEach(track => {
    const circles = Array.from(track.querySelectorAll('.stepper-circle'));
    if (circles.length < 2) return;
    const trackLeft = track.getBoundingClientRect().left;
    const centerOf = el => {
      const r = el.getBoundingClientRect();
      return r.left + r.width / 2 - trackLeft;
    };
    const firstCenter = centerOf(circles[0]);
    const lastCenter = centerOf(circles[circles.length - 1]);

    const base = track.querySelector('.stepper-line-base');
    if (base) {
      base.style.left = firstCenter + 'px';
      base.style.width = Math.max(0, lastCenter - firstCenter) + 'px';
    }

    const fill = track.querySelector('.stepper-line-fill');
    if (fill) {
      const doneCircles = circles.filter(c => c.classList.contains('done'));
      const fillEnd = doneCircles.length ? centerOf(doneCircles[doneCircles.length - 1]) : firstCenter;
      fill.style.left = firstCenter + 'px';
      fill.style.width = Math.max(0, fillEnd - firstCenter) + 'px';
    }
  });
}

const STEP_DESCRIPTIONS = {
  received: 'The service request has been logged.',
  assigned: 'Technician has been assigned to this ticket.',
  en_route: 'Technician is traveling to your location.',
  on_site: 'Technician has arrived on-site.',
  working: 'Technician is currently performing repairs.',
  completed: 'The issue has been resolved.',
};

const STEP_TIME_LABELS = {
  received: 'Logged Time',
  assigned: 'Assigned Time',
  en_route: 'Estimated Arrival',
  on_site: 'Arrival Time',
  working: 'Start Time',
  completed: 'Completion Time',
};

function renderStatusCard(t, step) {
  const timelineEntry = latestTimelineEntryFor(t.subStatusTimeline, step.key);
  const isAutoStep = step.key === 'received';

  // eventTime = เวลาที่ agent กรอกเองผ่านฟอร์ม Edit (ถ้ามี) — ถ้าไม่มีให้ใช้ createdAt
  // (เวลาที่ server บันทึกตอนกด Confirm/Save จริงๆ) แทน ไม่งั้นจะค้าง "Pending..."
  // ตลอดแม้กด Confirm ไปแล้ว เพราะ eventTime ไม่ได้ถูกกรอกทุกครั้ง
  let timeValue;
  if (step.key === 'received') {
    // acknowledgedAt (เวลากด Acknowledge) ไม่ใช่ assignedAt (เวลา confirm group ซึ่งมัก
    // ว่างเปล่าสำหรับ TT ที่จำแนกอัตโนมัติ) — ดู subStatusStepDone() ด้านบน
    timeValue = t.acknowledgedAt ? escapeHtml(formatTimestamp(t.acknowledgedAt)) : 'Pending...';
  } else {
    const at = timelineEntry && (timelineEntry.eventTime || timelineEntry.createdAt);
    timeValue = at ? escapeHtml(formatTimestamp(at)) : 'Pending...';
  }

  // ชื่อ/เบอร์ช่างต้องติดมาจาก step ก่อนหน้า (เช่นกรอกไว้ตอน Technician Assigned)
  // ไม่ใช่หายไปพอเปลี่ยนไป step ถัดไป — ใช้ latestTechnicianInfo() ไล่หาย้อนหลัง
  // แทนที่จะดูแค่ entry ของ step ปัจจุบัน (isAutoStep คือ step "received" ไม่มีช่างเกี่ยว)
  const techInfo = isAutoStep ? { name: null, contact: null } : latestTechnicianInfo(t.subStatusTimeline);
  const technicianName = techInfo.name;
  const technicianContact = techInfo.contact;

  // Only one "when" field per step (STEP_TIME_LABELS above) - a separate
  // ETA-in-minutes row used to sit right next to it showing the same idea
  // twice (and kept showing "ETA: Pending..." even once a ticket was fully
  // Resolved, which made no sense at all).
  const extraRowsHtml = isAutoStep ? '' : `
    <div class="status-row">
      <span class="status-row-label">Technician</span>
      <span class="status-row-value${technicianName ? '' : ' pending'}">${technicianName ? escapeHtml(technicianName) : 'Pending...'}</span>
    </div>
    <div class="status-row">
      <span class="status-row-label">Contact</span>
      <span class="status-row-value${technicianContact ? '' : ' pending'}">${technicianContact ? `<a class="tel-link" href="tel:${escapeHtml(technicianContact)}">Call Now</a>` : 'Pending...'}</span>
    </div>`;

  return `
    <div class="status-card">
      <div class="status-card-header">
        <div class="status-card-icon">${ICONS[step.icon]}</div>
        <div>
          <div class="status-card-title">Status: ${escapeHtml(step.label)}</div>
          <div class="status-card-desc">${escapeHtml(STEP_DESCRIPTIONS[step.key])}</div>
        </div>
      </div>
      <div class="status-card-divider"></div>
      <div class="status-row">
        <span class="status-row-label">${escapeHtml(STEP_TIME_LABELS[step.key])}</span>
        <span class="status-row-value${timeValue === 'Pending...' ? ' pending' : ''}">${timeValue}</span>
      </div>
      ${extraRowsHtml}
    </div>`;
}

// The picked image lives INSIDE the description content itself (same box
// as the note text) rather than as a separate preview elsewhere on the
// card - shown above the text in both the read-only and editing views.
function renderAttachedImagePreview() {
  if (!state.detailAttachedImagePreviewUrl) return '';
  return `<img class="note-box-image" src="${state.detailAttachedImagePreviewUrl}" alt="${escapeHtml(state.detailAttachedImageName)}">`;
}

function renderNotesReadOnly(timelineEntry) {
  const imageHtml = renderAttachedImagePreview();
  // Nothing typed yet -> leave the box genuinely empty, not a '—' placeholder.
  const noteText = timelineEntry && timelineEntry.note ? escapeHtml(timelineEntry.note) : '';
  return `<div class="detail-note-box">${imageHtml}${noteText}</div>`;
}

// isAutoStep only changes what the Edit form shows ("received" gets a
// plain Notes box, no Technician/Contact/Event Time fields, since it
// predates any technician even being considered) - Confirm/Back are global
// stage controls and render identically on every tab.
function renderDescriptionCard(t, step) {
  const timelineEntry = latestTimelineEntryFor(t.subStatusTimeline, step.key);
  const isAutoStep = step.key === 'received';
  const editing = state.inProgressDetailEditing;

  return `
    <div class="description-card">
      <div class="description-card-header">${ICONS.fileText} Description</div>
      ${editing ? renderInProgressEditForm(t, isAutoStep) : renderNotesReadOnly(timelineEntry)}
      <div class="attach-buttons">
        <label class="attach-btn">${ICONS.upload} Attach File<input type="file" data-action="pick-attach-file" style="display:none"></label>
        <label class="attach-btn">${ICONS.grid} Upload Image<input type="file" accept="image/*" data-action="pick-attach-image" style="display:none"></label>
      </div>
      ${state.detailAttachedFileName ? `<div class="attach-picked">${ICONS.fileText} ${escapeHtml(state.detailAttachedFileName)}</div>` : ''}
      ${renderInProgressActions(t, editing)}
    </div>`;
}

// Technician Name / Contact / Event Time start being relevant right at
// "Technician Assigned" (that's literally where you record who's being
// called in) - only "Received" predates a technician being considered at
// all, so it alone gets a plain Notes box.
function renderInProgressEditForm(t, isAutoStep) {
  const notesField = `
    <div class="field">
      <span class="field-label">Notes</span>
      ${renderAttachedImagePreview()}
      <textarea class="text-input detail-note-box" data-bind="detailNoteDraft" placeholder="Add more description here...">${escapeHtml(state.detailNoteDraft)}</textarea>
    </div>`;
  if (isAutoStep) return notesField;

  return notesField + `
    <div class="field">
      <span class="field-label">Technician Name</span>
      <input class="text-input" type="text" data-bind="detailTechnicianName" value="${escapeHtml(state.detailTechnicianName)}">
    </div>
    <div class="field">
      <span class="field-label">Contact Number</span>
      <input class="text-input" type="text" data-bind="detailTechnicianContact" value="${escapeHtml(state.detailTechnicianContact)}">
    </div>
    <div class="field">
      <span class="field-label">Event Time</span>
      <input class="text-input" type="datetime-local" step="${EVENT_TIME_STEP_MINUTES * 60}" max="${toDatetimeLocalValue(new Date())}" data-bind="detailEventTimeDraft" value="${escapeHtml(state.detailEventTimeDraft)}">
    </div>`;
}

// Two independent kinds of action, present identically on every tab
// (including Received/Assigned) since they do different jobs:
// - Edit/Save only ever touches whichever tab you're viewing's notes/
//   technician info (advance: false in saveInProgressStepDetails) - never
//   the ticket's real stage, so it's safe on a step already passed or not
//   yet reached.
// - Confirm/Back are global: they move the ticket's real current_sub_status
//   one step forward/backward from wherever it actually is right now,
//   regardless of which tab happens to be open. Confirm is the everyday
//   action (no dialog); Back is explicitly the "I clicked wrong" undo, so
//   it's gated behind a confirm dialog.
function renderInProgressActions(t, editing) {
  const next = nextManualStage(t);
  const prev = prevManualStage(t);
  // Edit/Save/Cancel sits on the left; Back/Confirm is deliberately grouped
  // on the right so it always lands in the bottom-right corner of the card
  // (the two "move the ticket's real stage" actions, kept visually apart
  // from the per-step note editing controls).
  return `
    <div class="detail-panel-actions">
      <div class="detail-panel-actions-left">
        <button type="button" class="btn-outline" data-action="log-customer-update" data-id="${escapeHtml(t.id)}">${ICONS.bell} Log Customer Update</button>
        ${editing
          ? `<button type="button" class="ack-btn" data-action="save-inprogress-details" data-id="${escapeHtml(t.id)}">${ICONS.checkCircle} Save</button>
             <button type="button" class="btn-outline" data-action="cancel-inprogress-edit">Cancel</button>`
          : `<button type="button" class="btn-outline" data-action="edit-inprogress-step">${ICONS.tool} Edit</button>`}
      </div>
      <div class="detail-panel-actions-right">
        <button type="button" class="btn-outline" data-action="revert-inprogress-stage" data-id="${escapeHtml(t.id)}" data-step="${escapeHtml(prev || '')}" ${prev ? '' : 'disabled'}>
          ← Back
        </button>
        <button type="button" class="ack-btn" data-action="advance-inprogress-stage" data-id="${escapeHtml(t.id)}" data-step="${escapeHtml(next || '')}" ${next ? '' : 'disabled'}>
          ${ICONS.checkCircle} Confirm
        </button>
      </div>
    </div>`;
}

function formatDuration(ms) {
  if (ms == null || ms < 0) return '0m';
  const totalMinutes = Math.round(ms / 60000);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}

// Only shown while viewing the "Resolved" tab - recaps every step's real
// timestamp (not the free-text eventTime, so durations are always accurate)
// and who was responsible, matching the reference mockup's timeline layout.
function renderServiceTimelineSummary(t) {
  // "Ticket Received" means something different in this narrative timeline
  // than it does on the Stepper above: here it's the moment CSMC actually
  // acknowledged the ticket (a person's action), not assignedAt (the ticket
  // merely landing in the team's queue, which the Stepper's own circle still
  // reflects via subStatusStepDone() - unchanged, so the two don't drift).
  const middleEntries = SUB_STATUS_STEPS.map(step => {
    if (step.key === 'received') {
      return { step, done: !!t.acknowledgedAt, at: t.acknowledgedAt, responsible: t.acknowledgedBy || '—' };
    }
    const timelineEntry = latestTimelineEntryFor(t.subStatusTimeline, step.key);
    // ชื่อช่างต้อง carry-forward จาก step ก่อนหน้าด้วย (เช่นกรอกไว้ตอน Technician
    // Assigned) ไม่ใช่ต้องกรอกซ้ำทุก step — แต่โชว์เฉพาะ step ที่เกิดขึ้นแล้วจริง
    // (มี timelineEntry ของ step นี้) กัน step ที่ยังไม่ถึงโชว์ชื่อช่างทั้งที่ยังไม่เกิด
    const carriedTechnician = timelineEntry ? latestTechnicianInfo(t.subStatusTimeline).name : null;
    // eventTime = เวลาที่กรอกเอง (ถ้ามี), ไม่งั้น fallback ไป createdAt (เวลาที่กด Confirm/Save จริง)
    return {
      step,
      done: subStatusStepDone(t, step),
      at: timelineEntry ? (timelineEntry.eventTime || timelineEntry.createdAt) : null,
      responsible: (timelineEntry && timelineEntry.createdBy) || '—',
      technician: carriedTechnician || null,
    };
  });
  const resolvedEntry = middleEntries[middleEntries.length - 1]; // SUB_STATUS_STEPS' last key is 'completed'

  // Two bracketing rows, added 2026-07-24 per brief - not real workflow
  // stages (no Edit/Confirm for either), so they live only in this read-only
  // summary, not the Stepper: Start Time is when the incident itself
  // happened (t.startTime, straight from the uploaded/event data - parsed
  // through parseTicketTime() since that field's raw format varies); End
  // Time is job closure, which this system doesn't yet track as a separate
  // step from Resolved, so per explicit product decision it just mirrors
  // Resolved's own timestamp rather than inventing a new one.
  const startMs = parseTicketTime(t.startTime);
  const startEntry = {
    step: { icon: 'inbox', label: 'Start Time' },
    done: isFinite(startMs),
    at: isFinite(startMs) ? new Date(startMs).toISOString() : null,
    responsible: 'System',
  };
  const endEntry = {
    step: { icon: 'checkCircle', label: 'End Time' },
    done: resolvedEntry.done,
    at: resolvedEntry.at,
    responsible: resolvedEntry.responsible,
    technician: resolvedEntry.technician,
  };
  const entries = [startEntry, ...middleEntries, endEntry];

  let prevMs = null;
  const itemsHtml = entries.map((entry, i) => {
    const ms = entry.done && entry.at ? new Date(entry.at).getTime() : null;
    const durationHtml = (ms != null && prevMs != null)
      ? `<p class="timeline-item-duration">Duration: ${formatDuration(ms - prevMs)}</p>` : '';
    if (ms != null) prevMs = ms;
    return `
      <div class="timeline-item${i === entries.length - 1 ? ' last' : ''}">
        <div class="timeline-item-icon${entry.done ? ' done' : ''}">${ICONS[entry.step.icon]}</div>
        <div class="timeline-item-body">
          <div class="timeline-item-head">
            <span class="timeline-item-label">${escapeHtml(entry.step.label)}</span>
            <span class="timeline-item-time${ms != null ? '' : ' pending'}">${ms != null ? escapeHtml(formatTimestamp(entry.at)) : 'Pending...'}</span>
          </div>
          <p class="timeline-item-responsible">Responsible: ${escapeHtml(entry.responsible)}</p>
          ${entry.technician ? `<p class="timeline-item-technician">Technician: ${escapeHtml(entry.technician)}</p>` : ''}
          ${durationHtml}
        </div>
      </div>`;
  }).join('');

  // Total Duration deliberately stays Acknowledge -> Resolved (per explicit
  // 2026-07-24 product decision) even though the timeline above now also
  // shows the wider Start Time/End Time bracket - the two bracketing rows
  // are informational only and don't feed this calculation.
  const firstMs = t.acknowledgedAt ? new Date(t.acknowledgedAt).getTime() : null;
  const lastMs = resolvedEntry.done && resolvedEntry.at ? new Date(resolvedEntry.at).getTime() : null;
  const totalHtml = (firstMs != null && lastMs != null)
    ? `<span class="timeline-total-badge">Total Duration: ${formatDuration(lastMs - firstMs)}</span>` : '';

  // Shows which of the 5 teams (and who currently owns it) handled this
  // ticket - lets whoever it gets handed to at a glance see who did what,
  // without having to scroll back up to the page header's Owner label.
  const teamData = GROUP_DATA[t.tag];
  const teamHtml = teamData
    ? `<span class="timeline-team-badge">${escapeHtml(teamData.name)} (${escapeHtml(teamData.tag)})${t.acknowledgedBy ? ' — ' + escapeHtml(t.acknowledgedBy) : ''}</span>`
    : '';

  return `
    <div class="timeline-summary-card">
      <div class="timeline-summary-header">${ICONS.activity} Service Timeline Summary ${teamHtml}${totalHtml}</div>
      <div class="timeline-items">${itemsHtml}</div>
    </div>`;
}

function renderDashboard() {
  const groups = computeGroups();
  const triageCount = state.uploadedGroups ? (state.uploadedUnmatched || 0) : 0;
  const handoverCount = incomingHandoverTickets().length;
  return `
    <div class="page-header">
      ${expandSidebarButton()}
      <div class="page-title">Incident Triage — System Groups</div>
      <div class="page-header-actions">
        ${handoverCount ? `
        <button type="button" class="btn-outline handover-btn" data-action="open-inprogress" title="${handoverCount} ticket${handoverCount > 1 ? 's' : ''} waiting for your handover decision">
          ${ICONS.userCheck} Handover <span class="nav-badge">${handoverCount}</span>
        </button>` : ''}
        <button type="button" class="btn-outline" data-action="open-manual-entry">${ICONS.upload} แจ้งเหตุด้วยตนเอง</button>
        <button class="btn-outline" data-action="refresh">${ICONS.refresh} Refresh</button>
      </div>
    </div>

    <div class="triage-banner" data-action="open-triage">
      <span class="triage-banner-count">${triageCount}</span> new tickets pending triage — not yet assigned to a group
      <span class="triage-banner-link">view →</span>
    </div>

    <div class="groups-grid">
      ${groups.map(g => `
        <div class="group-card" data-action="open-group" data-tag="${g.tag}">
          <div class="group-tag-wrap">
            <div class="group-tag">${escapeHtml(g.owner)}</div>
            ${g.overdueCount ? `<span class="nav-badge group-overdue-badge">${g.overdueCount}</span>` : ''}
          </div>
          <div class="group-owner">${escapeHtml(g.tag)}</div>
          <div class="group-divider"></div>
          <div class="group-total">
            <span class="group-total-num">${g.total}</span>
            <span class="group-total-label">Total ticket</span>
          </div>
          <div class="group-divider-tight"></div>
          <div class="group-statuses">
            ${g.statuses.map(s => `
              <div class="group-status-row">
                <span class="group-status-label"><span class="status-dot" data-status="${s.label}"></span>${s.label}</span>
                <span class="group-status-count">${s.count}</span>
              </div>`).join('')}
          </div>
        </div>`).join('')}
    </div>`;
}

/* ============================================================
   Render: Analyze (Phase 3 — docs/BRIEF_Phase3_Analyze.md ข้อ 5)
   คั่นระหว่าง Triage กับ In Progress: Triage -> [Analyze: ดูสาเหตุ + ยืนยัน] -> In Progress
   ============================================================ */

function analyzeTickets() {
  return (state.pendingReview || []).slice().sort(byNewest);
}

function renderAnalyze() {
  const tickets = analyzeTickets();
  const { pageItems, page, totalPages } = paginate(tickets, state.analyzePage);
  const columns = getColumnLayout('analyze');
  return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb"><span class="breadcrumb-current">Analyze</span></div>
      <div class="page-header-actions">
        <button class="btn-outline" data-action="refresh">${ICONS.refresh} Refresh</button>
      </div>
    </div>
    <div style="font-size:12.5px;color:var(--text-muted);margin-bottom:18px">${tickets.length} ticket awaiting root-cause review before moving to In Progress</div>
    <div class="table-card">
      <div class="table-scroll">
        <table class="table-resizable">
          ${renderColGroup([], columns, [])}
          <thead><tr>${columns.map(c => renderColumnHeader('analyze', c)).join('')}</tr></thead>
          <tbody>
            ${pageItems.map(t => `
              <tr class="inprogress-row" data-dblclick-action="open-analyze-detail" data-id="${escapeHtml(t.id)}">
                ${columns.map(c => ticketColumnCell(c.key, t)).join('')}
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      ${tickets.length === 0 ? `<div class="empty-state">No tickets awaiting review right now — if syslog_server.py/diagnose_worker.py just started, a new ticket and its diagnosis may take a moment to appear</div>` : ''}
    </div>
    ${renderPagination(tickets.length, page, totalPages, 'analyze-page-prev', 'analyze-page-next')}`;
}

// HH:MM:SS 24-hour clock — terser than the app's usual full-date timestamp,
// used only where a bare time-of-day reads better (signal status line)
function formatClockTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-GB', { hour12: false });
}

// "Down for 14m" / "Recovered · down 14m" — compact form for on-screen text
// (mockup ข้อ 3.2/7.3); เวลานาฬิกาที่แน่นอน (since/at) ไม่ได้หายไปไหน แค่ย้ายไปอยู่ใน
// title tooltip ตอน hover แทน ไม่ให้ต้องพิมพ์เต็มบนหน้าจอทุกครั้ง
function signalStatusLine(t) {
  const recovered = t.endTime && isFinite(parseTicketTime(t.endTime));
  const downMs = recovered
    ? parseTicketTime(t.endTime) - parseTicketTime(t.startTime)
    : Date.now() - parseTicketTime(t.startTime);
  const dur = formatDuration(downMs);
  const tooltip = recovered
    ? 'Recovered at ' + formatClockTime(t.endTime)
    : 'Down since ' + formatClockTime(t.startTime);
  return { ok: recovered, text: recovered ? 'Recovered · down ' + dur : 'Down for ' + dur, tooltip };
}

// หัวข้อการ์ด Quick เอาแค่ป้ายเหตุการณ์สั้นๆ ("Link down"/"Link restored") ตามข้อ 3.1 — ชื่อ
// อุปกรณ์/สายที่ตามหลัง ": " เดิมไปโผล่ซ้ำใน REPORTED BY / AFFECTED SITE ด้านล่างอยู่แล้ว
// เจาะจง pattern ที่ syslog_parser.py สร้างเท่านั้น (build_ticket_fields) กัน TT ที่แจ้งเหตุ
// ด้วยตนเอง (summary เป็น free text ไม่มี pattern นี้) โดนตัดข้อความผิดทิ้ง
const AUTO_TITLE_RE = /^(Link down|Link restored):/;
function shortIncidentTitle(description) {
  const d = description || '-';
  const m = d.match(AUTO_TITLE_RE);
  return m ? m[1] : d;
}

// "Checked 7 min ago" (ข้อ 4.4) อ่านง่ายกว่า timestamp เต็มตอนเหลือบดู แต่เกิน 24 ชม.
// ค่อยขึ้นวันที่เต็มเพราะ "3 days ago" ไม่ช่วยอะไรแล้ว ตอนนั้นอยากรู้วันที่จริงมากกว่า
function formatRelativeTime(iso) {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (!isFinite(ms)) return null;
  if (ms < 60000) return 'just now';
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return mins + ' min ago';
  const hours = Math.floor(mins / 60);
  if (hours < 24) return hours + (hours === 1 ? ' hour ago' : ' hours ago');
  return formatTimestamp(iso);
}

function copyText(text) {
  if (text && navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
}

// Short human phrase for the PRIORITY row's suffix, derived from the same
// structured impact text REASONING shows in full below - kept terse here
// since this row is a glance-level summary, not the full explanation.
function impactSuffix(impact) {
  if (!impact) return null;
  if (impact.includes('isolated')) return 'Entire site unreachable';
  if (impact.includes('sites down simultaneously')) return 'Multiple sites affected';
  return null;
}

function renderAnalyzeQuickTriage(t) {
  const signal = signalStatusLine(t);
  const reasoning = t.reasoning || {};
  const suffix = impactSuffix(reasoning.impact);
  const rawLogsText = t.rawSyslogDisplay || t.rawSyslog || '-';
  return `
    <div class="analyze-card">
      <div class="analyze-card-head"><span class="analyze-card-title">Quick</span></div>
      <div class="analyze-card-body">
        <div class="analyze-quick-title">${escapeHtml(shortIncidentTitle(t.description))}</div>
        <div class="analyze-priority-row">
          ${t.priority ? `<span class="priority-chip" style="color:${PRIORITY_COLOR[t.priority] || ''};border-color:${PRIORITY_COLOR[t.priority] || ''}">${escapeHtml(t.priority)}</span>` : '<span>-</span>'}
          ${suffix ? `<span class="analyze-confidence-word">· ${escapeHtml(suffix)}</span>` : ''}
        </div>
        <div class="analyze-signal-line" title="${escapeHtml(signal.tooltip)}">
          <span class="analyze-signal-dot ${signal.ok ? 'ok' : 'down'}"></span>${escapeHtml(signal.text)}
        </div>
        ${t.confidence != null && t.confidence < 50 ? `<p class="analyze-low-confidence">Low confidence — verify before confirming</p>` : ''}

        <div class="analyze-2col">
          <div class="field">
            <span class="field-label">Reported by</span>
            <div class="field-value">${escapeHtml(t.reportedBy || '-')}${t.interface ? ` · ${escapeHtml(t.interface)}` : ''}</div>
          </div>
          <div class="field">
            <span class="field-label">Affected site</span>
            <div class="field-value">${escapeHtml(t.node || '-')}</div>
          </div>
        </div>

        <div class="field">
          <span class="field-label">Reasoning</span>
          <div class="analyze-reasoning-group">
            <div class="analyze-reasoning-row">
              <span class="analyze-reasoning-label">Rule</span>
              <span class="analyze-reasoning-value analyze-reasoning-rule">${escapeHtml(reasoning.rule || '-')}</span>
            </div>
            <div class="analyze-reasoning-row">
              <span class="analyze-reasoning-label">Impact</span>
              <span class="analyze-reasoning-value">${escapeHtml(reasoning.impact || '-')}</span>
            </div>
            <div class="analyze-reasoning-row">
              <span class="analyze-reasoning-label">Priority</span>
              <span class="analyze-reasoning-value">${escapeHtml(reasoning.priority || '-')}</span>
            </div>
          </div>
        </div>

        <div class="field">
          <div class="analyze-box-head">
            <span class="field-label">Raw logs<span class="analyze-box-source"> from ${escapeHtml(t.reportedBy || '-')}</span></span>
            <button type="button" class="analyze-copy-plain" data-action="copy-raw-logs" data-id="${escapeHtml(t.id)}">Copy</button>
          </div>
          <pre class="analyze-raw-box">${escapeHtml(rawLogsText)}</pre>
        </div>
      </div>
    </div>`;
}

// Keep only the lines that actually help triage a link event - 'show
// logging' on a long-running lab router accumulates hundreds of lines of
// our own diagnose_worker.py SSH login/logout noise (%SSH-*, %SEC_LOGIN-*,
// %SYS-6-LOGOUT, %SYS-5-CONFIG_I) that has nothing to do with the fault.
// Filtered for DISPLAY only - the full text is still what's stored in the
// DB and returned by the API untouched (checkpoint #4 needs the complete
// record available, just not dumped on screen by default).
const LOG_LINE_KEEP_RE = /%(LINK|LINEPROTO|OSPF|BGP)-/;
function filterLogOutput(output) {
  const lines = (output || '').split('\n');
  const kept = lines.filter(l => LOG_LINE_KEEP_RE.test(l));
  return { text: kept.join('\n'), kept: kept.length, total: lines.length };
}

// One <details> per AUTO_COMMAND from the latest diagnosis - the command
// used for cause detection (always first) opens expanded, the rest start
// collapsed. All in one card, never a separate page (checkpoint #4/#5).
// เปิด/ปิดจริงอ้างอิงจาก state.analyzeOpenSections (ไม่ใช่ i === 0 ตรงๆ) เพราะฟังก์ชันนี้
// ถูกเรียกใหม่ทุก 5 วิจาก polling — ถ้าไม่จำค่าที่ user กางไว้ มันจะพับ/กางกลับ default เอง
function renderCommandOutputSections(runs) {
  if (!runs.length) return '';
  return runs.map((run, i) => {
    const isLogging = run.command.trim() === 'show logging';
    const filtered = isLogging ? filterLogOutput(run.output) : null;
    const body = filtered ? filtered.text : (run.output || '(no output)');
    // ปิดไว้ก่อนทุกแถวเป็นค่าเริ่มต้น (เดิม i===0 กางให้อัตโนมัติ) — user ขอเอง อยากดูค่อยกด
    const isOpen = state.analyzeOpenSections.hasOwnProperty(run.command)
      ? state.analyzeOpenSections[run.command]
      : false;
    return `
      <details class="analyze-command-section" data-command="${escapeHtml(run.command)}"${isOpen ? ' open' : ''}>
        <summary>
          <span class="analyze-command-name">${escapeHtml(run.command)}</span>
          ${filtered ? `<span class="analyze-command-filtered"> — ${filtered.kept} of ${filtered.total} lines</span>` : ''}
          <button type="button" class="btn-outline analyze-copy-btn" data-action="copy-command-output" data-command="${escapeHtml(run.command)}">Copy</button>
        </summary>
        <pre class="analyze-command-body">${escapeHtml(body || '(no matching lines)')}</pre>
      </details>`;
  }).join('');
}

// ประโยคขยายบรรทัดที่ 2 ของแผง CAUSE (ข้อ 1) — บอกลำดับเหตุการณ์ให้ชัดว่า "ถูกสั่งปิด" กับ
// "สัญญาณหายเอง" ต่างกันตรงไหน เพราะ cause_summary เดี่ยวๆ ไม่พอบอกลำดับ ผูกกับ cause_summary
// ทั้ง 4 แบบตรงตัวจาก lib/cause_rules.py (RULES list) — ถ้าไฟล์นั้นเพิ่ม/เปลี่ยนข้อความ
// cause_summary ต้องมาแก้ mapping นี้ให้ตรงกันด้วย ไม่งั้นบรรทัดขยายจะหายไปเงียบๆ
function causeExplanationHtml(t, device) {
  const iface = escapeHtml(t.interface || 'the interface');
  const dev = escapeHtml(device || 'the device');
  switch ((t.causeSummary || '').trim()) {
    case 'Port administratively shut down':
      return `${iface} on ${dev} was set to <code>shutdown</code>; the line protocol followed it down.`;
    case 'Physical-layer signal loss (line protocol down, not administratively shut)':
      return `${iface} on ${dev} lost its line protocol without an administrative shutdown — consistent with a physical signal loss.`;
    case 'Cable or transceiver fault (CRC/input error counters above 0)':
      return `${iface} on ${dev} is logging CRC/input errors above zero — consistent with a cable or transceiver fault.`;
    case 'Device unreachable (SSH failed), consistent with a link failure':
      return `${dev} could not be reached over SSH — consistent with the link itself being down.`;
    default:
      return null;
  }
}

// ป้ายบอกแหล่งที่มาของสาเหตุ — ต้องเห็นชัดว่ามาจากกฎหรือจากแบบจำลองภาษา
//
// ⚠️ ทำไมจำเป็น (docs/Plan.md 3.5.7): ผลที่มาจากกฎให้คำตอบเดิมทุกครั้ง ตรวจสอบย้อนกลับได้ 100%
// ส่วนผลจากแบบจำลองภาษาวัดแล้วพบว่าไม่การันตีผลเท่ากันทุกครั้งแม้ตรึงค่าทุกอย่าง — เจ้าหน้าที่
// ต้องรู้ว่ากำลังอ่านอะไรอยู่ก่อนตัดสินใจ ห้ามแสดงสองอย่างนี้ให้ดูเหมือนกัน
function causeSourceBadgeHtml(source) {
  if (source === 'rule') {
    return `<span class="cause-source cause-source-rule" title="Matched a deterministic rule in lib/cause_rules.py — same input always gives the same result">Rule</span>`;
  }
  if (source === 'llm') {
    return `<span class="cause-source cause-source-llm" title="Interpreted by a local language model, then verified: every cited line was checked to exist in the device output and to support the stated cause">AI-assisted</span>`;
  }
  return '';
}

function renderDeepAnalysisCard(t) {
  const checkedRelative = t.diagnosedAt ? formatRelativeTime(t.diagnosedAt) : null;
  const checkedTooltip = t.diagnosedAt ? formatTimestamp(t.diagnosedAt) : '';
  const runs = state.analyzeDiagnosisRuns;
  // runs ท้ายสุดคือ device ที่ใช้สรุปสาเหตุจริง (ถ้า fallback ไป Core runs ท้ายสุดจะเป็นของ
  // Core — ดูคอมเมนต์ที่ loadAnalyzeDiagnosis()) เอามาโชว์เป็นแหล่งที่มาของ REFERENCE
  const refDevice = runs.length ? runs[runs.length - 1].device : (t.node || '-');
  const causeDetail = causeExplanationHtml(t, refDevice);

  // 4 states per diagnose_state (docs/BRIEF_Phase3_Analyze.md section 5) - running shows
  // a skeleton loader; unreachable/undetermined get a distinct tone but still show output
  const bodyHtml = !t.diagnoseState ? `
    <div class="diagnose-banner diagnose-banner-pending">Not yet diagnosed — waiting for diagnose_worker.py to pick this up (keep that window running)</div>
  ` : t.diagnoseState === 'running' ? `
    <div class="diagnose-banner diagnose-banner-running"><span class="diagnose-skeleton"></span>Checking...</div>
  ` : `
    ${t.diagnoseState === 'unreachable' ? `<div class="diagnose-banner diagnose-banner-unreachable">Device unreachable — treated as evidence consistent with a link failure</div>` : ''}
    ${t.diagnoseState === 'undetermined' ? `<div class="diagnose-banner diagnose-banner-undetermined">Could not determine a cause automatically — please verify manually</div>` : ''}
    <div class="analyze-cause-panel">
      <div class="analyze-cause-title">
        ${escapeHtml(t.causeSummary || 'Cause not determined')}
        ${causeSourceBadgeHtml(t.causeSource)}
      </div>
      ${causeDetail ? `<div class="analyze-cause-detail">${causeDetail}</div>` : ''}
      ${t.llmExplanation ? `
        <div class="analyze-cause-llm">
          <span class="analyze-cause-llm-label">Plain-language summary</span>
          ${escapeHtml(t.llmExplanation)}
        </div>` : ''}
    </div>
    ${t.llmNote ? `
      <div class="diagnose-banner diagnose-banner-rejected">
        <strong>Model answer rejected</strong> — ${escapeHtml(t.llmNote)}
      </div>` : ''}
    <div class="field">
      <span class="analyze-label-plain">Reference</span>
      <div class="analyze-ref-box">
        <div class="analyze-ref-head">
          <span class="analyze-mono-source">${escapeHtml(refDevice)}</span>
          <button type="button" class="btn-outline analyze-copy-btn" data-action="copy-reference" data-id="${escapeHtml(t.id)}">Copy</button>
        </div>
        <pre class="analyze-ref-body">${escapeHtml(t.evidenceLines || '-')}</pre>
      </div>
    </div>
  `;

  return `
    <div class="analyze-card">
      <div class="analyze-card-head">
        <span class="analyze-card-title">Deep analysis</span>
        <span class="analyze-last-checked"${checkedTooltip ? ` title="${escapeHtml(checkedTooltip)}"` : ''}>${checkedRelative ? 'Checked ' + checkedRelative : 'Never checked'}</span>
        <button type="button" class="btn-outline" data-action="recheck-diagnosis" data-id="${escapeHtml(t.id)}" ${t.diagnoseState === 'running' ? 'disabled' : ''}>${ICONS.refresh} Re-check</button>
      </div>
      <div class="analyze-card-body">
        ${bodyHtml}
        ${runs.length ? `
          <div class="field">
            <span class="field-label">Command output</span>
            ${renderCommandOutputSections(runs)}
          </div>` : ''}
      </div>
    </div>`;
}

function renderManualInvestigationCard(t) {
  const result = state.analyzeManualResult;
  const canRun = state.analyzeManualDevice && state.analyzeManualCommand && !state.analyzeManualRunning;
  // Best-effort hint only (docs/BRIEF_Phase3_Analyze.md section 5.1) - flags the
  // affected site as unreachable if the last diagnosis had to fall back to Core for
  // it, so picking it in the dropdown doesn't waste a click on a command that will
  // just time out. Not a live reachability probe - it reflects the last check only.
  const unreachableSite = t.node !== 'Core' && (t.rawOutput || '').includes(t.node) && (t.rawOutput || '').includes('no response')
    ? t.node : null;
  return `
    <div class="analyze-card">
      <div class="analyze-card-head"><span class="analyze-card-title">Manual</span></div>
      <div class="analyze-card-body">
        <div class="analyze-manual-controls">
          <div class="select-wrap">
            <select class="raw-select" data-action="pick-analyze-device">
              <option value="" ${state.analyzeManualDevice ? '' : 'selected'} disabled hidden>Select device…</option>
              ${ANALYZE_DEVICES.map(d => `<option value="${escapeHtml(d)}" ${state.analyzeManualDevice === d ? 'selected' : ''}>${escapeHtml(d)}${d === unreachableSite ? ' (unreachable)' : ''}</option>`).join('')}
            </select>
            <span class="select-arrow">▾</span>
          </div>
          <div class="select-wrap">
            <select class="raw-select" data-action="pick-analyze-command">
              <option value="" ${state.analyzeManualCommand ? '' : 'selected'} disabled hidden>Select command…</option>
              ${ANALYZE_MANUAL_COMMANDS.map(c => `<option value="${escapeHtml(c)}" ${state.analyzeManualCommand === c ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('')}
            </select>
            <span class="select-arrow">▾</span>
          </div>
          <button type="button" class="ack-btn" data-action="run-manual-command" data-id="${escapeHtml(t.id)}" ${canRun ? '' : 'disabled'}>
            ${state.analyzeManualRunning ? 'Running…' : 'Run Command'}
          </button>
        </div>
        ${result ? `
          <pre class="analyze-raw-box analyze-manual-output">${escapeHtml(result.output || '(no output)')}</pre>
          <div class="analyze-manual-actions">
            <button type="button" class="btn-outline" data-action="copy-manual-output">Copy</button>
            <button type="button" class="btn-outline" data-action="download-manual-output" data-id="${escapeHtml(t.id)}">Download</button>
          </div>` : ''}
      </div>
    </div>`;
}

// Phase 6 — ตัวเลขผลกระทบที่จะถูกกรอกลงชีท PW Info แถว 25-34
//
// ทำไมต้องให้แก้ได้ตรงนี้ แทนที่จะให้ไปแก้ในไฟล์ Excel เอง:
//   ตัวเลขสิบช่องนี้ป้อนเข้าสูตรคำนวณ Severity ของแม่แบบ ซึ่งกำหนดลำดับการอนุมัติจริง
//   ถ้าให้ไปแก้ในไฟล์ ระบบจะไม่รู้ว่าตัวเลขสุดท้ายคืออะไร และไม่มีบันทึกว่าใครเป็นคนตัดสิน
//   แก้ที่นี่แล้วระบบร่างไฟล์ใหม่ให้เอง ตัวเลขในเอกสารกับในระบบจึงตรงกันเสมอ
//
// แสดงเฉพาะตอนที่ระบบเสนอค่ามาแล้ว (กดประเมินประเภทงานแล้ว) เพราะก่อนหน้านั้น
// ยังไม่รู้ขอบเขตของเหตุ จะเสนอตัวเลขอะไรก็เป็นการเดา
function renderImpactBlock(t) {
  if (!t.impactSuggested) return '';
  const fields = t.impactFields || [];
  const current = state.analyzeImpactDraft || t.impactValues || t.impactSuggested || {};
  const reasons = t.impactReasons || {};
  return `
    <div class="analyze-disposition-block analyze-impact">
      <span class="field-label">ตัวเลขผลกระทบ (ชีท PW Info แถว 25-34)</span>
      <p class="analyze-impact-basis">
        ${escapeHtml(t.impactBasis || '')}
        ${t.impactBy
          ? `<span class="chip-confirmed">ยืนยันโดย ${escapeHtml(t.impactBy)}</span>`
          : `<span class="chip-suggested">ระบบแนะนำ</span>`}
      </p>
      <div class="analyze-impact-grid">
        ${fields.map(f => `
          <label class="analyze-impact-item" title="${escapeHtml(reasons[f.key] || '')}">
            <span class="analyze-impact-label">${escapeHtml(f.label)}</span>
            <input type="number" min="0" class="analyze-impact-input"
              data-action="edit-impact" data-key="${escapeHtml(f.key)}"
              value="${Number(current[f.key] || 0)}">
          </label>`).join('')}
      </div>
      <div class="analyze-pw-row">
        <button type="button" class="btn-outline" data-action="save-impact" data-id="${escapeHtml(t.id)}" ${state.analyzeSavingImpact ? 'disabled' : ''}>
          ${state.analyzeSavingImpact ? 'กำลังบันทึก…' : (t.impactBy ? 'บันทึกการแก้ไข' : 'ยืนยันตัวเลขนี้')}
        </button>
        ${state.analyzeImpactDraft ? `<button type="button" class="btn-outline" data-action="reset-impact">คืนค่าที่ระบบแนะนำ</button>` : ''}
      </div>
      <p class="analyze-pw-hint">
        เอาเมาส์ชี้ที่ชื่อช่องเพื่อดูว่าตัวเลขนั้นมาจากการนับอะไร ·
        ระบบนับหน่วยบริการจาก topology ไม่ได้นับจำนวนผู้ใช้ปลายทาง
      </p>
    </div>`;
}

// Phase 6 — ส่วนแสดงร่าง PW ในการ์ดประเภทงาน
//
// ขึ้นต่อเมื่อผู้ตรวจยืนยันว่าเป็นงานประเภท configuration แล้วเท่านั้น
// และต้องเขียนให้ชัดว่านี่คือ "ร่างสำหรับ L2" ไม่ใช่ PW ที่ยื่นแล้ว — L1 ไม่ใช่คนยื่น PW
//
// กรณีร่างไม่ได้ก็ต้องแสดงเหตุผลด้วย ไม่ใช่เงียบไป เพราะ "ระบบไม่ยอมร่างเพราะอะไร"
// เป็นข้อมูลที่ผู้ตรวจต้องรู้พอๆ กับตัวร่างเอง
function renderPwDraftBlock(t) {
  // แสดงตั้งแต่ระบบ "เสนอ" ว่าเป็น configuration ไม่ต้องรอคนยืนยันก่อน
  // ร่างเป็นแค่เอกสารตั้งต้นที่ยังไม่มีผลอะไร การได้เห็นเนื้อหาเร็วช่วยให้ผู้ตรวจ
  // ใช้ประกอบการตัดสินใจได้ด้วยซ้ำว่างานนี้เป็นงานของ L2 จริงไหม
  //
  // แต่ถ้าคนยืนยันไปแล้วว่าเป็นประเภทอื่น ต้องซ่อนทันที — คำตัดสินของคนอยู่เหนือ
  // ข้อเสนอของระบบเสมอ
  const effective = t.confirmedDisposition || t.disposition;
  if (effective !== 'configuration') return '';
  const unconfirmed = !t.confirmedDisposition;
  const redraw = `<button type="button" class="btn-outline" data-action="draft-pw" data-id="${escapeHtml(t.id)}" ${state.analyzeDrafting ? 'disabled' : ''}>${state.analyzeDrafting ? 'กำลังร่าง…' : (t.pwDraftFile ? 'ร่างใหม่' : 'ร่าง PW')}</button>`;
  return `
    <div class="analyze-disposition-block analyze-pw-draft">
      <span class="field-label">ร่าง PW สำหรับ L2</span>
      ${t.pwDraftFile ? `
        <div class="analyze-pw-row">
          <span class="analyze-pw-file">${escapeHtml(t.pwDraftFile)}</span>
          ${t.pwDraftMop ? `<span class="chip-suggested">แม่แบบ ${escapeHtml(t.pwDraftMop)}</span>` : ''}
          <button type="button" class="btn-outline" data-action="download-pw-draft" data-id="${escapeHtml(t.id)}">ดาวน์โหลด</button>
          ${redraw}
        </div>
        <p class="analyze-pw-hint">
          ระบบกรอกให้เฉพาะชีท MOP · NoGo Plan · NoGo MOP ส่วนที่เหลือ L2 เป็นผู้กรอกและยื่นเอง
          เอกสารนี้ยังไม่ผ่านการตรวจ
        </p>` : `
        <div class="analyze-pw-row">${redraw}</div>
        <p class="analyze-pw-hint">${t.pwDraftNote
          ? 'ระบบไม่ได้ร่างให้ — ' + escapeHtml(t.pwDraftNote)
          : 'ยังไม่มีร่าง — กดปุ่มด้านบนเพื่อให้ระบบร่างจากแม่แบบที่ตรงกับอาการ'}</p>`}
      ${unconfirmed ? `<p class="analyze-pw-hint">
        ร่างจากประเภทงานที่ระบบเสนอ ยังไม่มีผู้ยืนยัน — ถ้ายืนยันเป็นประเภทอื่นในภายหลัง
        ร่างนี้จะใช้ไม่ได้
      </p>` : ''}
    </div>`;
}

// Phase 6 — การ์ดแสดงผลการตัดสิน "ประเภทงาน" พร้อมเหตุผลและรายการที่ตัดออก
//
// ทำไมต้องแสดง ruledOut ด้วย ไม่ใช่แค่คำตอบ:
//   ถ้าโชว์แต่คำตอบ agent ได้แค่ "เชื่อหรือไม่เชื่อ" ซึ่งตรวจสอบอะไรไม่ได้เลย
//   การแสดงว่า "ตัดอะไรออกเพราะอะไร" ทำให้คนไล่ตามเหตุผลได้จริงและจับผิดระบบได้
//   ถ้าระบบคิดผิด — เป็นหัวใจของ human-in-the-loop ที่เกณฑ์ข้อ 2 ต้องการ
function renderDispositionCard(t) {
  const has = !!t.disposition;
  const low = t.dispositionConfidence === 'low';
  const reasons = t.dispositionReasons || [];
  const ruledOut = t.dispositionRuledOut || [];
  return `
    <div class="analyze-card">
      <div class="analyze-card-head">
        <span class="analyze-card-title">ประเภทงาน</span>
        <span class="analyze-last-checked"${t.dispositionAt ? ` title="${escapeHtml(formatTimestamp(t.dispositionAt))}"` : ''}>${t.dispositionAt ? 'ประเมินเมื่อ ' + escapeHtml(formatRelativeTime(t.dispositionAt)) : 'ยังไม่ได้ประเมิน'}</span>
        <button type="button" class="btn-outline" data-action="assess-disposition" data-id="${escapeHtml(t.id)}" ${state.analyzeAssessing ? 'disabled' : ''}>
          ${state.analyzeAssessing ? 'กำลังตรวจ…' : (has ? 'ตรวจใหม่' : 'ประเมินประเภทงาน')}
        </button>
      </div>
      <div class="analyze-card-body">
        ${!has ? `
          <p class="analyze-disposition-empty">
            ยังไม่ได้ประเมิน — กด "ประเมินประเภทงาน" เพื่อให้ระบบตรวจสถานะพอร์ตและทดสอบการเข้าถึง
            แล้วเสนอว่าเป็นงานชนิดไหน
          </p>` : `
          <div class="analyze-disposition-head ${low ? 'is-low' : ''}">
            <span class="analyze-disposition-name">${escapeHtml(t.dispositionLabel || t.disposition)}</span>
            <span class="analyze-disposition-route">${escapeHtml(t.dispositionRoute || '')}</span>
          </div>
          ${low ? `<p class="analyze-low-confidence">หลักฐานไม่พอจะสรุป — ต้องให้คนตรวจสอบเพิ่มก่อนตัดสิน</p>` : ''}
          ${reasons.length ? `
            <div class="analyze-disposition-block">
              <span class="field-label">เพราะ</span>
              <ul class="analyze-disposition-list">
                ${reasons.map(r => `<li>${escapeHtml(r)}</li>`).join('')}
              </ul>
            </div>` : ''}
          ${ruledOut.length ? `
            <div class="analyze-disposition-block">
              <span class="field-label">ตัดออกเพราะ</span>
              <ul class="analyze-disposition-list analyze-disposition-ruled">
                ${ruledOut.map(r => `<li>${escapeHtml(r)}</li>`).join('')}
              </ul>
            </div>` : ''}
          ${t.dispositionEvidence ? `
            <div class="analyze-disposition-block">
              <span class="field-label">หลักฐานที่อ้าง</span>
              <pre class="analyze-raw-box">${escapeHtml(t.dispositionEvidence)}</pre>
            </div>` : ''}
          ${renderImpactBlock(t)}
          ${renderPwDraftBlock(t)}
          <p class="analyze-disposition-note">
            นี่เป็นข้อเสนอของระบบ ไม่ใช่การตัดสิน — ผู้ตรวจต้องยืนยันหรือแก้ไขที่แถบด้านล่างเสมอ
          </p>`}
      </div>
    </div>`;
}

function renderAnalyzeReviewBar(t) {
  const group = state.analyzeGroupDraft || t.tag || '';
  const alreadyReviewed = !!t.reviewedBy;
  // ตัวเลือกประเภทงานมาจาก backend เสมอ (ข้อห้าม #5 ห้ามเชื่อ frontend — ถ้าตั้งรายการเองที่นี่
  // วันหนึ่งกฎฝั่ง server เปลี่ยนแล้วหน้าจอจะเสนอค่าที่ระบบไม่รู้จัก)
  // 'undetermined' ไม่ให้คนเลือกเอง — เป็นคำตอบของระบบตอนหลักฐานไม่พอ ไม่ใช่การตัดสินของคน
  const options = (t.dispositionOptions || []).filter(o => o.key !== 'undetermined');
  const disposition = state.analyzeDispositionDraft
    || t.confirmedDisposition
    || (t.disposition !== 'undetermined' ? t.disposition : '')
    || '';
  return `
    <div class="analyze-review-bar">
      <div class="analyze-review-left">
        <span class="field-label">RESPONSIBLE TEAM</span>
        <div class="select-wrap">
          <select class="raw-select" data-action="pick-analyze-group" ${alreadyReviewed ? 'disabled' : ''}>
            ${GROUP_ORDER.map(tag => `<option value="${tag}" ${group === tag ? 'selected' : ''}>${escapeHtml(tag)}</option>`).join('')}
          </select>
          <span class="select-arrow">▾</span>
        </div>
        ${!state.analyzeGroupTouched ? `<span class="chip-suggested">System Suggested</span>` : ''}

        <span class="field-label analyze-review-sep">ประเภทงาน</span>
        <div class="select-wrap">
          <select class="raw-select" data-action="pick-analyze-disposition" ${alreadyReviewed ? 'disabled' : ''}>
            <option value="" ${disposition ? '' : 'selected'} disabled hidden>ยังไม่ได้เลือก…</option>
            ${options.map(o => `<option value="${escapeHtml(o.key)}" ${disposition === o.key ? 'selected' : ''}>${escapeHtml(o.label)}</option>`).join('')}
          </select>
          <span class="select-arrow">▾</span>
        </div>
        ${t.disposition && !state.analyzeDispositionTouched && !t.confirmedDisposition ? `<span class="chip-suggested">System Suggested</span>` : ''}
        ${t.confirmedDisposition ? `<span class="chip-confirmed">ยืนยันโดย ${escapeHtml(t.dispositionConfirmedBy || '-')}</span>` : ''}
      </div>
      <div class="analyze-review-right">
        ${alreadyReviewed
          ? `<span class="ack-done">${ICONS.checkCircle} Reviewed by ${escapeHtml(t.reviewedBy)}</span>`
          : `<button type="button" class="ack-btn" data-action="submit-review" data-id="${escapeHtml(t.id)}">${ICONS.checkCircle} Confirm</button>`}
      </div>
    </div>`;
}

function renderAnalyzeDetail() {
  const t = flatAllTickets().find(x => x.id === state.analyzeDetailId);
  if (!t) {
    return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb">
        <span class="breadcrumb-link" data-action="open-analyze-list">Analyze</span>
        <span>/</span>
        <span class="breadcrumb-current">Not found</span>
      </div>
    </div>
    <div class="empty-state">Ticket not found.</div>`;
  }

  return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb">
        ${t.tag ? `<span class="breadcrumb-link" data-action="open-group" data-tag="${escapeHtml(t.tag)}">${escapeHtml(t.tag)}</span><span>/</span>` : ''}
        <span class="breadcrumb-link" data-action="open-analyze-list">Analyze</span>
        <span>/</span>
        ${breadcrumbInProgressSegment(t)}
        <span class="breadcrumb-current">${escapeHtml(t.id)}</span>
      </div>
    </div>
    <div class="detail-cards analyze-cards-grid">
      ${renderAnalyzeQuickTriage(t)}
      <div style="display:flex;flex-direction:column;gap:16px">
        ${renderDeepAnalysisCard(t)}
        ${renderDispositionCard(t)}
        ${renderManualInvestigationCard(t)}
      </div>
    </div>
    ${renderAnalyzeReviewBar(t)}`;
}

/* ============================================================
   Render: Triage
   ============================================================ */

function renderTriage() {
  const triageCount = state.uploadedGroups ? (state.uploadedUnmatched || 0) : 0;
  const tickets = triageTickets();
  const { pageItems, page, totalPages } = paginate(tickets, state.triagePage);
  const selectedCount = Object.keys(state.selected).length;
  const columns = getColumnLayout('triage');
  return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb">
        <span class="breadcrumb-link" data-action="back-to-dashboard">Incident Triage Console</span>
        <span>/</span>
        <span class="breadcrumb-current">Not yet assigned</span>
      </div>
    </div>

    <div class="page-title" style="margin-bottom:4px">Not yet assigned to a group</div>
    <div style="font-size:12.5px;color:var(--text-muted);margin-bottom:18px">${triageCount} ticket — pick a group for each row, or select multiple to assign them together</div>

    ${selectedCount > 0 ? `
    <div class="bulk-bar">
      <span>${selectedCount} selected</span>
      <div class="select-wrap">
        <select class="raw-select" data-action="assign-bulk">
          <option value="">Assign to…</option>
          ${GROUP_ORDER.map(tag => `<option value="${tag}">${tag}</option>`).join('')}
        </select>
        <span class="select-arrow">▾</span>
      </div>
      <span class="bulk-bar-clear" data-action="clear-selection">Clear</span>
    </div>` : ''}

    ${priorityLegend(tickets)}
    <div class="table-card">
      <div class="table-scroll">
        <table class="table-resizable">
          ${renderColGroup([36], columns, [220])}
          <thead>
            <tr>
              <th style="width:36px"></th>
              ${columns.map(c => renderColumnHeader('triage', c)).join('')}
              <th>Assign to group</th>
            </tr>
          </thead>
          <tbody>
            ${pageItems.map(t => {
              const pr = ticketPriority(t);
              return `
              <tr data-priority="${pr}"${newTicketIds.has(t.id) ? ' class="row-new-ticket"' : ''}>
                <td><input type="checkbox" ${state.selected[t.id] ? 'checked' : ''} data-action="toggle-ticket" data-id="${escapeHtml(t.id)}" style="cursor:pointer"></td>
                ${columns.map(c => ticketColumnCell(c.key, t)).join('')}
                <td>${t.proposedTag ? `
                  <div class="proposal-cell">
                    <span class="proposal-tag">${escapeHtml(t.proposedTag)}</span>
                    <span class="proposal-by">proposed by ${escapeHtml(t.proposedBy || 'unknown')}</span>
                    ${state.isAdmin ? `
                      <div class="proposal-actions">
                        <button type="button" class="btn-outline" data-action="confirm-proposal" data-id="${escapeHtml(t.id)}">Confirm</button>
                        <button type="button" class="ack-btn" data-action="reject-proposal" data-id="${escapeHtml(t.id)}">Reject</button>
                      </div>` : `<span class="proposal-status">Awaiting admin confirmation</span>`}
                  </div>` : `
                  <div class="select-wrap">
                    <select class="raw-select" data-action="assign-triage" data-id="${escapeHtml(t.id)}">
                      <option value="">Select…</option>
                      ${GROUP_ORDER.map(tag => `<option value="${tag}">${tag}</option>`).join('')}
                    </select>
                    <span class="select-arrow">▾</span>
                  </div>`}
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
      ${tickets.length === 0 ? `<div class="empty-state">Everything has been assigned to a group</div>` : ''}
    </div>
    ${renderPagination(tickets.length, page, totalPages, 'triage-page-prev', 'triage-page-next')}`;
}

/* ============================================================
   Pagination — shared by the triage and group-detail tables, both
   of which can hold hundreds of rows with no paging today.
   ============================================================ */

function paginate(items, page) {
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const clamped = Math.min(Math.max(1, page || 1), totalPages);
  const start = (clamped - 1) * PAGE_SIZE;
  return { pageItems: items.slice(start, start + PAGE_SIZE), page: clamped, totalPages };
}

function renderPagination(totalItems, page, totalPages, prevAction, nextAction) {
  if (totalItems <= PAGE_SIZE) return '';
  return `
    <div class="pagination">
      <span class="pagination-info">${totalItems} tickets &middot; page ${page} of ${totalPages}</span>
      <div class="pagination-controls">
        <button type="button" class="btn-outline" data-action="${prevAction}" ${page <= 1 ? 'disabled' : ''}>&larr; Prev</button>
        <button type="button" class="btn-outline" data-action="${nextAction}" ${page >= totalPages ? 'disabled' : ''}>Next &rarr;</button>
      </div>
    </div>`;
}

// Small key shown above a ticket table so the row colours are
// self-explanatory (orange = past half the SLA, red = SLA breached).
// Only rendered when the table actually has at least one aging ticket.
function priorityLegend(tickets) {
  const hasWarning = tickets.some(t => ticketPriority(t) === 'warning');
  const hasCritical = tickets.some(t => ticketPriority(t) === 'critical');
  if (!hasWarning && !hasCritical) return '';
  return `
    <div class="priority-legend">
      ${hasWarning ? `<span class="priority-legend-item"><span class="priority-legend-dot" data-priority="warning"></span>Open &gt; ${SLA_WARNING_HOURS}h</span>` : ''}
      ${hasCritical ? `<span class="priority-legend-item"><span class="priority-legend-dot" data-priority="critical"></span>SLA breached (&gt; ${SLA_BREACH_HOURS}h)</span>` : ''}
    </div>`;
}

/* ============================================================
   Render: Group detail
   ============================================================ */

// Banner แจ้งเตือน ticket ที่ assign แล้วไม่ acknowledge เกิน 5 นาที
// dismissible — เมื่อ dismiss จะ add ticket id ลง dismissedOverdueBanners (module-level Set)
// แล้วเรียก render() ตรงๆ โดยไม่ผ่าน setState เพื่อไม่ให้ state เปลี่ยน
function renderOverdueBanner(tag) {
  const up = state.uploadedGroups;
  const list = up ? (up[tag] || []) : [];
  const overdueList = list.filter(t => t.overdueTier != null);
  if (!overdueList.length) return '';
  // กรองเฉพาะที่ user ยังไม่ได้ dismiss
  const undismissed = overdueList.filter(t => !dismissedOverdueBanners.has(t.id));
  if (!undismissed.length) return '';
  // หา ticket ที่ overdue นานสุด (minutesSinceAssigned สูงสุด)
  const worst = undismissed.reduce((a, b) => (b.minutesSinceAssigned || 0) > (a.minutesSinceAssigned || 0) ? b : a);
  const n = undismissed.length;
  return `
  <div class="overdue-banner">
    <span class="overdue-banner-count">${n}</span>
    ticket${n > 1 ? 's' : ''} assigned but not acknowledged for 5+ min
    — longest wait: <strong>${escapeHtml(String(worst.minutesSinceAssigned || 0))} min</strong> (${escapeHtml(worst.id)})
    <span class="triage-banner-link" data-action="overdue-filter-show" style="cursor:pointer">Show only →</span>
    <button type="button" class="banner-dismiss" data-action="dismiss-overdue-banner" data-tag="${escapeHtml(tag)}" aria-label="Dismiss overdue banner">✕</button>
  </div>`;
}

function renderDetail() {
  const detailGroup = GROUP_DATA[state.detailTag];
  const tickets = detailTickets();
  const { pageItems, page, totalPages } = paginate(tickets, state.detailPage);
  const columns = getColumnLayout('detail');

  return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb">
        <span class="breadcrumb-link" data-action="back-to-dashboard">Incident Triage Console</span>
        <span>/</span>
        <span class="breadcrumb-current">${detailGroup.tag}</span>
      </div>
    </div>

    <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px">
      <div class="group-tag" style="width:34px;height:34px;margin-bottom:0">${escapeHtml(detailGroup.owner)}</div>
      <div class="page-title">${escapeHtml(detailGroup.name)}</div>
      <button class="btn-outline" style="margin-left:auto" data-action="refresh">${ICONS.refresh} Refresh</button>
    </div>

    ${renderOverdueBanner(state.detailTag)}
    ${state.detailOverdueFilter ? `<div class="overdue-filter-bar">Showing overdue tickets only &mdash; <span class="triage-banner-link" data-action="overdue-filter-clear" style="cursor:pointer">Show all</span></div>` : ''}

    <div class="detail-toolbar">
      <div style="margin-left:auto">
        <div class="search-label">Search</div>
        <div class="search-box">
          ${ICONS.search}
          <input id="detail-search" placeholder="Circuit ID / keyword…" value="${escapeHtml(state.detailSearch)}" data-bind="detailSearch">
        </div>
      </div>
    </div>

    <div class="table-card">
      <div class="table-scroll">
        <table class="table-resizable">
          ${renderColGroup([36], columns, [140])}
          <thead>
            <tr>
              <th style="width:36px"></th>
              ${columns.map(c => renderColumnHeader('detail', c)).join('')}
              <th>Acknowledge</th>
            </tr>
          </thead>
          <tbody>
            ${pageItems.map(t => {
              const pr = ticketPriority(t);
              return `
              <tr data-action-context="analyze-row" data-dblclick-action="open-analyze-detail" data-id="${escapeHtml(t.id)}" data-priority="${pr}"${newTicketIds.has(t.id) ? ' class="row-new-ticket"' : ''}>
                <td><input type="checkbox" ${state.selected[t.id] ? 'checked' : ''} data-action="toggle-ticket" data-id="${escapeHtml(t.id)}" style="cursor:pointer"></td>
                ${columns.map(c => ticketColumnCell(c.key, t)).join('')}
                <td>
                  ${t.acknowledged
                    ? `<span class="ack-done">✓ ${escapeHtml(t.acknowledgedBy)}</span>`
                    : `<button type="button" class="ack-btn" data-action="acknowledge" data-id="${escapeHtml(t.id)}">Acknowledge</button>`}
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
      ${tickets.length === 0 ? `<div class="empty-state">ยังไม่มี TT ในกลุ่มนี้ — รอ syslog หรือกด "แจ้งเหตุด้วยตนเอง"</div>` : ''}
    </div>
    ${renderPagination(tickets.length, page, totalPages, 'detail-page-prev', 'detail-page-next')}`;
}

/* ============================================================
   Render: Admin (pending-approval queue + activity log)
   ============================================================ */

function renderAdminTabs() {
  const tab = state.adminTab || 'pending';
  return `
    <div class="sidebar-nav" style="margin-bottom:18px;display:flex;flex-direction:row;gap:6px;width:max-content">
      <span class="sidebar-nav-item${tab === 'pending' ? ' active' : ''}" data-action="admin-tab-pending">Pending approvals</span>
      <span class="sidebar-nav-item${tab === 'log' ? ' active' : ''}" data-action="admin-tab-log">Activity log</span>
    </div>`;
}

function renderAdminPending() {
  const pending = state.pendingUsers || [];
  return `
    <div class="page-title" style="margin-bottom:4px">Pending account approvals</div>
    <div style="font-size:12.5px;color:var(--text-muted);margin-bottom:18px">${pending.length} account${pending.length === 1 ? '' : 's'} waiting for review</div>

    <div class="table-card">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Username</th>
              <th>Name</th>
              <th>Team</th>
              <th>Requested</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${pending.map(u => `
              <tr>
                <td class="ticket-id">${escapeHtml(u.username)}</td>
                <td>${escapeHtml(u.firstName + ' ' + u.lastName)}</td>
                <td><span class="badge">${escapeHtml(u.team)}</span></td>
                <td>${escapeHtml(u.createdAt ? formatLocalDateTime(u.createdAt).slice(0, 10) : '')}</td>
                <td>
                  <div style="display:flex;gap:8px">
                    <button type="button" class="btn-outline" data-action="approve-user" data-id="${u.id}">Approve</button>
                    <button type="button" class="ack-btn" data-action="reject-user" data-id="${u.id}">Reject</button>
                  </div>
                </td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      ${pending.length === 0 ? `<div class="empty-state">No accounts waiting for approval</div>` : ''}
    </div>`;
}

function renderActivityLog() {
  const logs = state.activityLog || [];
  return `
    <div class="page-title" style="margin-bottom:4px">Activity log</div>
    <div style="font-size:12.5px;color:var(--text-muted);margin-bottom:18px">Most recent ${logs.length} action${logs.length === 1 ? '' : 's'}</div>

    <div class="table-card">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>User</th>
              <th>Action</th>
              <th title="What the action was done to — e.g. ticket #INC123 or user #6">Subject</th>
              <th title="Extra context for the action — e.g. which team a ticket moved from/to">Details</th>
            </tr>
          </thead>
          <tbody>
            ${logs.map(l => `
              <tr>
                <td>${escapeHtml(formatLocalDateTime(l.createdAt))}</td>
                <td>${escapeHtml(l.username || 'system')}</td>
                <td><span class="badge">${escapeHtml(l.action)}</span></td>
                <td class="ticket-id">${escapeHtml([l.subjectType, l.subjectId].filter(Boolean).join(' #'))}</td>
                <td style="font-size:12px;color:var(--text-muted)">${escapeHtml(l.details && Object.keys(l.details).length ? JSON.stringify(l.details) : '')}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      ${logs.length === 0 ? `<div class="empty-state">No activity recorded yet</div>` : ''}
    </div>`;
}

function renderAdmin() {
  const tab = state.adminTab || 'pending';
  return `
    <div class="page-header" style="margin-bottom:16px">
      ${expandSidebarButton()}
      <div class="breadcrumb">
        <span class="breadcrumb-link" data-action="back-to-dashboard">Incident Triage Console</span>
        <span>/</span>
        <span class="breadcrumb-current">Admin</span>
      </div>
    </div>

    ${renderAdminTabs()}
    ${tab === 'log' ? renderActivityLog() : renderAdminPending()}`;
}

/* ============================================================
   Top-level render
   ============================================================ */

const root = document.getElementById('app');

function renderBusyBar() {
  if (!state.busy) return '';
  return `<div class="busy-bar" aria-hidden="true"><div class="busy-bar-fill"></div></div>`;
}

function renderConfirmDialog() {
  const dlg = state.confirmDialog;
  if (!dlg) return '';
  return `
    <div class="modal-overlay">
      <div class="modal-card" role="alertdialog" aria-modal="true">
        <div class="modal-body">${escapeHtml(dlg.message)}</div>
        <div class="modal-actions">
          <button type="button" class="btn" data-action="confirm-no">Cancel</button>
          <button type="button" class="btn btn-primary" data-action="confirm-yes">${escapeHtml(dlg.confirmLabel || 'Confirm')}</button>
        </div>
      </div>
    </div>`;
}

function renderManualEntryModal() {
  if (!state.manualEntryOpen) return '';
  const groupOptions = GROUP_ORDER.map(tag =>
    `<option value="${tag}" ${state.manualGroup === tag ? 'selected' : ''}>${tag}</option>`
  ).join('');
  const priorityOptions = ['P1', 'P2', 'P3', 'P4'].map(p =>
    `<option value="${p}" ${state.manualPriority === p ? 'selected' : ''}>${p}</option>`
  ).join('');
  // Phase 5 ข้อ C-1: ใช้ ANALYZE_DEVICES ตัวเดียวกับ Manual Investigation ห้ามพิมพ์รายชื่อใหม่
  // จะได้มีที่เดียวให้แก้เวลา topology เปลี่ยน (ฝั่ง server validate ซ้ำอีกชั้นอยู่แล้ว)
  const nodeOptions = ANALYZE_DEVICES.map(d =>
    `<option value="${d}" ${state.manualNode === d ? 'selected' : ''}>${d}</option>`
  ).join('');
  // interface ขึ้นกับ node ที่เลือก — ยังไม่เลือก node ก็ยังเลือก interface ไม่ได้
  const ifaceList = NODE_INTERFACES[state.manualNode] || [];
  const ifaceLocked = ifaceList.length === 1;   // Site ต่อ Core เส้นเดียว = ไม่มีอะไรให้เลือก
  const ifaceOptions = ifaceList.map(i =>
    `<option value="${i}" ${state.manualInterface === i ? 'selected' : ''}>${i}</option>`
  ).join('');
  const ifacePlaceholder = state.manualNode ? 'ไม่ระบุ' : 'เลือกอุปกรณ์ก่อน';
  const ifaceHint = ifaceLocked
    ? `<div class="field-hint">${escapeHtml(state.manualNode)} ต่อกับ Core ด้วยสายเส้นเดียว ระบบเติมให้อัตโนมัติ</div>`
    : '';
  return `
    <div class="modal-overlay">
      <div class="modal-card" role="dialog" aria-modal="true" style="text-align:left;width:420px;max-width:90vw">
        <div class="page-title" style="margin-bottom:12px">แจ้งเหตุด้วยตนเอง</div>
        ${state.manualError ? `<div class="login-error" style="margin-bottom:10px">${escapeHtml(state.manualError)}</div>` : ''}
        <div>
          <div class="field">
            <label class="field-label">อุปกรณ์ (node)</label>
            <div class="select-wrap" style="width:100%">
              <select class="raw-select" style="width:100%" data-bind="manualNode">
                <option value="">เลือก…</option>
                ${nodeOptions}
              </select>
            </div>
          </div>
          <div class="field">
            <label class="field-label">Interface (ถ้ามี)</label>
            <div class="select-wrap" style="width:100%">
              <select class="raw-select" style="width:100%" data-bind="manualInterface" ${ifaceList.length && !ifaceLocked ? '' : 'disabled'}>
                ${ifaceLocked ? '' : `<option value="">${ifacePlaceholder}</option>`}
                ${ifaceOptions}
              </select>
            </div>
            ${ifaceHint}
          </div>
          <div class="field">
            <label class="field-label">คำอธิบายปัญหา</label>
            <textarea class="text-input" data-bind="manualDescription" placeholder="ลูกค้าโทรแจ้งว่า...">${escapeHtml(state.manualDescription)}</textarea>
          </div>
          <div class="field">
            <label class="field-label">Owner Group</label>
            <div class="select-wrap" style="width:100%">
              <select class="raw-select" style="width:100%" data-bind="manualGroup">
                <option value="">เลือก…</option>
                ${groupOptions}
              </select>
            </div>
          </div>
          <div class="field">
            <label class="field-label">Priority</label>
            <div class="select-wrap" style="width:100%">
              <select class="raw-select" style="width:100%" data-bind="manualPriority">
                <option value="">เลือก…</option>
                ${priorityOptions}
              </select>
            </div>
          </div>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn" data-action="close-manual-entry">Cancel</button>
          <button type="button" class="btn btn-primary" data-action="submit-manual-entry">สร้าง TT</button>
        </div>
      </div>
    </div>`;
}

function renderView() {
  if (state.view === 'login') return renderLogin();
  if (state.view === 'register') return renderRegister();
  const body = state.view === 'triage' ? renderTriage()
    : state.view === 'detail' ? renderDetail()
    : state.view === 'analyze' ? renderAnalyze()
    : state.view === 'analyze-detail' ? renderAnalyzeDetail()
    : state.view === 'inprogress' ? renderInProgress()
    : state.view === 'inprogress-detail' ? renderInProgressDetail()
    : state.view === 'admin' ? renderAdmin()
    : renderDashboard();
  return `
    <div class="app-shell">
      ${renderSidebar()}
      <div class="main"><div class="main-inner">
        ${renderErrorBanner()}
        ${body}
      </div></div>
    </div>`;
}

function renderRoot() {
  return renderBusyBar() + renderView() + renderConfirmDialog() + renderManualEntryModal();
}

function render() {
  const active = document.activeElement;
  // Keyed off data-bind (not id) - every data-bound field already carries
  // one uniquely (the global 'input' listener depends on it to know which
  // state key to update), whereas only a handful of fields happened to also
  // have an id. Fields with only an id and no data-bind (rare) still fall
  // back to that. Without this, any field missing an id lost focus after
  // every single keystroke - the full innerHTML replace below destroys and
  // recreates the DOM node, so a re-render mid-typing looked like "only one
  // character goes in before you have to click back into the box".
  const activeBind = active && active.dataset && active.dataset.bind;
  const activeId = active && active.id;
  const hasSelection = active && typeof active.selectionStart === 'number';
  const selStart = hasSelection ? active.selectionStart : null;
  const selEnd = hasSelection ? active.selectionEnd : null;

  root.innerHTML = renderRoot();

  // อัพเดต browser tab title: "(N) Incident Triage Console" เมื่อมี N overdue ticket รวมทุกกลุ่ม
  const _totalOverdue = countTotalOverdue();
  document.title = _totalOverdue > 0 ? '(' + _totalOverdue + ') Incident Triage Console' : 'Incident Triage Console';

  const el = activeBind
    ? root.querySelector('[data-bind="' + activeBind + '"]')
    : (activeId ? document.getElementById(activeId) : null);
  if (el) {
    el.focus();
    if (selStart !== null && el.setSelectionRange) {
      try { el.setSelectionRange(selStart, selEnd); } catch (e) { /* not a text field */ }
    }
  }

  layoutSteppers();
}

window.addEventListener('resize', layoutSteppers);

// TEMP DEV BYPASS — กด Enter จากตรงไหนก็ได้บนหน้า login เข้าระบบได้เลย โดยไม่ต้องคลิก/focus
// ช่องไหนก่อน (ปกติ browser submit form ให้เองก็ต่อเมื่อมี input ใน form นั้น focus อยู่)
// คู่กับ username/password ที่ prefill ไว้ด้านบน — ลบ block นี้ทิ้งพร้อมกันตอนคืนค่า login จริง
window.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  if (state.view !== 'login') return;
  e.preventDefault();
  runBusy(handleLoginSubmit);
});

/* ============================================================
   Event delegation
   ============================================================ */

root.addEventListener('submit', (e) => {
  if (e.target.closest('#login-form')) { e.preventDefault(); runBusy(handleLoginSubmit); }
  if (e.target.closest('#register-form')) { e.preventDefault(); runBusy(handleRegisterSubmit); }
});

root.addEventListener('input', (e) => {
  const field = e.target.dataset.bind;
  if (!field) return;
  const patch = { [field]: e.target.value };
  if (field === 'detailSearch') patch.detailPage = 1;
  // เปลี่ยนอุปกรณ์แล้ว interface เดิมอาจไม่มีอยู่บนเครื่องใหม่ — ล้างทิ้งกันค่าค้าง
  // (ถ้าไม่ล้าง ตัว select จะโชว์ "ไม่ระบุ" แต่ state ยังถือค่าเก่าอยู่ แล้วส่งค่าผิดไป server)
  // Site แต่ละแห่งต่อกับ Core ด้วยสายเส้นเดียว จึงมี interface ให้เลือกแค่ตัวเดียว —
  // เติมให้เลยแล้วล็อกไว้ ไม่ต้องให้คนเลือกซ้ำในสิ่งที่มีคำตอบเดียวอยู่แล้ว
  // ส่วน Core ต่อ 3 Site จึงต้องเลือกเองว่าเส้นไหน
  if (field === 'manualNode') {
    const list = NODE_INTERFACES[e.target.value] || [];
    patch.manualInterface = list.length === 1 ? list[0] : '';
  }
  if (e.target.dataset.clearError === 'reg') patch.regError = '';
  else if (e.target.dataset.clearError) patch.loginError = '';
  setState(patch);
});

root.addEventListener('change', (e) => {
  const action = e.target.dataset.action;
  if (action === 'assign-triage') {
    const tag = e.target.value;
    const id = e.target.dataset.id;
    if (!tag) return;
    if (state.isAdmin) {
      askConfirm('Assign ticket ' + id + ' to ' + tag + '?', () => assignFromTriage(id, tag), 'Assign');
    } else {
      askConfirm('Propose ticket ' + id + ' for ' + tag + '? An admin will need to confirm before it moves.', () => assignFromTriage(id, tag), 'Propose');
    }
  } else if (action === 'assign-bulk') {
    const tag = e.target.value;
    const ids = Object.keys(state.selected);
    if (!tag || !ids.length) return;
    if (state.isAdmin) {
      askConfirm('Assign ' + ids.length + ' tickets to ' + tag + '?', () => assignMultiple(ids, tag), 'Assign');
    } else {
      askConfirm('Propose ' + ids.length + ' tickets for ' + tag + '? An admin will need to confirm before they move.', () => assignMultiple(ids, tag), 'Propose');
    }
  } else if (action === 'toggle-ticket') {
    toggleSelectTicket(e.target.dataset.id);
  } else if (action === 'pick-handover-reason') {
    const val = e.target.value;
    if (val === '__other__') setState({ handoverReasonOther: true, handoverReason: '' });
    else setState({ handoverReasonOther: false, handoverReason: val });
  } else if (action === 'pick-attach-file') {
    setState({ detailAttachedFileName: (e.target.files && e.target.files[0] && e.target.files[0].name) || '' });
  } else if (action === 'pick-attach-image') {
    const file = e.target.files && e.target.files[0];
    if (state.detailAttachedImagePreviewUrl) {
      try { URL.revokeObjectURL(state.detailAttachedImagePreviewUrl); } catch (err) { /* ignore */ }
    }
    setState({
      detailAttachedImageName: (file && file.name) || '',
      detailAttachedImagePreviewUrl: file ? URL.createObjectURL(file) : '',
    });
  } else if (action === 'pick-analyze-device') {
    setState({ analyzeManualDevice: e.target.value, analyzeManualResult: null });
  } else if (action === 'pick-analyze-command') {
    setState({ analyzeManualCommand: e.target.value, analyzeManualResult: null });
  } else if (action === 'pick-analyze-group') {
    setState({ analyzeGroupDraft: e.target.value, analyzeGroupTouched: true });
  } else if (action === 'pick-analyze-disposition') {
    setState({ analyzeDispositionDraft: e.target.value, analyzeDispositionTouched: true });
  } else if (action === 'edit-impact') {
    // เก็บลง state ทุกครั้งที่พิมพ์ ไม่งั้น render รอบถัดไป (polling ทุก 5 วิ) จะล้างค่าที่พิมพ์ไว้
    const t = flatAllTickets().find(x => x.id === state.analyzeDetailId);
    const base = state.analyzeImpactDraft
      || (t && (t.impactValues || t.impactSuggested)) || {};
    const next = Object.assign({}, base);
    next[e.target.dataset.key] = Math.max(0, parseInt(e.target.value, 10) || 0);
    setState({ analyzeImpactDraft: next });
  }
});

// จำสถานะเปิด/พับของ <details> COMMAND OUTPUT ไว้ใน state.analyzeOpenSections ทุกครั้งที่ user
// คลิก summary — ต้องดัก 'toggle' ตอน capture phase เพราะ event นี้ไม่ bubble ขึ้นมาถึง root
// เฉยๆ (ต่างจาก click/change/input) ถ้าไม่ทำ renderCommandOutputSections รอบถัดไป (polling ทุก
// 5 วิ) จะไม่รู้ว่า user กาง/พับอันไหนไว้ แล้วรีเซ็ตกลับ default เอง (อาการ "เปิดปิดเอง" ที่เจอ)
// ตั้งค่าใน state ตรงๆ ไม่ผ่าน setState() เพราะ DOM ก็สะท้อนค่าใหม่ถูกต้องอยู่แล้วจาก native
// toggle ของ browser — เรียก render() (full innerHTML replace) ซ้ำตรงนี้จะรื้อ element ที่ผู้ใช้
// เพิ่งกดทิ้งโดยไม่จำเป็น
root.addEventListener('toggle', (e) => {
  const el = e.target.closest && e.target.closest('.analyze-command-section');
  if (!el) return;
  const command = el.dataset.command;
  if (!command) return;
  state.analyzeOpenSections[command] = el.open;
}, true);

root.addEventListener('contextmenu', (e) => {
  const row = e.target.closest('[data-action-context="analyze-row"]');
  if (!row) return;
  e.preventDefault();
  openAnalyzeDetail(row.dataset.id);
});

root.addEventListener('dblclick', (e) => {
  const el = e.target.closest('[data-dblclick-action]');
  if (!el) return;
  if (el.dataset.dblclickAction === 'open-inprogress-detail') {
    // แถวในตารางนี้ (In Progress list) เป็น ticket ที่ acknowledge แล้วเสมอ (inProgressTickets()
    // กรอง status === 'In Progress' อยู่แล้ว) ไม่ต้อง acknowledge ซ้ำอีก — ต่างจากตาราง Detail
    // ต่อทีม ที่ตอนนี้ดับเบิลคลิกต้องเปิดหน้า Analyze แทน (ดู 'open-analyze-detail' ด้านล่าง)
    openInProgressDetail(el.dataset.id);
  } else if (el.dataset.dblclickAction === 'open-analyze-detail') {
    openAnalyzeDetail(el.dataset.id);
  }
});

root.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  switch (action) {
    case 'toggle-visibility': setState({ isVisible: !state.isVisible }); break;
    case 'login-submit': runBusy(handleLoginSubmit); break;
    case 'show-register': setState({ view: 'register', regError: '', regSuccess: '' }); break;
    case 'show-login': setState({ view: 'login', regError: '' }); break;
    case 'toggle-sidebar': toggleSidebar(); break;
    case 'toggle-theme': toggleTheme(); break;
    case 'toggle-notifications': toggleDesktopNotifications(); break;
    case 'logout':
      stopPolling(); // หยุด polling ก่อน logout
      dismissedOverdueBanners.clear(); // เคลียร์ dismiss state
      knownOverdueIds = null; // เคลียร์ baseline notification กัน login รอบใหม่โดนของ session เก่า
      apiFetch('/api/logout', { method: 'POST' }).catch(() => {}); // best-effort
      authToken = null;
      setState({
        // username/password กลับไปเป็น dev bypass เดิม (ดูคอมเมนต์ตรง state.username ด้านบน)
        view: 'login', username: 'admin', password: 'admin123',
        firstName: '', lastName: '', team: null, isAdmin: false,
        uploadedGroups: null, uploadedUnassigned: [], uploadInfo: '', pendingUsers: [],
        adminTab: 'pending', activityLog: [], detailPage: 1, triagePage: 1, confirmDialog: null,
        selected: {}, detailOverdueFilter: false,
      });
      break;
    case 'refresh':
      runBusy(() => loadTickets().catch(err => setState({ errorMessage: err.message })));
      break;
    case 'open-manual-entry': openManualEntry(); break;
    case 'close-manual-entry': closeManualEntry(); break;
    case 'submit-manual-entry': runBusy(submitManualEntry); break;
    case 'open-triage': setState({ view: 'triage', triagePage: 1, selected: {} }); break;
    case 'open-group': setState({ view: 'detail', detailTag: el.dataset.tag, detailPage: 1, selected: {}, detailOverdueFilter: false }); break;
    case 'open-admin':
      setState({ view: 'admin', adminTab: 'pending' });
      runBusy(() => loadPendingUsers().catch(err => setState({ errorMessage: err.message })));
      break;
    case 'admin-tab-pending':
      setState({ adminTab: 'pending' });
      runBusy(() => loadPendingUsers().catch(err => setState({ errorMessage: err.message })));
      break;
    case 'admin-tab-log':
      setState({ adminTab: 'log' });
      runBusy(() => loadActivityLog().catch(err => setState({ errorMessage: err.message })));
      break;
    case 'approve-user': runBusy(() => approveUser(el.dataset.id)); break;
    case 'reject-user': runBusy(() => rejectUser(el.dataset.id)); break;
    case 'confirm-proposal': runBusy(() => confirmProposal(el.dataset.id)); break;
    case 'reject-proposal': runBusy(() => rejectProposal(el.dataset.id)); break;
    case 'back-to-dashboard': setState({ view: 'dashboard', selected: {} }); break;
    case 'acknowledge': acknowledgeFromRow(el.dataset.id); break;
    case 'open-analyze-list': setState({ view: 'analyze', analyzePage: 1 }); break;
    case 'open-analyze': openAnalyzeDetail(el.dataset.id); break;
    case 'open-inprogress-from-stage': openInProgressDetail(el.dataset.id); break;
    case 'open-inprogress': setState({ view: 'inprogress', selected: {} }); break;
    case 'back-to-inprogress-list': setState({ view: 'inprogress', inProgressDetailId: null, handoverFormFor: null }); break;
    case 'view-substatus-tab':
      setState({
        viewingStep: el.dataset.step, inProgressDetailEditing: false,
        ...resetAttachmentState(),
      });
      break;
    case 'edit-inprogress-step': {
      const t = inProgressTickets().find(x => x.id === state.inProgressDetailId);
      // Load whatever was already saved for this step (if any) so Edit
      // opens on the existing note/event time to be amended - not a blank
      // box that would silently overwrite it into nothing on the next Save.
      const existing = t ? latestTimelineEntryFor(t.subStatusTimeline, state.viewingStep) : null;
      // Existing value must already be a real timestamp to reuse - older
      // entries saved back when this was a free-text field (e.g. "02:30 PM")
      // don't parse, so fall back to "now" the same as a fresh entry would.
      const existingEventMs = existing && existing.eventTime ? Date.parse(existing.eventTime) : NaN;
      const eventTimeDefault = isFinite(existingEventMs)
        ? toDatetimeLocalValue(new Date(existingEventMs))
        : toDatetimeLocalValue(roundToStepMinutes(new Date(), EVENT_TIME_STEP_MINUTES));
      // ชื่อ/เบอร์ช่างต้องดึงมาจาก step ก่อนหน้าด้วย (carry-forward) ไม่ใช่แค่ของ step นี้เอง
      const techInfo = t ? latestTechnicianInfo(t.subStatusTimeline) : { name: null, contact: null };
      setState({
        inProgressDetailEditing: true,
        detailNoteDraft: (existing && existing.note) || '',
        detailEventTimeDraft: eventTimeDefault,
        detailTechnicianName: techInfo.name || '',
        detailTechnicianContact: techInfo.contact || '',
      });
      break;
    }
    case 'cancel-inprogress-edit': setState({ inProgressDetailEditing: false }); break;
    case 'save-inprogress-details': runBusy(() => saveInProgressStepDetails(el.dataset.id)); break;
    // Confirm is the everyday action - no dialog, just advances one step.
    case 'advance-inprogress-stage': {
      const stepKey = el.dataset.step;
      if (!stepKey) break; // disabled (already at "Resolved") - shouldn't fire, but guard anyway
      runBusy(() => setTicketStage(el.dataset.id, stepKey, { resetCustomerUpdate: true }));
      break;
    }
    // Back is explicitly the "I clicked wrong" undo - gated behind a dialog.
    case 'revert-inprogress-stage': {
      const id = el.dataset.id;
      const stepKey = el.dataset.step;
      if (!stepKey) break; // disabled (already at "En Route", nothing before it)
      const step = SUB_STATUS_STEPS.find(s => s.key === stepKey);
      // Not wrapped in runBusy() here - the 'confirm-yes' case already does
      // that around whatever onConfirm callback it invokes; wrapping again
      // here would make the inner runBusy() see state.busy already true and
      // silently no-op (exactly what happened before this comment existed).
      askConfirm(
        'Move this ticket back to "' + step.label + '"? Use this if Confirm was clicked by mistake.',
        () => setTicketStage(id, stepKey),
        'Move Back'
      );
      break;
    }
    case 'open-handover-form':
      setState({ handoverFormFor: el.dataset.id, handoverToUserId: '', handoverReason: '', handoverReasonOther: false, handoverCandidates: [] });
      runBusy(() => loadHandoverCandidates(el.dataset.id));
      break;
    case 'cancel-handover-form': setState({ handoverFormFor: null }); break;
    case 'submit-handover':
      runBusy(() => requestHandover(el.dataset.id, state.handoverToUserId, state.handoverReason));
      break;
    case 'confirm-handover': runBusy(() => confirmHandover(el.dataset.id)); break;
    case 'reject-handover': runBusy(() => rejectHandover(el.dataset.id)); break;
    case 'log-customer-update': runBusy(() => logCustomerUpdate(el.dataset.id)); break;
    case 'recheck-diagnosis': runBusy(() => recheckDiagnosis(el.dataset.id)); break;
    case 'run-manual-command': runBusy(() => runManualCommand(el.dataset.id)); break;
    case 'assess-disposition': runBusy(() => assessDisposition(el.dataset.id)); break;
    case 'download-pw-draft': runBusy(() => downloadPwDraft(el.dataset.id)); break;
    case 'draft-pw': runBusy(() => draftPw(el.dataset.id)); break;
    case 'save-impact': runBusy(() => saveImpact(el.dataset.id)); break;
    case 'reset-impact': setState({ analyzeImpactDraft: null }); break;
    case 'toggle-view-all-output': setState({ analyzeShowAllOutput: !state.analyzeShowAllOutput }); break;
    case 'copy-manual-output': {
      const result = state.analyzeManualResult;
      if (result && result.output && navigator.clipboard) navigator.clipboard.writeText(result.output).catch(() => {});
      break;
    }
    case 'copy-raw-logs': {
      const ticket = flatAllTickets().find(x => x.id === el.dataset.id);
      if (ticket) copyText(ticket.rawSyslogDisplay || ticket.rawSyslog || '');
      break;
    }
    case 'copy-reference': {
      const ticket = flatAllTickets().find(x => x.id === el.dataset.id);
      if (ticket) copyText(ticket.evidenceLines || '');
      break;
    }
    case 'copy-command-output': {
      // ปุ่มนี้อยู่ใน <summary> — ไม่ preventDefault ตรงนี้ จะโดน toggle เปิด/ปิดตามไปด้วย
      // ทุกครั้งที่กด Copy ทั้งที่แค่อยากคัดลอก ไม่ได้ตั้งใจกาง/พับ section
      e.preventDefault();
      const run = state.analyzeDiagnosisRuns.find(r => r.command === el.dataset.command);
      if (run) {
        const isLogging = run.command.trim() === 'show logging';
        copyText(isLogging ? filterLogOutput(run.output).text : (run.output || ''));
      }
      break;
    }
    case 'download-manual-output': {
      const result = state.analyzeManualResult;
      if (result && result.output) {
        const blob = new Blob([result.output], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = el.dataset.id + '-' + (result.device || 'output') + '.txt';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }
      break;
    }
    // ปุ่มชื่อ "Confirm" อยู่แล้ว กดครั้งเดียวจบ ไม่ต้องมี modal ถามซ้ำ (ตัดออกตามคำขอ
    // 2026-08-07 — เดิมมี askConfirm คั่นไว้ รอบนี้ user อยากให้กดแล้วไปเลย)
    case 'submit-review': runBusy(() => submitReview(el.dataset.id)); break;
    case 'dismiss-error': setState({ errorMessage: '' }); break;
    case 'confirm-yes': {
      const dlg = state.confirmDialog;
      setState({ confirmDialog: null });
      if (dlg && dlg.onConfirm) runBusy(dlg.onConfirm);
      break;
    }
    case 'confirm-no': setState({ confirmDialog: null }); break;
    case 'detail-page-prev': setState({ detailPage: Math.max(1, (state.detailPage || 1) - 1) }); break;
    case 'detail-page-next': setState({ detailPage: (state.detailPage || 1) + 1 }); break;
    case 'triage-page-prev': setState({ triagePage: Math.max(1, (state.triagePage || 1) - 1) }); break;
    case 'triage-page-next': setState({ triagePage: (state.triagePage || 1) + 1 }); break;
    case 'clear-selection': setState({ selected: {} }); break;
    case 'dismiss-overdue-banner': {
      // เพิ่ม id ของ overdue ticket ทุกตัวในกลุ่มนั้นเข้า dismissed set
      // แล้วเรียก render() ตรงๆ (ไม่ผ่าน setState เพื่อไม่ให้ state เปลี่ยน)
      const _bannerTag = el.dataset.tag;
      const _bannerList = (state.uploadedGroups && state.uploadedGroups[_bannerTag]) || [];
      _bannerList.filter(t => t.overdueTier != null).forEach(t => dismissedOverdueBanners.add(t.id));
      render();
      break;
    }
    case 'overdue-filter-show':
      // กรองตารางให้แสดงเฉพาะ ticket overdue
      setState({ detailOverdueFilter: true, detailPage: 1 });
      break;
    case 'overdue-filter-clear':
      // คืนค่าตารางให้แสดง ticket ทั้งหมด
      setState({ detailOverdueFilter: false, detailPage: 1 });
      break;
    default: break;
  }
});

render();
