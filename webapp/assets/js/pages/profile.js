    import { fetchUserData, rotateDeviceKey as apiRotateDeviceKey, fetchBotConfig } from '../api.js';

    const tg = window.Telegram?.WebApp;
    if (tg) { tg.expand(); tg.ready(); tg.enableClosingConfirmation(); }

    const tgUser = tg?.initDataUnsafe?.user;
    const params = new URLSearchParams(window.location.search);
    const USER_ID = tgUser?.id ?? (params.get('uid') ? parseInt(params.get('uid')) : null);

    // _instrKey — ключ для инструкции: первое активное устройство
    let _instrKey     = '';
    let _devices      = [];
    let _activeDevice = null;
    let _botName      = null;
    let _userStatus   = null;

    if (tgUser) {
      const fullName = [tgUser.first_name, tgUser.last_name].filter(Boolean).join(' ');
      document.getElementById('user-avatar').textContent   = (tgUser.first_name?.[0] || 'U').toUpperCase();
      document.getElementById('user-name').textContent     = fullName || 'Пользователь';
      document.getElementById('user-username').textContent = tgUser.username ? '@' + tgUser.username : 'без username';
    }
    if (USER_ID) document.getElementById('user-tg-id').textContent = USER_ID;

    // ------------------------------------------------------------------
    // Загрузка профиля
    // ------------------------------------------------------------------
    async function loadProfile() {
      if (!USER_ID) { showToast('⚠️ Откройте приложение через Telegram', 'error'); return; }
      try {
        const data = await fetchUserData(USER_ID);
        renderProfile(data);
      } catch (e) {
        console.error(e);
        const msg = e?.message ? `Ошибка загрузки: ${e.message}` : 'Ошибка загрузки данных';
        showToast(msg, 'error');
      }
      try {
        if (!_botName) {
          const cfg = await fetchBotConfig();
          _botName = cfg.bot_name;
        }
      } catch (e) {
        console.warn('fetchBotConfig failed:', e);
      }
    }

    function renderProfile(data) {
      const bal = parseFloat(data.balance ?? 0).toFixed(2);
      document.getElementById('balance-amount').textContent = `${bal} ₽`;

      const cost = data.monthly_cost ?? null;
      document.getElementById('balance-sub').textContent = cost
        ? `Расход: ${parseFloat(cost).toFixed(2)} ₽/мес`
        : 'Нет активных устройств';

      const statusMap = {
        ACTIVE:   { label: '✅ Активна',   cls: 'badge-success' },
        TRIAL:    { label: '🔓 Trial',      cls: 'badge-warning' },
        INACTIVE: { label: '⛔ Отключена', cls: 'badge-error'   },
        EXPIRED:  { label: '❌ Истекла',   cls: 'badge-error'   },
      };
      _userStatus = data.status;
      const st = statusMap[data.status] ?? { label: '— Нет данных', cls: 'badge-muted' };
      const badge = document.getElementById('status-badge');
      badge.textContent = st.label;
      badge.className   = `badge ${st.cls}`;

      document.getElementById('sub-status').textContent = st.label;
      document.getElementById('sub-days').textContent   = data.days_left != null ? `${data.days_left} дн.` : '—';
      document.getElementById('sub-cost').textContent   = cost ? `${parseFloat(cost).toFixed(2)} ₽/мес` : '—';

      _devices = data.devices ?? [];

      // Для инструкции используем первый активный ключ устройства.
      if (_devices.length) {
        const sorted = [..._devices].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
        const firstActive = sorted.find(d => d.is_active && d.vless_link);
        _instrKey = firstActive ? firstActive.vless_link : '';
      } else {
        _instrKey = '';
      }

      renderDevices(_devices);
    }

    // ------------------------------------------------------------------
    // Рендер списка устройств
    // ------------------------------------------------------------------
    function renderDevices(devices) {
      const list = document.getElementById('devices-list');
      if (!devices.length) {
        list.innerHTML = `<div style="padding:var(--space-4) 0; text-align:center;"><div style="font-size:var(--text-sm); color:var(--color-text-faint); font-style:italic;">Нет устройств</div></div>`;
        return;
      }
      list.innerHTML = devices.map(dev => {
        return `
          <div class="device-item" onclick="openDeviceSheet(${dev.id})">
            <div class="device-dot ${dev.is_active ? 'active' : 'inactive'}"></div>
            <span class="device-name">${escHtml(dev.device_name)}</span>
            <span class="device-cost">${parseFloat(dev.monthly_cost ?? 0).toFixed(2)} ₽/мес</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--color-text-faint); flex-shrink:0;"><polyline points="9 18 15 12 9 6"/></svg>
          </div>`;
      }).join('');
    }

    // ------------------------------------------------------------------
    // Шторка устройства
    // ------------------------------------------------------------------
    window.openDeviceSheet = function(deviceId) {
      const dev = _devices.find(d => d.id === deviceId);
      if (!dev) return;
      _activeDevice = dev;

      const dot = document.getElementById('ds-dot');
      dot.className = `device-sheet-dot ${dev.is_active ? 'active' : 'inactive'}`;
      document.getElementById('ds-name').textContent = dev.device_name;

      let statusText = dev.is_active ? 'Активно' : 'Отключено';
      if (dev.disabled_reason && !dev.is_active) {
        const reasonMap = {
          user_request: 'по запросу',
          insufficient_funds: 'недостаточно средств',
        };
        statusText = `Отключено (${reasonMap[dev.disabled_reason] || dev.disabled_reason})`;
      }
      document.getElementById('ds-status').textContent = statusText;

      const keySection = document.getElementById('ds-key-section');
      const keyText    = document.getElementById('ds-key-text');
      if (dev.vless_link) {
        keyText.textContent = dev.vless_link;
        keySection.style.display = '';
      } else {
        keySection.style.display = 'none';
      }

      const actionsDiv = document.getElementById('ds-actions');
      actionsDiv.innerHTML = '';

      if (dev.is_active) {
        const btnRotate = document.createElement('button');
        btnRotate.className = 'btn btn-primary btn-full';
        btnRotate.id = 'btn-ds-rotate';
        btnRotate.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
          </svg>
          Обновить ключ`;
        btnRotate.onclick = doRotateDevice;
        actionsDiv.appendChild(btnRotate);

        const btnDelete = document.createElement('button');
        btnDelete.className = 'btn btn-danger btn-full';
        btnDelete.id = 'btn-ds-delete';
        btnDelete.textContent = 'Отключить устройство';
        btnDelete.onclick = doDeleteDevice;
        actionsDiv.appendChild(btnDelete);
      } else {
        const infoEl = document.createElement('div');
        infoEl.className = 'device-disabled-info';
        infoEl.textContent = 'Устройство отключено.';
        actionsDiv.appendChild(infoEl);

        const btnHardDelete = document.createElement('button');
        btnHardDelete.className = 'btn btn-danger btn-full';
        btnHardDelete.textContent = '🗑 Удалить насовсем';
        btnHardDelete.onclick = doHardDeleteDevice;
        actionsDiv.appendChild(btnHardDelete);
      }

      const btnClose = document.createElement('button');
      btnClose.className = 'btn btn-ghost btn-full';
      btnClose.textContent = 'Закрыть';
      btnClose.onclick = () => closeModal('modal-device');
      actionsDiv.appendChild(btnClose);

      openModal('modal-device');
    };

    window.copyDeviceSheetKey = function() {
      const text = document.getElementById('ds-key-text').textContent;
      if (!text || text === '—') return;
      navigator.clipboard.writeText(text).then(() => showToast('✅ Ключ скопирован'));
    };

    // ------------------------------------------------------------------
    // Ротация ключа устройства
    // ------------------------------------------------------------------
    window.doRotateDevice = async function() {
      if (!USER_ID || !_activeDevice) return;
      const btn = document.getElementById('btn-ds-rotate');
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<div class="spinner" style="width:14px;height:14px;"></div> Обновляем...`;
      }

      try {
        const res = await apiRotateDeviceKey(USER_ID, _activeDevice.id);
        _activeDevice.vless_link = res.vless_link;
        document.getElementById('ds-key-text').textContent = res.vless_link;
        document.getElementById('ds-key-section').style.display = '';
        openKeyModal(res.vless_link);
        showToast('✅ Ключ устройства обновлён');
        await loadProfile();
      } catch (e) {
        const msg = e?.message ? `Ошибка обновления: ${e.message}` : '❌ Ошибка обновления ключа';
        showToast(msg, 'error');
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
            Обновить ключ`;
        }
      }
    };

    // ------------------------------------------------------------------
    // Мягкое отключение устройства
    // ------------------------------------------------------------------
    window.doDeleteDevice = function() {
      if (!_activeDevice) return;
      closeModal('modal-device');
      const name = _activeDevice.device_name;
      document.getElementById('modal-delete-title').textContent = `Отключить «${name}»?`;
      document.getElementById('modal-delete-desc').textContent =
        'Устройство будет отключено в панели. Списание по нему прекратится. Устройство останется в списке.';
      document.getElementById('btn-confirm-delete').onclick = () => confirmDeactivateDevice(_activeDevice.id);
      openModal('modal-delete');
    };

    async function confirmDeactivateDevice(id) {
      const btn = document.getElementById('btn-confirm-delete');
      btn.disabled = true;
      btn.innerHTML = `<div class="spinner"></div> Отключаем...`;
      try {
        const res = await fetch(`/api/user/${USER_ID}/devices/${id}`, { method: 'DELETE' });
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          throw new Error(errBody?.detail || String(res.status));
        }
        closeModal('modal-delete');
        showToast('✅ Устройство отключено');
        await loadProfile();
      } catch (e) {
        const msg = e?.message ? `Ошибка отключения: ${e.message}` : '❌ Ошибка отключения';
        showToast(msg, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Отключить';
      }
    }
    // ------------------------------------------------------------------
    // Хард-удаление устройства
    // ------------------------------------------------------------------

    window.doHardDeleteDevice = function() {
        if (!_activeDevice) return;
        closeModal('modal-device');
        const name = _activeDevice.device_name;
        document.getElementById('modal-hard-delete-title').textContent = `Удалить «${name}» насовсем?`;
        document.getElementById('modal-hard-delete-desc').textContent =
            'Ключ будет удалён из панели и из системы. Восстановить нельзя.';
        document.getElementById('btn-confirm-hard-delete').onclick = () => confirmHardDeleteDevice(_activeDevice.id);
        openModal('modal-hard-delete');
        };

        async function confirmHardDeleteDevice(id) {
        const btn = document.getElementById('btn-confirm-hard-delete');
        btn.disabled = true;
        btn.innerHTML = `<div class="spinner"></div> Удаляем...`;
        try {
            const res = await fetch(`/api/user/${USER_ID}/devices/${id}/hard`, { method: 'DELETE' });
            if (!res.ok) {
            const errBody = await res.json().catch(() => ({}));
            throw new Error(errBody?.detail || String(res.status));
            }
            closeModal('modal-hard-delete');
            showToast('✅ Устройство удалено');
            await loadProfile();
        } catch (e) {
            showToast(e?.message ? `Ошибка: ${e.message}` : '❌ Ошибка удаления', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '🗑 Удалить насовсем';
        }
        }

    // ------------------------------------------------------------------
    // Добавить устройство
    // ------------------------------------------------------------------
    window.openAddDevice = function() {
      document.getElementById('input-device-name').value = '';
      openModal('modal-add');
    };

    window.confirmAddDevice = async function() {
      const name = document.getElementById('input-device-name').value.trim();
      if (!name || name.length > 50) { showToast('❌ Введите название (1–50 символов)', 'error'); return; }
      const btn = document.getElementById('btn-confirm-add');
      btn.disabled = true;
      btn.innerHTML = `<div class="spinner"></div> Добавляем...`;
      try {
        const res = await fetch(`/api/user/${USER_ID}/devices`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ device_name: name }),
        });
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          throw new Error(errBody?.detail || String(res.status));
        }
        closeModal('modal-add');
        showToast('✅ Устройство добавлено');
        await loadProfile();
      } catch (e) {
        const msg = e?.message ? `Ошибка: ${e.message}` : '❌ Ошибка добавления устройства';
        showToast(msg, 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = 'Добавить';
      }
    };

    // ------------------------------------------------------------------
    // Модалка с новым ключом после ротации устройства
    // ------------------------------------------------------------------
    function openKeyModal(link) {
      document.getElementById('modal-new-key-text').textContent = link;
      openModal('modal-new-key');
    }

    window.copyModalKey = function() {
      const text = document.getElementById('modal-new-key-text').textContent;
      if (!text) return;
      navigator.clipboard.writeText(text).then(() => showToast('✅ Ключ скопирован'));
    };

    // ------------------------------------------------------------------
    // Инструкция — показывает ключ первого активного устройства
    // ------------------------------------------------------------------
    window.openInstruction = function() {
      const preview = document.getElementById('instr-key-preview');
      if (_instrKey) {
        const short = _instrKey.length > 60 ? _instrKey.slice(0, 60) + '…' : _instrKey;
        preview.textContent = short;
      } else {
        preview.textContent = 'Сначала добавьте устройство и скопируйте его ключ';
      }
      openModal('modal-instruction');
    };

    window.copyKeyFromInstruction = function() {
      if (!_instrKey) { showToast('❌ Ключ не найден — добавьте устройство', 'error'); return; }
      navigator.clipboard.writeText(_instrKey).then(() => {
        showToast('✅ Ключ скопирован');
      });
    };

    // ------------------------------------------------------------------
    // Поддержка — редирект в бота
    // ------------------------------------------------------------------
    window.openSupport = function() {
      closeModal('modal-instruction');
      if (_botName) {
        if (tg) {
          tg.openTelegramLink(`https://t.me/${_botName}?start=support`);
        } else {
          window.open(`https://t.me/${_botName}?start=support`, '_blank');
        }
      } else {
        window.location.href = 'support.html';
      }
    };

    // ------------------------------------------------------------------
    // Разное
    // ------------------------------------------------------------------
    window.openTopup = function() {
      if (!_botName) {
        if (tg) tg.close();
        return;
      }
      if (tg) {
        tg.openTelegramLink(`https://t.me/${_botName}?start=topup`);
      } else {
        window.open(`https://t.me/${_botName}?start=topup`, '_blank');
      }
    };

    function openModal(id) {
      document.getElementById(id).classList.add('open');
      document.body.style.overflow = 'hidden';
    }
    window.closeModal = function(id) {
      document.getElementById(id).classList.remove('open');
      document.body.style.overflow = '';
    };
    document.querySelectorAll('.modal-overlay').forEach(el => {
      el.addEventListener('click', e => { if (e.target === el) closeModal(el.id); });
    });

    // ------------------------------------------------------------------
    // showToast — единая функция
    // ------------------------------------------------------------------
    function showToast(msg, type = 'default') {
      const existing = document.querySelector('.toast-popup');
      if (existing) {
        clearTimeout(existing._timer);
        existing.remove();
      }

      const isError = type === 'error';
      const duration = isError ? 8000 : 2500;

      const wrap = document.createElement('div');
      wrap.className = 'toast-popup' + (isError ? ' toast-popup-error' : '');

      const inner = document.createElement('div');
      inner.className = 'toast-popup-inner';
      inner.textContent = msg;

      if (isError) {
        const x = document.createElement('button');
        x.className = 'toast-popup-close';
        x.setAttribute('aria-label', 'Закрыть');
        x.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
        x.onclick = () => hidePopup(wrap);
        inner.appendChild(x);
      }

      wrap.appendChild(inner);
      document.body.appendChild(wrap);
      wrap._timer = setTimeout(() => hidePopup(wrap), duration);
    }

    function hidePopup(el) {
      if (!el.isConnected) return;
      clearTimeout(el._timer);
      el.classList.add('toast-popup-out');
      el.addEventListener('animationend', () => el.remove(), { once: true });
    }

    function escHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    loadProfile();

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') loadProfile();
    });
