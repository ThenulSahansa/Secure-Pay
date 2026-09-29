import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import sqlite3
import re

from API import (
    extract_text,
    update_messages,
    update_previous_payments,
    get_previous_payments,
    get_image_hash,
    get_previous_hashes,
    is_slip_difficult_to_read,
    parse_amount,
    resolve_slip_path,
)

app = Flask(__name__)
app.secret_key = 'securepay-secret-key-2026'
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'bank_slips')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SecurePayDataBase.db')

# ─── Helper Functions (exact replicas from cli.py) ────────────────────────────

def parse_datetime(val):
    if not val:
        return None
    val = re.sub(r'[^\x20-\x7E]', '', str(val)).strip('. ')
    for fmt in [
        '%d/%m/%Y %H:%M', '%d/%m/%Y %I:%M %p', '%d-%m-%Y %I:%M %p', '%d-%m-%Y %H:%M',
        '%Y/%m/%d %I:%M %p', '%Y/%m/%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
        '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d', '%Y-%m-%d'
    ]:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None

def normalize_name(name):
    if not name:
        return ""
    s = str(name).upper()
    s = re.sub(r"\b(MR|MRS|MISS|MS|DR|REV|HON|PROF|MR\.|MRS\.|DR\.)\b", "", s)
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s

def names_match(n1, n2):
    if not n1 or not n2:
        return True
    s1, s2 = normalize_name(n1), normalize_name(n2)
    if not s1 or not s2:
        return True
    if s1 == s2 or s1 in s2 or s2 in s1:
        return True
    t1 = set(re.findall(r"[A-Z]{3,}", str(n1).upper()))
    t2 = set(re.findall(r"[A-Z]{3,}", str(n2).upper()))
    return bool(t1 and t2 and (t1 & t2))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Core Verification Logic (exact replica of cli.py) ────────────────────────

