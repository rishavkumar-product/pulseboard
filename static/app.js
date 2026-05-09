// PulseBoard client-side JS

// ── Refresh polling ────────────────────────────────────────────────
let _pollTimer = null;

function startRefreshPoll(pubId) {
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(() => pollRefreshStatus(pubId), 3000);
}

async function pollRefreshStatus(pubId) {
  const res = await fetch(`/api/publications/${pubId}/refresh/status`);
  const data = await res.json();
  updateRefreshUI(data);
  if (data.refresh_status !== 'running') {
    clearInterval(_pollTimer);
    _pollTimer = null;
    if (data.refresh_status === 'success') {
      const iframe = document.getElementById('pub-iframe');
      if (iframe) {
        const base = iframe.src.split('?')[0];
        iframe.src = base + '?t=' + Date.now(); // cache-bust
      }
    }
  }
}

function updateRefreshUI(data) {
  const badge = document.getElementById('refresh-status-badge');
  const log = document.getElementById('refresh-log');
  const btn = document.getElementById('refresh-btn');
  if (badge) {
    badge.className = `status-${data.refresh_status} fw-semibold`;
    badge.textContent = data.refresh_status.charAt(0).toUpperCase() + data.refresh_status.slice(1);
  }
  if (log && data.refresh_log) log.textContent = data.refresh_log;
  if (btn) btn.disabled = data.refresh_status === 'running';
}

async function triggerRefresh(pubId) {
  const btn = document.getElementById('refresh-btn');
  if (btn) btn.disabled = true;
  const res = await fetch(`/api/publications/${pubId}/refresh`, { method: 'POST' });
  const data = await res.json();
  updateRefreshUI(data);
  if (data.refresh_status === 'running') startRefreshPoll(pubId);
}

// ── Comment submit (no full reload) ───────────────────────────────
async function submitComment(pubId, parentId = null) {
  const formId = parentId ? `reply-form-${parentId}` : 'comment-form';
  const form = document.getElementById(formId);
  if (!form) return;
  const body = form.querySelector('textarea').value.trim();
  if (!body) return;
  const url = parentId
    ? `/api/publications/${pubId}/comments/${parentId}/reply`
    : `/api/publications/${pubId}/comments`;
  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body })
  });
  window.location.reload();
}

// ── Toggle reply box ───────────────────────────────────────────────
function toggleReply(commentId) {
  const box = document.getElementById(`reply-form-${commentId}`);
  if (box) box.classList.toggle('d-none');
}

// ── Member add ─────────────────────────────────────────────────────
async function addMember(forumId) {
  const usernameEl = document.getElementById('add-member-username');
  const roleEl = document.getElementById('add-member-role');
  if (!usernameEl) return;
  const username = usernameEl.value.trim();
  const role = roleEl ? roleEl.value : 'member';
  if (!username) return;
  const res = await fetch(`/api/forums/${forumId}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, role })
  });
  const data = await res.json();
  if (res.ok) window.location.reload();
  else alert(data.detail || 'Error adding member');
}

async function removeMember(forumId, userId) {
  if (!confirm('Remove this member?')) return;
  await fetch(`/api/forums/${forumId}/members/${userId}`, { method: 'DELETE' });
  window.location.reload();
}

// ── Topic create ───────────────────────────────────────────────────
async function createTopic(forumId) {
  const name = document.getElementById('topic-name').value.trim();
  const desc = document.getElementById('topic-desc').value.trim();
  if (!name) return;
  const res = await fetch(`/api/forums/${forumId}/topics`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: name, description: desc })
  });
  const data = await res.json();
  if (res.ok) window.location.href = `/forums/${data.forum_slug}/${data.slug}`;
  else alert(data.detail || 'Error creating topic');
}

// ── Delete confirm helpers ─────────────────────────────────────────
async function deletePub(pubId, forumSlug, topicSlug) {
  if (!confirm('Delete this publication? This cannot be undone.')) return;
  await fetch(`/api/publications/${pubId}`, { method: 'DELETE' });
  window.location.href = `/forums/${forumSlug}/${topicSlug}`;
}

async function deleteComment(commentId) {
  if (!confirm('Delete this comment?')) return;
  await fetch(`/api/comments/${commentId}`, { method: 'DELETE' });
  window.location.reload();
}

// ── Schedule management ────────────────────────────────────────────
async function saveSchedule(pubId) {
  const cron = document.getElementById('cron-input').value.trim();
  if (!cron) return;
  const res = await fetch(`/api/publications/${pubId}/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cron_expression: cron })
  });
  const data = await res.json();
  if (res.ok) window.location.reload();
  else alert(data.detail || 'Invalid cron expression');
}

async function toggleSchedule(pubId, activate) {
  await fetch(`/api/publications/${pubId}/schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: activate })
  });
  window.location.reload();
}

async function deleteSchedule(pubId) {
  if (!confirm('Remove this schedule?')) return;
  await fetch(`/api/publications/${pubId}/schedule`, { method: 'DELETE' });
  window.location.reload();
}

// ── Client-side pub search ─────────────────────────────────────────
function filterPubs(query) {
  const cards = document.querySelectorAll('.pub-card-wrapper');
  const q = query.toLowerCase();
  cards.forEach(c => {
    const title = c.dataset.title || '';
    c.style.display = title.toLowerCase().includes(q) ? '' : 'none';
  });
}
