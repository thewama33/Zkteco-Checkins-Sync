# Copyright (c) 2025, osama.ahmed@deliverydevs.com
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
import requests
from frappe.utils import today, now_datetime, get_datetime
from datetime import timedelta


class ZKTecoConfig(Document):
    pass


def get_base_url():
    """Build the base URL from ZKTeco Config settings."""
    cfg = frappe.get_single("ZKTeco Config")
    scheme = "https" if cfg.use_https else "http"
    server_ip = cfg.server_ip
    server_port = cfg.server_port

    # Omit port for default HTTP/HTTPS ports
    if (scheme == "https" and str(server_port) == "443") or (scheme == "http" and str(server_port) == "80"):
        return f"{scheme}://{server_ip}"
    return f"{scheme}://{server_ip}:{server_port}"


def refresh_token():
    """Re-authenticate with the ZKTeco server and save the new token."""
    username = frappe.db.get_single_value("ZKTeco Config", "username")
    password = frappe.utils.password.get_decrypted_password("ZKTeco Config", "ZKTeco Config", "password")
    if not username or not password:
        return None

    url = f"{get_base_url()}/jwt-api-token-auth/"
    try:
        resp = requests.post(url, json={"username": username, "password": password},
                             headers={"Content-Type": "application/json"}, timeout=15)
        resp.raise_for_status()
        token = resp.json().get("token")
        if token:
            frappe.db.set_single_value("ZKTeco Config", "token", token)
            frappe.db.commit()
        return token
    except Exception:
        return None


@frappe.whitelist()
def register_api_token():
    """
    Calls the remote API to obtain a token and returns it to the client.
    """
    username = frappe.db.get_single_value("ZKTeco Config", "username")
    password = frappe.utils.password.get_decrypted_password("ZKTeco Config", "ZKTeco Config", "password")

    if not all([username, password]):
        frappe.throw(_("Please configure username and password in ZKTeco Config."))

    url = f"{get_base_url()}/jwt-api-token-auth/"

    payload = {
        "username": username,
        "password": password
    }

    try:
        resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        token = data.get("token")
        if not token:
            frappe.throw(_("Token not found in API response."))

        return {"success": True, "token": token}

    except requests.exceptions.RequestException as e:
        frappe.throw(_("Connection error: {0}").format(str(e)))


@frappe.whitelist()
def test_connection():
    """
    Enhanced test connection that shows latest transactions with detailed info
    """
    cfg = frappe.get_single("ZKTeco Config")
    token = (cfg.token or "").strip()

    if not token:
        return {"ok": False, "error": _("Token not set in ZKTeco Config. Please register/save a token first.")}

    base_url = f"{get_base_url()}/iclock/api/transactions/"
    day = today()
    start_time = f"{day} 00:00:00"
    end_time = f"{day} 23:59:59"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"JWT {token}",
    }
    params = {
        "start_time": start_time,
        "end_time": end_time,
    }

    try:
        resp = requests.get(base_url, headers=headers, params=params, timeout=15)
        
        if resp.ok:
            try:
                data = resp.json()
                
                # Process and format transaction data for display
                formatted_transactions = []
                transaction_count = 0
                
                # Handle ZKTeco API response structure
                if isinstance(data, dict) and 'data' in data:
                    transactions = data['data']
                    transaction_count = data.get('count', len(transactions))
                elif isinstance(data, dict) and 'results' in data:
                    transactions = data['results']
                    transaction_count = len(transactions)
                elif isinstance(data, list):
                    transactions = data
                    transaction_count = len(transactions)
                else:
                    transactions = []
                
                # Format latest 5 transactions for preview
                for transaction in transactions[:5]:
                    try:
                        # Map ZKTeco transaction fields based on actual API response
                        emp_code = transaction.get('emp_code')
                        punch_time = transaction.get('punch_time')
                        punch_state = transaction.get('punch_state')
                        punch_state_display = transaction.get('punch_state_display')
                        device_id = transaction.get('terminal_alias') or transaction.get('terminal_sn')
                        first_name = transaction.get('first_name', '')
                        last_name = transaction.get('last_name', '') or ''
                        verify_type_display = transaction.get('verify_type_display')
                        
                        # Combine first and last name
                        zkteco_name = f"{first_name} {last_name}".strip()
                        
                        # Try to find employee name from ERPNext
                        employee_name = zkteco_name
                        erpnext_employee = None
                        if emp_code:
                            # Try to find employee by employee_id or user_id
                            employee = frappe.db.get_value("Employee", 
                                                         {"employee": emp_code}, 
                                                         ["name", "employee_name"])
                            if not employee:
                                employee = frappe.db.get_value("Employee", 
                                                             {"user_id": emp_code}, 
                                                             ["name", "employee_name"])
                            if employee:
                                erpnext_employee = employee[0] if isinstance(employee, tuple) else employee
                                employee_name = f"{employee[1]} (ERPNext)" if isinstance(employee, tuple) else f"{employee} (ERPNext)"
                        
                        # Determine log type based on punch_state
                        log_type = "IN"
                        if str(punch_state) == "1" or punch_state_display == "Check Out":
                            log_type = "OUT"
                        
                        formatted_transactions.append({
                            "id": transaction.get('id'),
                            "employee_code": emp_code,
                            "employee_name": employee_name,
                            "erpnext_employee": erpnext_employee,
                            "punch_time": punch_time,
                            "log_type": log_type,
                            "punch_state_display": punch_state_display,
                            "device_id": device_id,
                            "verify_method": verify_type_display,
                            "zkteco_name": zkteco_name,
                            "department": transaction.get('department')
                        })
                    except Exception as e:
                        frappe.log_error(f"Error processing transaction: {e}", "ZKTeco Transaction Processing")
                        continue
                
                return {
                    "ok": True,
                    "status_code": resp.status_code,
                    "url": resp.url,
                    "total_transactions": transaction_count,
                    "transactions_preview": formatted_transactions,
                    "message": f"Found {transaction_count} transactions for {day}"
                }
                
            except ValueError as e:
                return {
                    "ok": False,
                    "status_code": resp.status_code,
                    "error": f"Invalid JSON response: {str(e)}",
                }
        else:
            return {
                "ok": False,
                "status_code": resp.status_code,
                "url": resp.url,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"
            }
            
    except requests.RequestException as e:
        return {
            "ok": False,
            "error": f"Connection error: {str(e)}"
        }