def verify_slip(slip_path, order_id):
    """
    Runs all 11 verification checks from cli.py.
    Returns a dict with status, reason, extracted_data, raw_ocr, slip_hash.
    """
    result = {
        'status': '',
        'reason': '',
        'extracted_data': {},
        'raw_ocr': '',
        'slip_hash': '',
    }

    try:
        slip_path = resolve_slip_path(slip_path)
        if not os.path.exists(slip_path):
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'File not found'})
            return result

        if is_slip_difficult_to_read(slip_path):
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Unclear image'})
            return result

        slip_text = extract_text(slip_path)
        result['raw_ocr'] = "\n".join(slip_text) if isinstance(slip_text, list) else str(slip_text)

        if isinstance(slip_text, str):
            slip_text = slip_text.split('\n')

        messages_database = DB_PATH

        conn = sqlite3.connect(messages_database)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM messages")
        db_results = cursor.fetchall()
        messages = [i[1] for i in db_results]
        conn.close()

        # ── Parse all fields from slip ──
        amount_on_slip = None
        transaction_id_on_slip = None
        account_no_on_slip = None
        date_time_on_slip = None
        sender_name_on_slip = None
        beneficiary_name_on_slip = None
        remarks_on_slip = None
        fee_on_slip = None
        total_amount_on_slip = None
        printed_date_on_slip = None
        status_on_slip = None

        full_text = " ".join(slip_text)

        def clean_text(text):
            return re.sub(r'[^\x20-\x7E]', '', str(text)).strip()

        def norm_label(text):
            clean = clean_text(text)
            return re.sub(r"[\'\'`]", '', clean).strip().lower().rstrip(':,.')

        # --- Detect column format ---
        is_column_format = False
        known_labels = {
            'account no', 'date-time', 'reference no', 'reference no.', 'amount', 'fee', 'total amount',
            'bank reference number', 'transfer amount', 'beneficiary account number',
            'transaction date/time', 'transfer currency', 'commission', 'beneficiary name',
            'purpose of payment', 'payment date', 'bank/branch name', 'status',
            "senders account number", "sender account number", "senders name", "sender name",
            "beneficiarys account narration", "beneficiary account narration"
        }
        matched_label_count = 0
        for line in slip_text:
            cleaned = norm_label(line)
            if cleaned in known_labels:
                matched_label_count += 1

        is_column_format = matched_label_count >= 3

        if is_column_format:
            first_known_label_idx = len(slip_text)
            last_known_label_idx = -1
            for idx, line in enumerate(slip_text):
                cleaned = norm_label(line)
                if cleaned in known_labels:
                    if idx < first_known_label_idx:
                        first_known_label_idx = idx
                    last_known_label_idx = idx

            all_label_lines = []
            for idx in range(first_known_label_idx, last_known_label_idx + 1):
                cleaned = norm_label(slip_text[idx])
                if cleaned:
                    all_label_lines.append((idx, cleaned))

            value_lines = []
            for j in range(last_known_label_idx + 1, len(slip_text)):
                stripped = slip_text[j].strip()
                if stripped and not stripped.startswith('['):
                    value_lines.append(stripped)

            for i, (idx, label) in enumerate(all_label_lines):
                if i < len(value_lines):
                    val = value_lines[i]
                    if label in ['account no', 'beneficiary account number']:
                        account_no_on_slip = val.rstrip(',')
                    elif label in ['date-time', 'transaction date/time']:
                        date_time_on_slip = parse_datetime(val)
                    elif label in ['reference no', 'reference no.', 'bank reference number']:
                        transaction_id_on_slip = val.rstrip(',')
                    elif label in ['amount', 'transfer amount']:
                        num_match = re.match(r'^([\d,]+\.?\d*)', val)
                        if num_match:
                            amount_on_slip = num_match.group(1)
                        else:
                            amount_on_slip = val
                    elif label in ["senders name", "sender name"]:
                        sender_name_on_slip = val
                    elif label in ["beneficiary name"]:
                        beneficiary_name_on_slip = val
                    elif label in ["purpose of payment", "purpose"]:
                        remarks_on_slip = val
                    elif label in ["narration", "beneficiarys account narration", "beneficiary account narration"]:
                        if not remarks_on_slip or len(val) > len(remarks_on_slip):
                            remarks_on_slip = val
                    elif label in ["status"]:
                        status_on_slip = val
                    elif label in ["fee", "commission"]:
                        num_match = re.match(r'^([\d,]+\.?\d*)', val)
                        fee_on_slip = num_match.group(1) if num_match else val
                    elif label in ["total amount"]:
                        num_match = re.match(r'^([\d,]+\.?\d*)', val)
                        total_amount_on_slip = num_match.group(1) if num_match else val

        # --- Extract Amount ---
        if not amount_on_slip:
            amount_patterns = [
                r'Transaction Amount\s+LKR\s+([\d,]+\.?\d*)',
                r'Transfer Amount\s+([\d,]+\.?\d*)',
                r'Transostion Amoun[t]?\s+K?L?R?\s*([\d,]+\.?\d*)',
                r'Charge Amount\s+L?K?R?\s*([\d,]+\.?\d*)',
            ]
            for pattern in amount_patterns:
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    amount_on_slip = match.group(1)
                    break

        if not amount_on_slip:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Cant read amount on the slip'})
            return result

        # --- Extract Transaction ID / Reference No ---
        if not transaction_id_on_slip:
            for idx, line in enumerate(slip_text):
                stripped = clean_text(line)
                if re.search(r'^(Reference No\.?|Bank Reference Number)$', stripped, re.IGNORECASE):
                    for j in range(idx + 1, min(idx + 5, len(slip_text))):
                        ref_val = slip_text[j].strip()
                        ref_match = re.match(r'^([\dA-Za-z,]+)$', ref_val)
                        if ref_match and ref_match.group(1):
                            transaction_id_on_slip = ref_match.group(1).rstrip(',')
                            break
                    break

        if not transaction_id_on_slip:
            for line in slip_text:
                cleaned_line = clean_text(line)
                match = re.search(r'Transaction ID\s+(.+)$', cleaned_line, re.IGNORECASE)
                if match:
                    transaction_id_on_slip = match.group(1).strip().replace(' ', '')
                    break

        if not transaction_id_on_slip:
            match = re.search(r'cyber Receipt Reference\s+([^\]]+)', full_text, re.IGNORECASE)
            if match:
                transaction_id_on_slip = match.group(1).strip()

        if not transaction_id_on_slip:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Cant read transaction ID on the slip'})
            return result

        # --- Extract Account Number ---
        if not account_no_on_slip:
            for idx, line in enumerate(slip_text):
                stripped = clean_text(line)
                if re.search(r'^Beneficiary Account Number$', stripped, re.IGNORECASE):
                    for j in range(idx + 1, min(idx + 5, len(slip_text))):
                        acc_match = re.match(r'^([\d\-]+)$', slip_text[j].strip())
                        if acc_match:
                            account_no_on_slip = acc_match.group(1)
                            break
                    break

        if not account_no_on_slip:
            acc_patterns = [
                r'To Account Number\s+(\S+)',
                r'Account No\.?\s+\(?(\d{5,})\)?',
                r'Beneficiary Account Number.*?(\d{5,})',
            ]
            for pattern in acc_patterns:
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    account_no_on_slip = match.group(1).strip('()')
                    break

        if not account_no_on_slip:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Cant read acc no on the slip'})
            return result

        # --- Extract Date & Time ---
        if not date_time_on_slip:
            datetime_formats = [
                (r'Transaction Date\s*[&]\s*Time\s+(\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)', '%d-%m-%Y %I:%M %p'),
                (r'(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)', '%d/%m/%Y %I:%M %p'),
                (r'(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})(?!\s*[AP])', '%d/%m/%Y %H:%M'),
                (r'(\d{4}/\d{2}/\d{2}\s+\d{1,2}:\d{2}\s*[AP]M)', '%Y/%m/%d %I:%M %p'),
            ]

            for pattern, fmt in datetime_formats:
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    raw_date_time = match.group(1).strip('. ')
                    try:
                        date_time_on_slip = datetime.strptime(raw_date_time, fmt)
                        break
                    except ValueError:
                        continue

        if not date_time_on_slip:
            for idx, line in enumerate(slip_text):
                if re.search(r'Date[\s\-/]*Time', clean_text(line), re.IGNORECASE):
                    for j in range(idx + 1, min(idx + 5, len(slip_text))):
                        dt_line = slip_text[j].strip()
                        for fmt in ['%d/%m/%Y %H:%M', '%d/%m/%Y %I:%M %p', '%d-%m-%Y %I:%M %p', '%Y/%m/%d %I:%M %p']:
                            try:
                                date_time_on_slip = datetime.strptime(dt_line, fmt)
                                break
                            except ValueError:
                                continue
                        if date_time_on_slip:
                            break
                    break

        # --- Non-column extractions for Sender, Beneficiary, Remarks, Fee, Total, Printed Date, Status ---
        if not sender_name_on_slip:
            for idx, line in enumerate(slip_text):
                clean_l = clean_text(line)
                m = re.search(r"Sender['\u2019]?s?\s*Name[:\s]*(.*)$", clean_l, re.I)
                if m and m.group(1).strip():
                    sender_name_on_slip = m.group(1).strip().strip(':-,')
                    break
                elif re.search(r"^Sender['\u2019]?s?\s*Name$", clean_l, re.I) and idx + 1 < len(slip_text):
                    sender_name_on_slip = clean_text(slip_text[idx + 1]).strip(':-,')
                    break

        if not beneficiary_name_on_slip:
            for idx, line in enumerate(slip_text):
                clean_l = clean_text(line)
                m = re.search(r"(?:To\s*Account\s*Name|Beneficiary\s*Name)[:\s]*(.*)$", clean_l, re.I)
                if m and m.group(1).strip():
                    beneficiary_name_on_slip = m.group(1).strip().strip(':-,')
                    break
                elif re.search(r"^(?:To\s*Account\s*Name|Beneficiary\s*Name)$", clean_l, re.I) and idx + 1 < len(slip_text):
                    beneficiary_name_on_slip = clean_text(slip_text[idx + 1]).strip(':-,')
                    break

        if not remarks_on_slip:
            for line in slip_text:
                m = re.search(r"(?:Remarks|Purpose(?:\s*of\s*Payment)?|Narration)[:\s]*(.*)$", clean_text(line), re.I)
                if m and m.group(1).strip():
                    remarks_on_slip = m.group(1).strip().strip(':-,')
                    break

        if not status_on_slip:
            for line in slip_text:
                m = re.search(r"Transaction\s*Status[:\s]*([A-Za-z]+)", clean_text(line), re.I)
                if m:
                    status_on_slip = m.group(1).strip()
                    break

        if not fee_on_slip:
            m = re.search(r"(?:Charges?\s*(?:&|and)?\s*Fees?|Fee|Convenience\s*Fe?e?)\s*(?:LKR|Rs\.?|KR|LR)?\s*([\d,]+\.?\d*)", full_text, re.I)
            if m:
                fee_on_slip = m.group(1)

        if not total_amount_on_slip:
            m = re.search(r"(?:Total\s*Amount|Charge\s*Amount)\s*(?:LKR|Rs\.?|KR|LR)?\s*([\d,]+\.?\d*)", full_text, re.I)
            if m:
                total_amount_on_slip = m.group(1)

        if not printed_date_on_slip:
            m = re.search(r"Printed\s*on[:\s]*(\d{2}[-/]\d{2}[-/]\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)", full_text, re.I)
            if m:
                printed_date_on_slip = parse_datetime(m.group(1))

        # Save extracted fields to result
        result['extracted_data'] = {
            'amount': amount_on_slip,
            'transaction_id': transaction_id_on_slip,
            'account_no': account_no_on_slip,
            'date_time': date_time_on_slip.strftime('%Y-%m-%d %H:%M:%S') if date_time_on_slip else None,
            'sender_name': sender_name_on_slip,
            'beneficiary_name': beneficiary_name_on_slip,
            'remarks': remarks_on_slip,
            'fee': fee_on_slip,
            'total_amount': total_amount_on_slip,
            'status_on_slip': status_on_slip,
        }

        # --- Check Required Fields ---
        if not amount_on_slip:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'missing amount'})
            return result
        if not transaction_id_on_slip:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'missing transaction reference'})
            return result
        if not account_no_on_slip:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'missing account number'})
            return result
        if not date_time_on_slip:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'missing date'})
            return result

        # --- 1. Tampered Receipt Digest Checks ---
        if fee_on_slip and total_amount_on_slip:
            amt = parse_amount(amount_on_slip) or 0.0
            f = parse_amount(fee_on_slip) or 0.0
            tot = parse_amount(total_amount_on_slip) or 0.0
            if abs((amt + f) - tot) > 0.05:
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Tampered receipt digest'})
                return result

        if printed_date_on_slip and date_time_on_slip:
            if printed_date_on_slip < date_time_on_slip - timedelta(minutes=5):
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Tampered receipt digest'})
                return result

        if date_time_on_slip > datetime.now() + timedelta(days=1):
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Tampered receipt digest'})
            return result

        # --- 2. Repeated Submission / Reused Payments ---
        slipHash = get_image_hash(slip_path)
        result['slip_hash'] = slipHash
        previous_slip_hashes = get_previous_hashes()
        previous_transaction_ids = get_previous_payments()

        if slipHash in previous_slip_hashes:
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Repeated submission (Duplicate payment)'})
            return result

        if transaction_id_on_slip in previous_transaction_ids:
            if slipHash not in previous_slip_hashes:
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Same payment, different image'})
            else:
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Repeated submission (Reused payment)'})
            return result

        # --- 3. Order Details Retrieval & Validation ---
        clean_order_id = str(order_id).strip()
        digits_id = re.sub(r'\D', '', clean_order_id)

        conn_db = sqlite3.connect(messages_database)
        cursor_db = conn_db.cursor()

        cursor_db.execute("PRAGMA table_info(orderDetails)")
        col_names = [col[1] for col in cursor_db.fetchall()]

        query_cols = [c for c in ["id", "account", "amount", "datetime", "status", "customer_name", "reference", "expires_at"] if c in col_names]
        query_str = f"SELECT {', '.join(query_cols)} FROM orderDetails WHERE id = ?"
        cursor_db.execute(query_str, (clean_order_id,))
        order_res = cursor_db.fetchone()
        if not order_res and digits_id:
            cursor_db.execute(query_str, (digits_id,))
            order_res = cursor_db.fetchone()

        if not order_res:
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Order not found'})
            return result

        order_dict = dict(zip(query_cols, order_res))
        order_db_id = order_dict.get("id")
        Amount = order_dict.get("amount")
        Account = order_dict.get("account")
        order_datetime = order_dict.get("datetime")
        order_status = order_dict.get("status", "pending")
        order_customer = order_dict.get("customer_name")
        order_reference = order_dict.get("reference")
        order_expires_at = order_dict.get("expires_at")

        # Prevent double payment on already approved order
        if order_status and str(order_status).lower() == "approved":
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Repeated submission: Order already approved'})
            return result

        # --- 4. Wrong Amount ---
        if parse_amount(Amount) != parse_amount(amount_on_slip) or parse_amount(amount_on_slip) <= 0:
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Wrong amount'})
            return result

        # --- 5. Wrong Account ---
        clean_account_req = re.sub(r'\D', '', str(Account))
        clean_account_slip = re.sub(r'\D', '', str(account_no_on_slip))
        if clean_account_req != clean_account_slip:
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Wrong account'})
            return result

        # --- 6. Wrong Customer ---
        if order_customer:
            if sender_name_on_slip and not names_match(order_customer, sender_name_on_slip):
                conn_db.close()
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Wrong customer'})
                return result
            if remarks_on_slip and not names_match(order_customer, remarks_on_slip):
                cursor_db.execute("SELECT customer_name FROM orderDetails WHERE id != ? AND customer_name IS NOT NULL", (order_db_id,))
                for (other_cust,) in cursor_db.fetchall():
                    if other_cust and names_match(other_cust, remarks_on_slip):
                        conn_db.close()
                        result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Wrong customer'})
                        return result

        # --- 7. Other Order Association & Suspicious References ---
        if remarks_on_slip:
            other_order_match = re.search(r'(?:ORDER|ORD)[-_]?(\d+)', remarks_on_slip, re.I)
            if other_order_match:
                found_num = other_order_match.group(1).lstrip('0') or '0'
                req_num = str(digits_id).lstrip('0') or '0'
                if found_num != req_num:
                    conn_db.close()
                    result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Other order association'})
                    return result

            cursor_db.execute("SELECT id, reference FROM orderDetails WHERE id != ?", (order_db_id,))
            for o_id, o_ref in cursor_db.fetchall():
                if o_ref and str(o_ref).strip().lower() == remarks_on_slip.strip().lower():
                    conn_db.close()
                    result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Other order association'})
                    return result

        if transaction_id_on_slip.lower() in ['reference not shown', 'n/a', 'none', 'null', '000000', '123456']:
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Suspicious reference'})
            return result

        # --- 8. Expired Order & Old Transactions ---
        if date_time_on_slip.year < 2024:
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Old payment'})
            return result

        order_dt = parse_datetime(order_datetime)
        if order_dt:
            if date_time_on_slip < order_dt - timedelta(minutes=30):
                conn_db.close()
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Expired order'})
                return result

        if order_expires_at:
            exp_dt = parse_datetime(order_expires_at)
            if exp_dt and date_time_on_slip > exp_dt:
                conn_db.close()
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Expired order'})
                return result

        # --- 9. SMS Verification ---
        matching_sms = None
        for msg in messages:
            ref_match = re.search(r'Ref:\s*([^\s.]+)', msg)
            if ref_match and ref_match.group(1).replace(' ', '') == transaction_id_on_slip.replace(' ', ''):
                matching_sms = msg
                break

        if not matching_sms:
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Missing evidence'})
            return result

        # SMS Amount check
        sms_amt_match = re.search(r'(?:Amount of|LKR|Rs\.?)\s*([\d,]+\.?\d*)', matching_sms)
        if sms_amt_match:
            sms_amount = parse_amount(sms_amt_match.group(1))
            if sms_amount != parse_amount(amount_on_slip):
                conn_db.close()
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Conflicting evidence'})
                return result

        # SMS Date check
        sms_date_match = re.search(r'(\d{2})[-/](\d{2})[-/](\d{4})', matching_sms)
        if sms_date_match:
            sms_d, sms_m, sms_y = int(sms_date_match.group(1)), int(sms_date_match.group(2)), int(sms_date_match.group(3))
            slip_y, slip_m, slip_d = date_time_on_slip.year, date_time_on_slip.month, date_time_on_slip.day
            if slip_y != sms_y or not ((slip_d == sms_d and slip_m == sms_m) or (slip_d == sms_m and slip_m == sms_d)):
                conn_db.close()
                result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Conflicting date'})
                return result

        # SMS Sender check
        if order_customer:
            sms_sender_match = re.search(r'from\s+([A-Z\s.]+?)(?=\s+on|\s+Ref|\.|\Z)', matching_sms)
            if sms_sender_match:
                sms_sender = sms_sender_match.group(1).strip()
                if not names_match(order_customer, sms_sender):
                    conn_db.close()
                    result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Wrong customer'})
                    return result

        # --- 10. Transaction Status Failure Check ---
        if status_on_slip and status_on_slip.upper() in ['FAILED', 'DECLINED', 'REJECTED', 'PENDING', 'CANCELLED']:
            conn_db.close()
            result.update({'status': 'NEEDS VERIFICATION', 'reason': 'Transaction not successful'})
            return result

        # --- 11. Normal Payment -> Approve and Record ---
        update_previous_payments(transaction_id_on_slip, str(amount_on_slip), str(date_time_on_slip), slipHash)
        cursor_db.execute("UPDATE orderDetails SET status = 'approved' WHERE id = ?", (order_db_id,))
        conn_db.commit()
        conn_db.close()

        result.update({'status': 'approved', 'reason': ''})
        return result

    except Exception as e:
        result.update({'status': 'ERROR', 'reason': str(e)})
        return result


