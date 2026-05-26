"""
Публичный API пакета app.db.

Все остальные модули проекта импортируют из этого файла —
внутренняя структура пакета для них прозрачна.
"""

from __future__ import annotations

from app.db.core import Database, init_db, close_db, get_db
from app.db.users import (
    ensure_user_stub,
    save_user,
    save_paid_access,
    get_user_data_dict,
    get_user_balance,
    get_user_by_uuid,
    update_user_link,
    update_user_status,
    deactivate_panel_client,
    set_low_balance_notified,
    set_reactivation_notification_pending,
    get_expired_trial_users,
)
from app.db.devices import (
    add_device,
    remove_device,
    deactivate_device,
    activate_device,
    get_user_devices,
    get_device_by_id,
    update_device_link,
    get_all_users_with_devices,
    get_user_total_monthly_cost,
    get_all_active_devices,
    get_all_reconcile_devices,
)
from app.db.billing import (
    add_balance_atomic,
    charge_balance_atomic,
    charge_daily_billing_atomic,
    update_user_last_billing_date,
    get_user_last_billing_date,
    get_balance_transactions,
    set_user_balance,
)
from app.db.payments import (
    record_payment_idempotent,
    apply_payment_topup_idempotent,
    get_payment_by_charge_ids,
)
from app.db.support import (
    create_ticket,
    get_user_tickets,
)

__all__ = [
    # core
    "Database",
    "init_db",
    "close_db",
    "get_db",
    # users
    "ensure_user_stub",
    "save_user",
    "save_paid_access",
    "get_user_data_dict",
    "get_user_balance",
    "get_user_by_uuid",
    "update_user_link",
    "update_user_status",
    "deactivate_panel_client",
    "set_low_balance_notified",
    "set_reactivation_notification_pending",
    "get_expired_trial_users",
    # devices
    "add_device",
    "remove_device",
    "deactivate_device",
    "activate_device",
    "get_user_devices",
    "get_device_by_id",
    "update_device_link",
    "get_all_users_with_devices",
    "get_user_total_monthly_cost",
    "get_all_active_devices",
    "get_all_reconcile_devices",
    # billing
    "add_balance_atomic",
    "charge_balance_atomic",
    "charge_daily_billing_atomic",
    "update_user_last_billing_date",
    "get_user_last_billing_date",
    "get_balance_transactions",
    "set_user_balance",
    # payments
    "record_payment_idempotent",
    "apply_payment_topup_idempotent",
    "get_payment_by_charge_ids",
    # support
    "create_ticket",
    "get_user_tickets",
]