def sync_zkteco_transactions():
    """
    Main function to sync ZKTeco transactions with ERPNext Employee Checkin records
    """
    # Check if sync is enabled
    cfg = frappe.get_single("ZKTeco Config")
    if not cfg.enable_sync or not cfg.token:
        return
    
    try:
        # Get transactions from last sync or last hour
        last_sync_val = frappe.db.get_single_value("ZKTeco Config", "last_sync")
        last_sync = get_datetime(last_sync_val) if last_sync_val else (now_datetime() - timedelta(hours=1))
        current_time = now_datetime()
        
        transactions = fetch_zkteco_transactions(cfg, last_sync, current_time)

        processed_count = 0
        error_count = 0

        for transaction in transactions:
            try:
                if create_employee_checkin(transaction):
                    processed_count += 1
                else:
                    error_count += 1
            except Exception as e:
                error_count += 1
                frappe.log_error(f"Error creating checkin for transaction {transaction}: {str(e)}", "ZKTeco Sync Error")

        # Always update last sync time so the UI shows the scheduler is active
        frappe.db.set_single_value("ZKTeco Config", "last_sync", current_time)
        if processed_count:
            total_synced = frappe.db.get_single_value("ZKTeco Config", "total_synced_records") or 0
            frappe.db.set_single_value("ZKTeco Config", "total_synced_records", total_synced + processed_count)
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(f"ZKTeco sync failed: {str(e)}", "ZKTeco Sync Fatal Error")


def fetch_zkteco_transactions(cfg, start_time, end_time):
    """
    Fetch transactions from ZKTeco device, following pagination.
    """
    token = cfg.token

    url = f"{get_base_url()}/iclock/api/transactions/"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"JWT {token}",
    }

    params = {
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "page_size": 1000,
    }

    all_transactions = []

    try:
        while url:
            resp = requests.get(url, headers=headers, params=params, timeout=60)

            # Auto-refresh token on 401 Unauthorized and retry once
            if resp.status_code == 401:
                new_token = refresh_token()
                if new_token:
                    headers["Authorization"] = f"JWT {new_token}"
                    resp = requests.get(url, headers=headers, params=params, timeout=60)

            resp.raise_for_status()

            data = resp.json()

            # Extract transactions from response
            if isinstance(data, dict) and 'data' in data:
                all_transactions.extend(data['data'])
                url = data.get('next')
            elif isinstance(data, dict) and 'results' in data:
                all_transactions.extend(data['results'])
                url = data.get('next')
            elif isinstance(data, list):
                all_transactions.extend(data)
                url = None
            else:
                url = None

            # Fix: pagination URLs from API may use http; match our scheme
            if url and cfg.use_https and url.startswith("http://"):
                url = url.replace("http://", "https://", 1)

            # Clear params after first request; pagination URL includes them
            params = None

        return all_transactions

    except Exception as e:
        frappe.log_error(f"Failed to fetch ZKTeco transactions: {str(e)}", "ZKTeco API Error")
        return all_transactions or []


