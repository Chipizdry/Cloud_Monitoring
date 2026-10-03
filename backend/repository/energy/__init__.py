"""Energy domain repository - Cerbo GX and energy managers.

Energy management repository functions.

Contains database operations for:
- Energetic object management
- Energy measurements (Cerbo, Modbus)
- Energy schedules
- ESS control operations
"""

from .energetic_device import (
	get_device_by_device_id,
	get_device_by_id,
	get_device_access,
	list_device_accesses,
	list_devices_for_user,
	list_all_devices,
	get_assigned_device_ids,
	upsert_device_access,
	upsert_energetic_device,
	update_device,
	delete_device,
	delete_device_access,
	remove_own_device_access,
)

from .energetic_object_access import (
	ensure_object_permission,
	get_object_access,
	get_object_by_id,
	is_admin_or_superadmin,
	list_object_accesses,
	list_objects_for_user,
	revoke_object_access,
	upsert_object_access,
)