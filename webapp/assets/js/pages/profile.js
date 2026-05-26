    import { fetchUserData, rotateDeviceKey as apiRotateDeviceKey, fetchBotConfig } from '../api.js';

    const tg = window.Telegram?.WebApp;
    if (tg) { tg.expand(); tg.ready(); tg.enableClosingConfirmation(); }

    const tgUser = tg?.initDataUnsafe?.user;
    const params = new URLSearchParams(window.location.search);
    const USER_ID = tgUser?.id ?? (params.get('uid') ? parseInt(params.get('uid')) : null);
    const PROFILE_LOADING_IDS = [
      'user-name',
      'user-username',
      'status-badge',
      'balance-amount',
      'balance-sub',
      'sub-status',
      'sub-days',
      'sub-cost',
      'user-tg-id',
    ];

    // _instrKey — ключ для инструкции: первое активное устройство
    let _instrKey     = '';
    let _devices      = [];
    let _activeDevice = null;
    let _botName      = null;
    let _userStatus   = null;
    let _botNamePromise = null;
    let _loadSeq = 0;
    let _profileLoaded = false;
    let _lastVisibleRefreshAt = 0;

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
    async function ensureBotName() {
      if (_botName) return _botName;
      if (_botNamePromise) return _botNamePromise;

      _botNamePromise = fetchBotConfig()
        .then(cfg => {
          _botName = cfg?.bot_name || null;
          return _botName;
        })
        .catch(e => {
          console.warn('fetchBotConfig failed:', e);
          return null;
        })
        .finally(() => {
          _botNamePromise = null;
        });

      return _botNamePromise;
    }

    function formatMoney(value) {
      return `${parseFloat(value ?? 0).toFixed(2)} ₽`;
    }

    function buildDevicesLoadingMarkup() {
      return `
        <div class="device-item" aria-hidden="true">
          <div class="device-dot inactive"></div>
          <div class="device-main">
            <span class="device-name skeleton">Загрузка устройства</span>
            <span class="device-meta skeleton">Статус загружается</span>
          </div>
          <span class="device-item-arrow"> </span>
        </div>
        <div class="device-item" aria-hidden="true">
          <div class="device-dot inactive"></div>
          <div class="device-main">
            <span class="device-name skeleton">Загрузка устройства</span>
            <span class="device-meta skeleton">Статус загружается</span>
          </div>
          <span class="device-item-arrow"> </span>
        </div>`;
    }

    function renderDevicesEmptyState() {
      const list = document.getElementById('devices-list');
      list.innerHTML = `
        <div class="device-empty">
          <div class="device-empty-title">Пока нет устройств</div>
          <div class="device-empty-desc">Добавьте первое устройство, чтобы получить персональный ключ и быстро подключиться по инструкции.</div>
        </div>`;
    }

    function renderDevicesErrorState(message) {
      const list = document.getElementById('devices-list');
      list.innerHTML = `
        <div class="device-empty">
          <div class="device-empty-title">Профиль не загрузился</div>
          <div class="device-empty-desc">${escHtml(message)}</div>
        </div>`;
    }

    function setProfileLoading(isLoading, { soft = false } = {}) {
      document.body.classList.toggle('profile-soft-loading', Boolean(isLoading && soft));

      PROFILE_LOADING_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.toggle('skeleton', Boolean(isLoading && !soft));
        if (!soft) {
          el.setAttribute('aria-busy', isLoading ? 'true' : 'false');
        }
      });

      const topupBtn = document.getElementById('btn-topup');
      const addBtn = document.getElementById('btn-add-device');
      if (topupBtn) topupBtn.disabled = Boolean(isLoading && !_profileLoaded);
      if (addBtn) addBtn.disabled = Boolean(isLoading && !_profileLoaded);

      const list = document.getElementById('devices-list');
      if (!list) return;
      list.classList.toggle('is-soft-loading', Boolean(isLoading && soft));

      if (isLoading && (!soft || !_profileLoaded)) {
        list.innerHTML = buildDevicesLoadingMarkup();
      }
    }

    async function loadProfile({ soft = false } = {}) {
      if (!USER_ID) {
        renderDevicesErrorState('Откройте приложение через Telegram, чтобы увидеть профиль.');
        showToast('⚠️ Откройте приложение через Telegram', 'error');
        return;
      }

      const seq = ++_loadSeq;
      setProfileLoading(true, { soft: soft && _profileLoaded });

      try {
        const [data] = await Promise.all([
          fetchUserData(USER_ID),
          ensureBotName(),
        ]);
        if (seq !== _loadSeq) return;
        renderProfile(data);
        _profileLoaded = true;
      } catch (e) {
        if (seq !== _loadSeq) return;
        console.error(e);
        const msg = e?.message ? `Ошибка загрузки: ${e.message}` : 'Ошибка загрузки данных';
        if (!_profileLoaded) {
          renderDevicesErrorState(msg);
        }
        showToast(msg, 'error');
      } finally {
        if (seq === _loadSeq) {
          setProfileLoading(false, { soft: soft && _profileLoaded });
        }
      }
    }

    function renderProfile(data) {
      document.getElementById('balance-amount').textContent = formatMoney(data.balance);

      const cost = data.monthly_cost ?? null;
      document.getElementById('balance-sub').textContent = cost
        ? `Расход: ${formatMoney(cost)}/мес`
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
      document.getElementById('sub-cost').textContent   = cost ? `${formatMoney(cost)}/мес` : '—';

      _devices = [...(data.devices ?? [])].sort((a, b) => {
        if (a.is_active !== b.is_active) return Number(b.is_active) - Number(a.is_active);
        return new Date(a.created_at || 0) - new Date(b.created_at || 0);
      });

      // Для инструкции используем первый активный ключ устройства.
      if (_devices.length) {
        const sorted = [..._devices].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
        const firstActive = sorted.find(d => d.is_active && d.vless_link);
        _instrKey = firstActive ? firstActive.vless_link : '';
      } else {
        _instrKey = '';
      }

      syncInstructionState();
      renderDevices(_devices);
    }

    function syncInstructionState() {
      const copyBtn = document.getElementById('btn-instr-copy');
      const supportBtn = document.getElementById('btn-instr-support');
      if (!copyBtn || !supportBtn) return;

      if (_instrKey) {
        copyBtn.disabled = false;
        copyBtn.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
          </svg>
          Скопировать ключ`;
        supportBtn.className = 'btn btn-ghost btn-full';
        supportBtn.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          Написать в поддержку`;
        return;
      }

      copyBtn.disabled = true;
      copyBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
        </svg>
        Ключ недоступен`;
      supportBtn.className = 'btn btn-primary btn-full';
      supportBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        Открыть поддержку`;
    }

    // ------------------------------------------------------------------
    // Рендер списка устройств
    // ------------------------------------------------------------------
    function formatDeviceMeta(dev) {
      const cost = `${formatMoney(dev.monthly_cost)}/мес`;
      if (dev.is_active) {
        return `Активно · ${cost}`;
      }
      const reasonMap = {
        user_request: 'выключено вручную',
        insufficient_funds: 'недостаточно средств',
      };
      const reason = dev.disabled_reason ? reasonMap[dev.disabled_reason] || dev.disabled_reason : 'отключено';
      return `${reason} · ${cost}`;
    }

    function renderDevices(devices) {
      const list = document.getElementById('devices-list');
      if (!devices.length) {
        renderDevicesEmptyState();
        return;
      }
      list.innerHTML = devices.map(dev => {
        return `
          <div
            class="device-item ${dev.is_active ? '' : 'is-inactive'}"
            onclick="openDeviceSheet(${dev.id})"
            onkeydown="handleDeviceItemKeydown(event, ${dev.id})"
            role="button"
            tabindex="0"
            aria-label="Открыть устройство ${escHtml(dev.device_name)}"
          >
            <div class="device-dot ${dev.is_active ? 'active' : 'inactive'}"></div>
            <div class="device-main">
              <span class="device-name">${escHtml(dev.device_name)}</span>
              <span class="device-meta">${escHtml(formatDeviceMeta(dev))}</span>
            </div>
            <svg class="device-item-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
          </div>`;
      }).join('');
    }

    window.handleDeviceItemKeydown = function(event, deviceId) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openDeviceSheet(deviceId);
      }
    };

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
      copyText(text, '✅ Ключ скопирован');
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
        closeModal('modal-device');
        openKeyModal(res.vless_link);
        showToast('✅ Ключ устройства обновлён');
        await loadProfile({ soft: true });
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
        await loadProfile({ soft: true });
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
            await loadProfile({ soft: true });
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
        await loadProfile({ soft: true });
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
      copyText(text, '✅ Ключ скопирован');
    };

    // ------------------------------------------------------------------
    // Инструкция — показывает ключ первого активного устройства
    // ------------------------------------------------------------------
    window.openInstruction = function() {
      const preview = document.getElementById('instr-key-preview');
      syncInstructionState();
      if (_instrKey) {
        const short = _instrKey.length > 60 ? _instrKey.slice(0, 60) + '…' : _instrKey;
        preview.textContent = short;
      } else if (_devices.length) {
        preview.textContent = 'Сейчас у вас нет активного ключа. Пополните баланс или откройте поддержку, если доступ не восстановился автоматически.';
      } else {
        preview.textContent = 'Сначала добавьте устройство и скопируйте его ключ';
      }
      openModal('modal-instruction');
    };

    window.copyKeyFromInstruction = function() {
      if (!_instrKey) { showToast('❌ Ключ не найден — добавьте устройство', 'error'); return; }
      copyText(_instrKey, '✅ Ключ скопирован');
    };

    // ------------------------------------------------------------------
    // Поддержка — редирект в бота
    // ------------------------------------------------------------------
    window.openSupport = async function() {
      closeModal('modal-instruction');
      const botName = _botName || await ensureBotName();
      if (botName) {
        if (tg) {
          tg.openTelegramLink(`https://t.me/${botName}?start=support`);
        } else {
          window.open(`https://t.me/${botName}?start=support`, '_blank');
        }
      } else {
        window.location.href = 'support.html';
      }
    };

    // ------------------------------------------------------------------
    // Разное
    // ------------------------------------------------------------------
    window.openTopup = async function() {
      const botName = _botName || await ensureBotName();
      if (!botName) {
        showToast('Не удалось открыть пополнение. Попробуйте ещё раз через пару секунд.', 'error');
        return;
      }
      if (tg) {
        tg.openTelegramLink(`https://t.me/${botName}?start=topup`);
      } else {
        window.open(`https://t.me/${botName}?start=topup`, '_blank');
      }
    };

    function updateBodyScrollLock() {
      const hasOpenModal = document.querySelector('.modal-overlay.open');
      document.body.style.overflow = hasOpenModal ? 'hidden' : '';
    }

    function openModal(id) {
      document.getElementById(id).classList.add('open');
      updateBodyScrollLock();
    }
    window.closeModal = function(id) {
      document.getElementById(id).classList.remove('open');
      updateBodyScrollLock();
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

    async function copyText(text, successMessage) {
      try {
        await navigator.clipboard.writeText(text);
        showToast(successMessage);
      } catch (e) {
        console.warn('clipboard write failed:', e);
        showToast('Не удалось скопировать ключ. Разрешите доступ к буферу обмена и попробуйте снова.', 'error');
      }
    }

    function escHtml(s) {
      return String(s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    loadProfile();

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - _lastVisibleRefreshAt < 5000) return;
      _lastVisibleRefreshAt = now;
      loadProfile({ soft: true });
    });