def create_employee_checkin(transaction):
    """
    Create Employee Checkin record from ZKTeco transaction
    """
    try:
        # Extract transaction data based on ZKTeco API response structure
        emp_code = transaction.get('emp_code')
        punch_time = transaction.get('punch_time')
        punch_state = transaction.get('punch_state')
        device_id = transaction.get('terminal_alias') or transaction.get('terminal_sn')
        transaction_id = transaction.get('id')
        
        if not emp_code or not punch_time:
            frappe.log_error(f"Missing required fields in transaction: {transaction}", "ZKTeco Transaction Error")
            return False
        
        # Find employee
        employee = find_employee_by_code(emp_code)
        if not employee:
            return False
        
        # Convert punch_time to datetime
        if isinstance(punch_time, str):
            punch_datetime = get_datetime(punch_time)
        else:
            punch_datetime = punch_time
        
        # Determine log type based on punch_state
        log_type = "IN"
        if str(punch_state) == "1":  # Based on API response: "1" = Check Out
            log_type = "OUT"
        
        # Build unique device_id that includes the ZKTeco transaction ID
        checkin_device_id = f"{device_id} (ZKTeco-{transaction_id})" if transaction_id else device_id or "ZKTeco Device"

        # Check if checkin already exists
        if frappe.db.exists("Employee Checkin", {"device_id": checkin_device_id}):
            return True  # Already processed

        # Create Employee Checkin
        checkin = frappe.get_doc({
            "doctype": "Employee Checkin",
            "employee": employee,
            "time": punch_datetime,
            "log_type": log_type,
            "device_id": checkin_device_id,
            "skip_auto_attendance": 0,
        })

        checkin.insert(ignore_permissions=True)
        
        return True
        
    except Exception as e:
        frappe.log_error(f"Error creating Employee Checkin: {str(e)}", "ZKTeco Checkin Creation")
        return False


def find_employee_by_code(emp_code):
    """
    Find employee by various ID fields
    """
    # Try employee field first
    employee = frappe.db.get_value("Employee", {"employee": emp_code}, "name")
    if employee:
        return employee
    
    # Try user_id field
    employee = frappe.db.get_value("Employee", {"user_id": emp_code}, "name")
    if employee:
        return employee
    
    # Try attendance_device_id if it exists
    if frappe.db.has_column("Employee", "attendance_device_id"):
        employee = frappe.db.get_value("Employee", {"attendance_device_id": emp_code}, "name")
        if employee:
            return employee
    
    return None


@frappe.whitelist()
def manual_sync():
    """
    Manual sync trigger for testing
    """
    try:
        sync_zkteco_transactions()
        return {"success": True, "message": "Sync completed successfully"}
    except Exception as e:
        frappe.log_error(f"Manual sync failed: {str(e)}", "ZKTeco Manual Sync")
        return {"success": False, "message": f"Sync failed: {str(e)}"}


def scheduled_sync():
    """
    Scheduled sync function that respects the configured frequency.
    Called every minute by the scheduler; skips execution when the
    configured interval has not yet elapsed.
    """
    try:
        cfg = frappe.get_single("ZKTeco Config")
        if not cfg.enable_sync or not cfg.token:
            return

        sync_seconds = int(cfg.seconds or 300)
        current_time = now_datetime()

        last_run = frappe.cache().get_value("zkteco_last_sync_run")
        if last_run:
            time_diff = (current_time - get_datetime(last_run)).total_seconds()
            if time_diff < sync_seconds:
                return  # Not yet time for next sync

        frappe.cache().set_value("zkteco_last_sync_run", current_time)
        sync_zkteco_transactions()

    except Exception as e:
        frappe.log_error(f"Scheduled ZKTeco sync failed: {str(e)}", "ZKTeco Scheduled Sync Error")


@frappe.whitelist()
def get_sync_status():
    """
    Get current sync status and statistics
    """
    try:
        cfg = frappe.get_single("ZKTeco Config")
        
        # Get last sync time
        last_sync = frappe.db.get_single_value("ZKTeco Config", "last_sync")
        
        # Count recent employee checkins from ZKTeco
        recent_checkins = frappe.db.count("Employee Checkin", {
            "device_id": ["like", "%ZKTeco%"],
            "creation": [">=", frappe.utils.add_days(today(), -1)]
        })
        
        return {
            "enabled": cfg.enable_sync,
            "sync_frequency": cfg.seconds,
            "last_sync": last_sync,
            "recent_checkins_24h": recent_checkins,
            "server_configured": bool(cfg.server_ip and cfg.server_port),
            "token_configured": bool(cfg.token)
        }
        
    except Exception as e:
        return {"error": str(e)}