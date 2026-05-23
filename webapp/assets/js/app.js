// ============================================
// METRON WEBAPP — Entry point
// ============================================

import { initTelegram, getUser, sendToBot, showConfirm, haptic } from './telegram.js';
import { fetchUserData, rotateKey } from './api.js';
import { renderUser, renderSubscription, renderVlessKey, showToast } from './ui.js';

async function init() {
  initTelegram();

  const user = getUser();
  // Фолбек для теста пока initDataUnsafe пустой
  const userId = user?.id ?? 5937555925;

  try {
    const data = await fetchUserData(userId);
    renderUser(user, data);
    renderSubscription(data);
    renderVlessKey(data.vless_key);
  } catch (err) {
    console.error('Init error:', err);
    showToast('Ошибка загрузки данных');
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
    } catch {
      showToast('Ошибка — попробуй позже');
      haptic('error');
    }
  });
};

// Поддержка
window.openSupport = function () {
  window.open('https://t.me/Filactery', '_blank');
};

init();
