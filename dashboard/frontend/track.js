'use strict';

const TRACK_API_BASE = 'http://127.0.0.1:8000';
const POLL_MS = 60000; // well under the 1-hour requirement; cheap since the endpoint is a single indexed lookup

// Standalone copy of the icon set/step order - this page intentionally
// shares no <script> with script.js so it can never accidentally load the
// authenticated app's Bearer-token API client.
const TRACK_ICONS = {
  fileText: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>',
  userCheck: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="8.5" cy="7" r="4"></circle><polyline points="17 11 19 13 23 9"></polyline></svg>',
  truck: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="3" width="15" height="13"></rect><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon><circle cx="5.5" cy="18.5" r="2.5"></circle><circle cx="18.5" cy="18.5" r="2.5"></circle></svg>',
  mapPin: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>',
  tool: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>',
  checkCircle: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>',
};

const TRACK_STEPS = [
  { key: 'received', label: 'Ticket Received', icon: 'fileText' },
  { key: 'assigned', label: 'Technician Assigned', icon: 'userCheck' },
  { key: 'en_route', label: 'En Route', icon: 'truck' },
  { key: 'on_site', label: 'On-Site', icon: 'mapPin' },
  { key: 'working', label: 'Resolving', icon: 'tool' },
  { key: 'completed', label: 'Resolved', icon: 'checkCircle' },
];
const MANUAL_STEP_ORDER = ['en_route', 'on_site', 'working', 'completed'];

function escapeHtml(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function getToken() {
  return new URLSearchParams(window.location.search).get('token') || '';
}

function stepDone(ticket, step) {
  if (step.key === 'received') return !!ticket.ticketReceivedAt;
  if (step.key === 'assigned') return !!ticket.technicianAssigned;
  const currentIndex = MANUAL_STEP_ORDER.indexOf(ticket.currentSubStatus);
  return currentIndex >= 0 && MANUAL_STEP_ORDER.indexOf(step.key) <= currentIndex;
}

function renderTimeline(ticket) {
  const el = document.getElementById('track-app');
  if (!ticket) {
    el.innerHTML = '<p>We could not find this tracking link — it may be incorrect or expired.</p>';
    return;
  }

  el.innerHTML = `
    <div class="page-title">Tracking ticket ${escapeHtml(ticket.id)}</div>
    <p>${escapeHtml(ticket.description)}</p>
    <div class="sub-status-timeline">
      ${TRACK_STEPS.map(step => `<div class="timeline-step${stepDone(ticket, step) ? ' done' : ''}">${TRACK_ICONS[step.icon]}<span>${escapeHtml(step.label)}</span></div>`).join('')}
    </div>
    <p style="font-size:12px;color:var(--text-muted)">Last updated: ${new Date().toLocaleTimeString('en-US')}</p>`;
}

async function pollOnce() {
  const token = getToken();
  if (!token) {
    document.getElementById('track-app').innerHTML = '<p>This link is invalid.</p>';
    return;
  }
  try {
    const response = await fetch(TRACK_API_BASE + '/api/track/' + encodeURIComponent(token));
    const ticket = response.ok ? await response.json() : null;
    renderTimeline(ticket);
  } catch (err) {
    document.getElementById('track-app').innerHTML = '<p>Could not connect — retrying…</p>';
  }
}

pollOnce();
setInterval(pollOnce, POLL_MS);
