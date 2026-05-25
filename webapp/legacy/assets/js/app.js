// ============================================
// METRON WEBAPP — Entry point
// ============================================

import { initTelegram, getUser, sendToBot, showConfirm, haptic } from './telegram.js';
import { fetchUserData, rotateKey } from './api.js';
import { renderUser, renderSubscription, renderVlessKey, showToast } from './ui.js';

async function init() {
  initTelegram();

  const user = getUser();
  const userId = user?.id ?? 5937555925;

  try {
    const data = await fetchUserData(userId);
    renderUser(user, data);
    renderSubscription(data);
    renderVlessKey(data.vless_key);
  } catch (err) {
    console.error('Init error:', err);
    const msg = err?.message ? `Ошибка загрузки: ${err.message}` : 'Ошибка загрузки — попробуй позже';
    showToast(msg, 'error');
  }
}

// Ротация ключа
window.rotateKey = function () {
  showConfirm('Сгенерировать новый ключ? Старый перестанет работать.', async (confirmed) => {
    if (!confirmed) return;
    haptic('medium');

    try {
      const user = getUser();
      const data = await rotateKey(user?.id);
      renderVlessKey(data.vless_key);
      showToast('✓ Ключ обновлён');
      haptic('success');

      // Переключаем кнопку в режим «Обновить»
      const btn = document.getElementById('btn-rotate');
      if (btn) {
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Обновить`;
        btn.className = 'btn btn-ghost';
      }
    } catch (err) {
      console.error('rotateKey error:', err);
      const msg = err?.message ? `Ошибка ключа: ${err.message}` : 'Ошибка — попробуй позже';
      showToast(msg, 'error');
      haptic('error');
    }
  });
};

// Поддержка
window.openSupport = function () {
  window.open('https://t.me/Filactery', '_blank');
};

init();
