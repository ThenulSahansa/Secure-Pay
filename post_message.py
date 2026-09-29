import sys
import os
import urllib.request
import urllib.parse
import json
import sqlite3

# Ensure API module can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from API import update_messages

SERVER_URL = "http://127.0.0.1:5000/messages"

def post_message(message_content, server_url=SERVER_URL):
    """
    Posts a bank SMS message to the web application endpoint
    and saves it to the SQLite database.
    """
    message_content = message_content.strip()
    if not message_content:
        print("[-] Error: Message content cannot be empty.")
        return False

    posted_to_server = False

    # 1. Try sending HTTP POST to running Flask server
    try:
        data = urllib.parse.urlencode({'content': message_content}).encode('utf-8')
        req = urllib.request.Request(
            server_url,
            data=data,
            headers={'User-Agent': 'SecurePayClient/1.0', 'Content-Type': 'application/x-www-form-urlencoded'}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status in [200, 302]:
                print(f"[+] Posted message to Flask server ({server_url}).")
                posted_to_server = True
    except Exception as e:
        print(f"[*] Note: Could not reach web server at {server_url} ({e}).")

    # 2. Directly update local SQLite databases to guarantee persistence
    db_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "SecurePayDataBase.db"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "SecurePayDataBase.db")
    ]

    saved_db_count = 0
    for db_path in set(db_paths):
        if not os.path.exists(db_path):
            continue
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM messages ORDER BY id DESC LIMIT 1;")
                res = cursor.fetchall()
                last_id = int(res[0][0]) if res else 0

                sql_query = "INSERT INTO messages (id, content) VALUES (?, ?)"
                cursor.execute(sql_query, (str(last_id + 1), message_content))
                conn.commit()
                print(f"[+] Saved message to database '{db_path}' (Assigned ID: {last_id + 1})")
                saved_db_count += 1
        except Exception as e:
            print(f"[-] Failed to update database '{db_path}': {e}")

    if posted_to_server or saved_db_count > 0:
        print(f"\n[=] Success! Message saved and ready for payment verification.")
        return True
    else:
        print(f"[-] Failed to save message.")
        return False

if __name__ == '__main__':
    if len(sys.argv) > 1:
        msg = " ".join(sys.argv[1:])
    else:
        print("--- SecurePay SMS Poster ---")
        msg = input("Enter SMS Message Content: ").strip()

    post_message(msg)
