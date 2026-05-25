// ============================================
// METRON WEBAPP — Telegram WebApp bridge
// ============================================

const tg = window.Telegram.WebApp;

export function initTelegram() {
  tg.expand();
  tg.ready();
  tg.setHeaderColor('#1a1b27');
  tg.setBackgroundColor('#0d0e17');
}

export function getUser() {
  return tg.initDataUnsafe?.user ?? null;
}

export function sendToBot(data) {
  tg.sendData(JSON.stringify(data));
}

export function showPopup(title, message) {
  tg.showPopup({ title, message, buttons: [{ type: 'ok' }] });
}

export function showConfirm(message, callback) {
  tg.showConfirm(message, callback);
}

export function haptic(type = 'light') {
  // type: light | medium | heavy | success | error | warning
  if (tg.HapticFeedback) {
    if (['success', 'error', 'warning'].includes(type)) {
      tg.HapticFeedback.notificationOccurred(type);
    } else {
      tg.HapticFeedback.impactOccurred(type);
    }
  }
}

export function closeMiniApp() {
  tg.close();
}

export { tg };