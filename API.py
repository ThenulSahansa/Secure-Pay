import hashlib
from PIL import Image, ImageFilter, ImageStat
import pytesseract
import sqlite3


# --- WINDOWS USERS ONLY ---
# Un-comment the line below and point it to your actual tesseract.exe path
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
# --------------------------

import re
import os

def resolve_slip_path(path):
    if not path:
        return path
    path = str(path).strip().strip('"\'')
    if os.path.exists(path):
        return path

    norm = path.replace('\\', '/')
    base = os.path.basename(norm)
    no_space_base = base.replace(' ', '')
    no_ext_base = os.path.splitext(no_space_base)[0]

    candidates = [
        path,
        path + '.jpg',
        path + '.png',
        path + '.jpeg',
        os.path.splitext(path)[0] + '.jpg',
        os.path.splitext(path)[0] + '.png',
        os.path.join('bank_slips', base),
        os.path.join('bank_slips', base + '.jpg'),
        os.path.join('bank_slips', base + '.png'),
        os.path.join('bank_slips', no_space_base),
        os.path.join('bank_slips', no_space_base + '.jpg'),
        os.path.join('bank_slips', no_space_base + '.png'),
        os.path.join('bank_slips', no_ext_base + '.jpg'),
        os.path.join('bank_slips', no_ext_base + '.png'),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return path

def parse_amount(val):
    if val is None:
        return None
    try:
        return float(re.sub(r'[^\d.]', '', str(val)))
    except (ValueError, TypeError):
        return None

def get_image_hash(image_path):
    image_path = resolve_slip_path(image_path)
    sha256 = hashlib.sha256()
    with open(image_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

get_sha256 = get_image_hash
get_file_hash = get_image_hash

class SlipQualityResult(str):
    def __new__(cls, is_difficult: bool, detail: str = ""):
        message = "The slip is blurry, cropped, dark, low-resolution, or otherwise difficult to read" if is_difficult else ""
        obj = str.__new__(cls, message)
        obj.is_difficult = is_difficult
        obj.detail = detail
        return obj

    def __bool__(self):
        return self.is_difficult

    def __iter__(self):
        return iter((self.is_difficult, self.detail if self.is_difficult else "Clear and readable"))

def is_slip_difficult_to_read(image_path):
    image_path = resolve_slip_path(image_path)
    try:
        img = Image.open(image_path)
    except Exception as e:
        return SlipQualityResult(True, f"Cannot open image: {e}")

    w, h = img.size

    # 1. Cropped / abnormal aspect ratio
    aspect = w / h
    if aspect > 2.5 or aspect < 0.28:
        return SlipQualityResult(True, f"The slip is cropped (aspect ratio {aspect:.2f})")

    # 2. Low-resolution
    if w < 200 or h < 200 or (w * h) < 80000:
        return SlipQualityResult(True, f"The slip is low-resolution ({w}x{h})")

    # 3. Dark or low contrast
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    mean_brightness = stat.mean[0]
    std_dev = stat.stddev[0]

    if mean_brightness < 65:
        return SlipQualityResult(True, f"The slip is dark (brightness {mean_brightness:.1f})")
    if std_dev < 15:
        return SlipQualityResult(True, f"The slip has poor contrast (stddev {std_dev:.1f})")

    # 4. Blurry (Laplacian and edge variance)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    laplacian = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], 1, 0))
    edge_var = ImageStat.Stat(edges).var[0]
    lap_var = ImageStat.Stat(laplacian).var[0]

    if edge_var < 750 and lap_var < 750:
        return SlipQualityResult(True, f"The slip is blurry (edge_var={edge_var:.1f}, lap_var={lap_var:.1f})")

    # 5. Otherwise difficult to read (OCR text quality and confidence)
    try:
        data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
        words = [data["text"][i].strip() for i in range(len(data["text"])) if data["text"][i].strip()]
        confs = [int(c) for c in data["conf"] if int(c) != -1]
        avg_conf = sum(confs) / len(confs) if confs else 0
        full_text = " ".join(words).lower()

        if len(words) < 5:
            return SlipQualityResult(True, f"The slip is difficult to read (insufficient text recognized: {len(words)} words)")
        if avg_conf < 45:
            return SlipQualityResult(True, f"The slip is difficult to read (low OCR confidence: {avg_conf:.1f}%)")

        keywords = ["amount", "acc", "date", "ref", "trans", "lkr", "rs", "fee", "total", "pay", "bank", "biller", "slip", "success"]
        matched_keywords = [k for k in keywords if k in full_text]
        if len(matched_keywords) < 2:
            return SlipQualityResult(True, f"The slip is difficult to read (missing slip keywords, found {matched_keywords})")
    except Exception as e:
        return SlipQualityResult(True, f"The slip is difficult to read (OCR error: {e})")

    return SlipQualityResult(False, "Clear and readable")

# Aliases for convenience
is_difficult_to_read = is_slip_difficult_to_read
check_slip_quality = is_slip_difficult_to_read
detect_slip_issue = is_slip_difficult_to_read
detect_slip_issues = is_slip_difficult_to_read
is_blurry_or_difficult = is_slip_difficult_to_read

def extract_text(image_path):
    image_path = resolve_slip_path(image_path)
    try:
        # Open the image using Pillow
        img = Image.open(image_path)
        
        # Extract text directly from the image
        text = pytesseract.image_to_string(img)
        
        return text.split("\n")
    except Exception as e:
        return f"An error occurred: {e}"

def update_messages(message):
    with sqlite3.connect("./SecurePayDataBase.db") as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT id from messages ORDER BY id DESC LIMIT 1;")
        last_id_query = int(cursor.fetchall()[0][0])
        sqlQuery = """
        INSERT INTO messages (id, content)
        VALUES (?, ?)
        """
        cursor.execute(sqlQuery, (str(last_id_query + 1), message))
        conn.commit()

        print("Message added to the database")

def update_previous_payments(transactionID, amount, datetime, slipHash=None):
    with sqlite3.connect("./SecurePayDataBase.db") as conn:
        cursor = conn.cursor()
        # query = """
        #     CREATE TABLE IF NOT EXISTS previousPayments (
        #     ID int PRIMARY KEY,
        #     transactionID VARCHAR(100) UNIQUE,
        #     amount VARCHAR(50) NOT NULL,
        #     datetime DATE,
        #     slipHash VARCHAR(64)
        #     )
        # """
        cursor.execute("SELECT ID from previousPayments ORDER BY ID DESC LIMIT 1;")
        result = cursor.fetchall()
        last_id_query = int(result[0][0]) if result else 0

        query = """
        INSERT INTO previousPayments (ID, transactionID, amount, datetime, slipHash)
        VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(query, (str(last_id_query + 1), transactionID, amount, datetime, slipHash))
        conn.commit()

def get_previous_payments():
    with sqlite3.connect("./SecurePayDataBase.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT transactionID from previousPayments")
        previous_transaction_ids = cursor.fetchall()
        return [j for i in previous_transaction_ids for j in i]

def get_previous_hashes():
    with sqlite3.connect("./SecurePayDataBase.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT slipHash from previousPayments")
        previous_hashes = cursor.fetchall()
        return [j for i in previous_hashes for j in i]



if __name__ == "__main__":
    print("Previous Transaction IDs:", get_previous_payments())
    print("Slip 1 SHA256:", get_image_hash("./bank_slips/slip1.png"))
