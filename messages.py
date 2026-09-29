import os
import re
import sys
import json
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from API import update_messages

# Keywords used to identify bank transaction SMS messages
BANK_KEYWORDS = [
    r'\bcredited\b', r'\bdebited\b', r'\bLKR\b', r'\bRs\.?\b',
    r'\bRef:\b', r'\bRef\b', r'\bA/C\b', r'\bAccount\b', r'\bTransfer\b',
    r'\bpaid\b', r'\bdeposit\b', r'\bwithdrawal\b'
]

BANK_REGEX = re.compile('|'.join(BANK_KEYWORDS), re.IGNORECASE)

def is_bank_message(text):
    """Check if the text content matches bank transaction SMS patterns."""
    if not text:
        return False
    # Must have at least 2 bank keyword indicators (e.g. credited + Ref or LKR + credited)
    matches = BANK_REGEX.findall(text)
    return len(matches) >= 1 and (
        re.search(r'Ref:\s*\w+', text, re.IGNORECASE) or
        re.search(r'(?:LKR|Rs\.?|Amount of)\s*[\d,]+', text, re.IGNORECASE) or
        re.search(r'credited|debited', text, re.IGNORECASE)
    )

def get_existing_messages(db_path="./SecurePayDataBase.db"):
    """Fetch existing message contents from SQLite database to avoid duplicates."""
    if not os.path.exists(db_path):
        return set()
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT content FROM messages")
            rows = cursor.fetchall()
            return {row[0].strip() for row in rows if row[0]}
    except Exception as e:
        print(f"[-] Error reading existing database: {e}")
        return set()

def save_message_to_db(message_text, db_paths=None):
    """Save a bank SMS message to database if not already present."""
    if db_paths is None:
        db_paths = ["./SecurePayDataBase.db", "./server/SecurePayDataBase.db"]

    message_text = message_text.strip()
    if not message_text:
        return False

    saved_any = False
    for path in db_paths:
        if not os.path.exists(path):
            continue
        try:
            existing = get_existing_messages(path)
            if message_text in existing:
                continue

            with sqlite3.connect(path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM messages ORDER BY id DESC LIMIT 1;")
                res = cursor.fetchall()
                last_id = int(res[0][0]) if res else 0

                sql_query = "INSERT INTO messages (id, content) VALUES (?, ?)"
                cursor.execute(sql_query, (str(last_id + 1), message_text))
                conn.commit()
                print(f"[+] Saved message to {path} (ID: {last_id + 1})")
                saved_any = True
        except Exception as e:
            print(f"[-] Error saving to {path}: {e}")

    return saved_any

def extract_from_adb():
    """Extract SMS messages directly from connected Android device via ADB (Google Messages content provider)."""
    print("[*] Attempting ADB extraction from connected Android device...")
    try:
        cmd = ["adb", "shell", "content", "query", "--uri", "content://sms/inbox", "--projection", "address:body:date"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            print("[-] ADB query failed or no ADB device found.")
            return []

        messages = []
        body_pattern = re.compile(r'body=(.*?)(?:,\s*date=|\Z)', re.DOTALL)
        for line in result.stdout.splitlines():
            m = body_pattern.search(line)
            if m:
                body = m.group(1).strip()
                if is_bank_message(body):
                    messages.append(body)
        
        print(f"[+] Found {len(messages)} bank SMS messages via ADB.")
        return messages
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"[-] ADB extraction unavailable: {e}")
        return []

def extract_from_xml(xml_path):
    """Extract bank SMS messages from SMS Backup & Restore XML export."""
    print(f"[*] Extracting SMS from XML backup: {xml_path}")
    if not os.path.exists(xml_path):
        print(f"[-] File not found: {xml_path}")
        return []

    bank_messages = []
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for sms in root.findall('sms'):
            body = sms.attrib.get('body', '')
            if is_bank_message(body):
                bank_messages.append(body)
        print(f"[+] Found {len(bank_messages)} bank SMS messages in {xml_path}")
    except Exception as e:
        print(f"[-] Failed to parse XML {xml_path}: {e}")

    return bank_messages

def extract_from_json(json_path):
    """Extract bank SMS messages from a JSON file (e.g. messages.json)."""
    print(f"[*] Extracting SMS from JSON file: {json_path}")
    if not os.path.exists(json_path):
        print(f"[-] File not found: {json_path}")
        return []

    bank_messages = []
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, dict):
                items = data.values()
            elif isinstance(data, list):
                items = [item.get('body', item.get('content', str(item))) if isinstance(item, dict) else str(item) for item in data]
            else:
                items = []

            for text in items:
                if is_bank_message(text):
                    bank_messages.append(text)
        print(f"[+] Found {len(bank_messages)} bank SMS messages in {json_path}")
    except Exception as e:
        print(f"[-] Failed to parse JSON {json_path}: {e}")

    return bank_messages

def extract_from_txt(txt_path):
    """Extract bank SMS messages from a plain text file (one message per line)."""
    print(f"[*] Extracting SMS from TXT file: {txt_path}")
    if not os.path.exists(txt_path):
        print(f"[-] File not found: {txt_path}")
        return []

    bank_messages = []
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if is_bank_message(line):
                    bank_messages.append(line)
        print(f"[+] Found {len(bank_messages)} bank SMS messages in {txt_path}")
    except Exception as e:
        print(f"[-] Failed to read TXT {txt_path}: {e}")

    return bank_messages

def run_extractor(target_file=None):
    """Main extraction process routine."""
    extracted = []

    if target_file:
        ext = os.path.splitext(target_file)[1].lower()
        if ext == '.xml':
            extracted = extract_from_xml(target_file)
        elif ext == '.json':
            extracted = extract_from_json(target_file)
        elif ext in ['.txt', '.log']:
            extracted = extract_from_txt(target_file)
        else:
            print(f"[-] Unsupported file extension: {ext}")
    else:
        # Try automatic discovery
        # 1. Try ADB
        extracted = extract_from_adb()

        # 2. Try default json / xml files in directory
        if not extracted:
            for candidate in ["messages.json", "sms.xml", "sms_backup.xml", "sms.json"]:
                if os.path.exists(candidate):
                    if candidate.endswith('.json'):
                        extracted.extend(extract_from_json(candidate))
                    elif candidate.endswith('.xml'):
                        extracted.extend(extract_from_xml(candidate))

    if not extracted:
        print("[-] No bank SMS messages extracted.")
        return

    count = 0
    for msg in extracted:
        if save_message_to_db(msg):
            count += 1

    print(f"\n[=] Extraction finished. Added {count} new bank SMS message(s) to SecurePayDataBase.")

if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run_extractor(target)
