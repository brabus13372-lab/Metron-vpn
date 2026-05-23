// ============================================
// METRON WEBAPP — UI / DOM rendering
// ============================================

import { haptic } from './telegram.js';

export function renderUser(user, data) {
  // Avatar + имя
  const name = data?.name ?? user?.first_name ?? 'Пользователь';
  const avatarEl = document.getElementById('avatar');
  const usernameEl = document.getElementById('username');
  if (avatarEl) avatarEl.textContent = name[0].toUpperCase();
  if (usernameEl) usernameEl.textContent = name;
}

export function renderSubscription(data) {
  const { days_left, days_total, subscription_end, plan } = data;

  // Badge статуса
  const badgeEl = document.getElementById('sub-badge');
  if (badgeEl) {
    const isActive = days_left > 0;
    badgeEl.innerHTML = `
      <span class="badge ${isActive ? 'badge-success' : 'badge-error'}">
        <span class="status-dot ${isActive ? 'active' : 'expired'}"></span>
        ${isActive ? 'Активна' : 'Истекла'}
      </span>`;
  }

  // Дни
  const daysEl = document.getElementById('days-left');
  if (daysEl) daysEl.textContent = days_left;

  // Дата окончания
  const endEl = document.getElementById('sub-end');
  if (endEl) {
    const date = new Date(subscription_end);
    endEl.textContent = date.toLocaleDateString('ru-RU', {
      day: 'numeric', month: 'long', year: 'numeric'
    });
  }

  // Тариф
  const planEl = document.getElementById('sub-plan');
  if (planEl) planEl.textContent = plan ?? '—';

  // Прогресс-бар
  const fill = document.getElementById('progress-fill');
  if (fill) {
    const pct = Math.min(100, Math.round((days_left / days_total) * 100));
    fill.style.setProperty('--progress-value', `${pct}%`);
    fill.style.width = `${pct}%`;
    if (days_left <= 5) fill.classList.add('warning');
  }
}

export function renderVlessKey(key) {
  const el = document.getElementById('vless-key');
  if (el) el.textContent = key;
}

export function showToast(message, duration = 2000) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), duration);
}

// Глобальная функция копирования (вызывается из onclick в HTML)
window.copyKey = async function () {
  const key = document.getElementById('vless-key')?.textContent;
  if (!key || key.includes('загрузка')) return;

  try {
    await navigator.clipboard.writeText(key);
    haptic('success');

    const btn = document.getElementById('copy-btn');
    if (btn) {
      btn.classList.add('copied', 'copy-feedback');
      btn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="20 6 9 17 4 12"/>
        </svg>`;
      setTimeout(() => {
        btn.classList.remove('copied', 'copy-feedback');
        btn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
          </svg>`;
      }, 2000);
    }

    showToast('✓ Ключ скопирован');
  } catch {
    showToast('Не удалось скопировать');
  }
};