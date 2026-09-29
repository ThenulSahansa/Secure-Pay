# SecurePay - Automated Bank Slip & Payment Verification System

SecurePay is a web application and verification pipeline designed to detect payment fraud and automate bank transfer slip verification. It pairs OCR image extraction with multi-rule cross-referencing against bank SMS notifications and recorded orders to prevent forged slips, duplicate transactions, expired orders, and payment mismatches.

---

## Features

- **Automated Slip OCR & Extraction**:
  - Leverages Tesseract OCR and Pillow to extract critical fields from uploaded transaction slips (Amount, Reference / Transaction ID, Beneficiary Account Number, Timestamp, Sender/Beneficiary Names, Fees, and Status).
  - Handles single-column, key-value, and multi-line slip formats.

- **Image Quality & Tamper Diagnostics**:
  - Checks for abnormal aspect ratios (cropped slips), low-resolution images, dark/low-contrast photos, and blurriness using Laplacian & edge variance filters.
  - Verifies slip arithmetic integrity (Fee + Transfer Amount == Total Amount) and detects conflicting timestamps (e.g. printed date preceding transfer date).

- **11-Stage Fraud & Verification Engine**:
  1. **Image Quality & OCR Readability**: Ensures slip text and essential fields are legible.
  2. **Tampered Receipt Digest**: Detects mathematical or chronological discrepancies on the slip.
  3. **Duplicate / Reused Payment Detection**: Uses SHA-256 image hashing and transaction ID tracking against previously approved payments (`previousPayments`).
  4. **Amount Verification**: Compares extracted slip amount with order amount.
  5. **Account Verification**: Matches beneficiary account numbers, stripping punctuation/spaces.
  6. **Customer Name Matching**: Verifies sender and remarks against order customer name with title-stripping and token matching.
  7. **Cross-Order Association**: Flags slips referencing other orders or suspicious placeholder references (`000000`, `N/A`, etc.).
  8. **Order Expiry / Historical Validation**: Checks order creation and expiration timestamps against transaction timestamps.
  9. **Bank SMS Cross-Verification**: Validates transaction reference, amount, date, and sender name against bank SMS notifications stored in SQLite.
  10. **Transaction Status Confirmation**: Flags slips with failed, declined, or cancelled statuses.
  11. **Auto-Approval**: Updates order status to `approved` and logs the transaction record upon passing all checks.

- **Bank SMS Extraction & Ingestion Pipeline**:
  - **Direct Android ADB**: Queries `content://sms/inbox` for incoming bank transaction SMS messages.
  - **Backup & File Ingestion**: Ingests SMS messages from XML exports (SMS Backup & Restore), JSON, or plain text log files.
  - **CLI / Web Ingestion**: Fast ingestion utilities (`messages.py`, `post_message.py`) and dedicated web UI endpoint (`/messages`).

- **Interactive Web Interface**:
  - Built with Flask and modern HTML templates.
  - **Dashboard (`/`)**: View all orders, pending verifications, and approved payments.
  - **Verify (`/verify`)**: Upload or pick existing bank slip images and verify against pending orders with detailed diagnostic output and raw OCR inspections.
  - **Messages (`/messages`)**: Manage and review ingested bank SMS messages.
  - **Add Order (`/add-order`)**: Create new orders with account numbers, amounts, customer names, references, and expiry dates.
  - **REST API Endpoints**: Fetch orders and bank slips programmatically (`/api/orders`, `/api/orders/<id>`, `/api/slips`).

---

## Project Structure

```text
.
├── API.py                 # Core OCR, image hashing, quality diagnostics & database helpers
├── app.py                 # Flask server, route controllers & 11-step verification engine
├── messages.py            # Automated bank SMS extraction (ADB, XML, JSON, TXT)
├── post_message.py        # Utility script to post SMS messages to server and database
├── SecurePayDataBase.db   # SQLite database storing orders, messages, and payments
├── bank_slips/            # Directory containing uploaded and test bank slip images
├── templates/             # Jinja2 templates for the web application
│   ├── base.html          # Base layout and navigation
│   ├── dashboard.html     # Orders & payments overview
│   ├── verify.html        # Slip verification portal with OCR inspection
│   ├── messages.html      # Bank SMS message inbox
│   └── add_order.html     # Manual order creation form
└── README.md
```

---

## Prerequisites

1. **Python**: Python 3.8 or higher.
2. **Tesseract OCR**:
   - **Windows**: Install [Tesseract-OCR](https://github.com/UB-Mannheim/tesseract/wiki).
     - Default expected path: `C:\Program Files\Tesseract-OCR\tesseract.exe` (update in `API.py` if installed elsewhere).
   - **Linux**:
     ```bash
     sudo apt-get install tesseract-ocr
     ```
   - **macOS**:
     ```bash
     brew install tesseract
     ```
3. **Android Debug Bridge (ADB)** *(Optional)*:
   - Required only if extracting bank SMS messages directly from a connected Android device.

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/ThenulSahansa/Secure-Pay.git
   cd Secure-Pay
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Windows (PowerShell)
   python -m venv venv
   .\venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install Flask Pillow pytesseract werkzeug
   ```

---

## Database Configuration

The application uses an SQLite database (`SecurePayDataBase.db`) containing three primary tables:

- **`orderDetails`**: Stores customer orders (`id`, `account`, `amount`, `datetime`, `status`, `customer_name`, `reference`, `expires_at`).
- **`messages`**: Stores bank SMS notification texts (`id`, `content`).
- **`previousPayments`**: Logs approved transactions to prevent duplicate usage (`ID`, `transactionID`, `amount`, `datetime`, `slipHash`).

---

## Usage

### 1. Running the Web Application

Start the Flask server:
```bash
python app.py
```

Access the interface in your browser:
- **Dashboard**: [http://127.0.0.1:5000/](http://127.0.0.1:5000/)
- **Verify Bank Slip**: [http://127.0.0.1:5000/verify](http://127.0.0.1:5000/verify)
- **SMS Inbox**: [http://127.0.0.1:5000/messages](http://127.0.0.1:5000/messages)
- **Add Order**: [http://127.0.0.1:5000/add-order](http://127.0.0.1:5000/add-order)

---

### 2. Ingesting Bank SMS Messages

Bank SMS messages serve as the source of truth for payment verification.

- **Auto-Extract from Android phone (via ADB)**:
  ```bash
  python messages.py
  ```

- **Extract from XML, JSON, or TXT file**:
  ```bash
  python messages.py path/to/sms_backup.xml
  python messages.py path/to/messages.json
  ```

- **Post a single message via CLI**:
  ```bash
  python post_message.py "Your A/C 123456 has been credited with LKR 5,000.00 on 28/09/2026. Ref: TXN987654321 from JOHN DOE."
  ```

---

### 3. API Reference

- **`GET /api/orders`**: Returns all orders in JSON format.
- **`GET /api/orders/<id>`**: Returns details for a specific order.
- **`GET /api/slips`**: Lists all available slip files in the `bank_slips/` directory.
- **`POST /messages`**: Accepts a JSON body `{"content": "<SMS_TEXT>"}` to record a new SMS notification.

---

## Verification Statuses

- **`approved`**: The slip passed all 11 integrity and matching checks. Order status is updated to `approved` and logged in `previousPayments`.
- **`NEEDS VERIFICATION`**: Slip failed one or more checks. A specific reason is returned (e.g., `Unclear image`, `Wrong amount`, `Wrong account`, `Repeated submission`, `Missing evidence`, `Conflicting date`).
- **`ERROR`**: Unexpected parsing or server exception.

---

## License

This project is licensed under the MIT License.