# ─── Flask Routes ─────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    conn = get_db_connection()
    orders_raw = conn.execute("SELECT * FROM orderDetails ORDER BY id DESC").fetchall()
    payments_raw = conn.execute("SELECT * FROM previousPayments ORDER BY ID DESC").fetchall()
    conn.close()

    # Convert sqlite3.Row to dicts with template-friendly keys
    orders = []
    for o in orders_raw:
        orders.append({
            'id': o['id'],
            'account_no': o['account'],
            'amount': o['amount'],
            'date_time': o['datetime'],
            'status': o['status'],
            'customer_name': o['customer_name'] or '',
            'reference': o['reference'] or '',
        })

    payments = []
    for p in payments_raw:
        payments.append({
            'transaction_id': p['transactionID'],
            'amount': p['amount'],
            'date_time': p['datetime'],
            'slip_hash': p['slipHash'] or '',
        })

    return render_template('dashboard.html', orders=orders, payments=payments)


@app.route('/verify', methods=['GET', 'POST'])
def verify():
    # Get list of available slips
    slips = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        slips = sorted([f for f in os.listdir(app.config['UPLOAD_FOLDER'])
                       if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff'))])

    # Get non-approved orders for the dropdown
    conn = get_db_connection()
    orders_raw = conn.execute(
        "SELECT * FROM orderDetails WHERE status != 'approved' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    pending_orders = []
    for o in orders_raw:
        pending_orders.append({
            'id': o['id'],
            'account_no': o['account'],
            'amount': o['amount'],
            'customer_name': o['customer_name'] or '',
        })

    if request.method == 'GET':
        return render_template('verify.html', slips=slips, pending_orders=pending_orders, result=None)

    # POST - process verification
    order_id = request.form.get('order_id', '').strip()
    slip_file = request.files.get('slip_image')
    existing_slip = request.form.get('existing_slip', '').strip()

    slip_path = None
    if slip_file and slip_file.filename:
        filename = secure_filename(slip_file.filename)
        slip_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        slip_file.save(slip_path)
    elif existing_slip:
        slip_path = os.path.join(app.config['UPLOAD_FOLDER'], existing_slip)

    if not slip_path or not order_id:
        flash('Please provide both a bank slip and an Order ID.', 'danger')
        return render_template('verify.html', slips=slips, pending_orders=pending_orders, result=None)

    result = verify_slip(slip_path, order_id)
    return render_template('verify.html', slips=slips, pending_orders=pending_orders, result=result)


@app.route('/messages', methods=['GET', 'POST'])
def messages_page():
    if request.method == 'POST':
        content = None
        if request.is_json:
            content = request.json.get('content', '').strip()
        else:
            content = request.form.get('content', '').strip()

        if content:
            update_messages(content)
            if request.is_json:
                return jsonify({'status': 'success', 'message': 'Message saved to database'})
            flash('SMS message added successfully!', 'success')
        else:
            if request.is_json:
                return jsonify({'error': 'Message content cannot be empty'}), 400
            flash('Message content cannot be empty.', 'danger')
        return redirect(url_for('messages_page'))

    conn = get_db_connection()
    msgs = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
    conn.close()

    messages_list = []
    for m in msgs:
        messages_list.append({
            'id': m['id'],
            'content': m['content'],
        })

    return render_template('messages.html', messages=messages_list)


@app.route('/add-order', methods=['GET', 'POST'])
def add_order():
    if request.method == 'POST':
        account_no = request.form.get('account_no', '').strip()
        amount = request.form.get('amount', '').strip()
        date_time = request.form.get('date_time', '').strip()
        customer_name = request.form.get('customer_name', '').strip()
        reference = request.form.get('reference', '').strip()
        expires_at = request.form.get('expires_at', '').strip()

        if not account_no or not amount:
            flash('Account Number and Amount are required.', 'danger')
            return render_template('add_order.html')

        # Format datetime from HTML datetime-local input (YYYY-MM-DDTHH:MM) to dd-mm-YYYY HH:MM
        if date_time:
            try:
                dt = datetime.strptime(date_time, '%Y-%m-%dT%H:%M')
                date_time = dt.strftime('%d-%m-%Y %I:%M %p')
            except ValueError:
                pass

        if expires_at:
            try:
                exp = datetime.strptime(expires_at, '%Y-%m-%dT%H:%M')
                expires_at = exp.strftime('%d-%m-%Y %I:%M %p')
            except ValueError:
                pass

        conn = get_db_connection()
        conn.execute(
            "INSERT INTO orderDetails (account, amount, datetime, status, customer_name, reference, expires_at) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (account_no, amount, date_time or None, customer_name or None, reference or None, expires_at or None)
        )
        conn.commit()
        conn.close()

        flash('Order created successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('add_order.html')


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.route('/api/orders', methods=['GET'])
def api_orders():
    conn = get_db_connection()
    orders = conn.execute("SELECT * FROM orderDetails").fetchall()
    conn.close()
    return jsonify([dict(o) for o in orders])


@app.route('/api/orders/<int:id>', methods=['GET'])
def api_order(id):
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orderDetails WHERE id = ?", (id,)).fetchone()
    conn.close()
    if order:
        return jsonify(dict(order))
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/slips', methods=['GET'])
def api_slips():
    slips = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        slips = sorted(os.listdir(app.config['UPLOAD_FOLDER']))
    return jsonify(slips)


if __name__ == '__main__':
    print("\n  SecurePay Web UI")
    print("  --------------------------------")
    print("  Running at: http://127.0.0.1:5000")
    print("  Press Ctrl+C to quit\n")
    app.run(debug=True, port=5000)
