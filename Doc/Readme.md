python -m venv .venv
.\.venv\Scripts\Activate.ps1

1) เปิด venv (ถ้ามี)
cd E:\FinTech\pinebitz
.\.venv\Scripts\Activate.ps1

2) รันเซิร์ฟเวอร์
uvicorn pinebitz.api.app:app --host 127.0.0.1 --port 8000 --reload
pinebitz.api.app = ไฟล์โมดูล
:app = ตัวแปร app ในไฟนั้น (FastAPI)

3) เปิดหน้าเว็บ

Start-Process "http://127.0.0.1:8000/dashboard"

