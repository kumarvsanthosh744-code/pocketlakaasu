import os
import sqlite3
from datetime import date, datetime
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, "index.html")
DB_PATH = os.path.join(BASE_DIR, "fintrack.db")

app = FastAPI(title="Expense Tracker Full-Stack App")


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                monthly_income REAL NOT NULL DEFAULT 6000.0,
                savings_goal REAL NOT NULL DEFAULT 1500.0
            );
        """)
        conn.execute("""
            INSERT OR IGNORE INTO settings (id, monthly_income, savings_goal)
            VALUES (1, 6000.0, 1500.0);
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                exp_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)


init_db()


def sanitize_text(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    if cleaned and cleaned[0] in ("=", "+", "-", "@", "\t", "\r"):
        cleaned = "'" + cleaned
    return cleaned[:80]


class ExpenseIn(BaseModel):
    item: str = Field(..., min_length=1, max_length=80)
    category: str = Field(..., min_length=1, max_length=60)
    amount: float = Field(..., gt=0)
    exp_date: str | None = None


class SettingsIn(BaseModel):
    monthly_income: float = Field(..., ge=100)
    savings_goal: float = Field(..., ge=0)


@app.get("/")
def serve_frontend():
    if not os.path.exists(HTML_PATH):
        raise HTTPException(status_code=404, detail="index.html not found.")
    return FileResponse(HTML_PATH, media_type="text/html")


@app.get("/api/state")
def get_state():
    with get_db() as conn:
        cfg = conn.execute("SELECT monthly_income, savings_goal FROM settings WHERE id = 1").fetchone()
        rows = conn.execute(
            "SELECT id, item, category, amount, exp_date FROM expenses ORDER BY exp_date DESC, id DESC"
        ).fetchall()
    return {
        "income": cfg["monthly_income"],
        "goal": cfg["savings_goal"],
        "expenses": [dict(r) for r in rows],
    }


@app.put("/api/settings")
def update_settings(payload: SettingsIn):
    safe_goal = min(payload.savings_goal, payload.monthly_income)
    with get_db() as conn:
        conn.execute(
            "UPDATE settings SET monthly_income = ?, savings_goal = ? WHERE id = 1",
            (float(payload.monthly_income), float(safe_goal)),
        )
    return {"ok": True, "income": float(payload.monthly_income), "goal": float(safe_goal)}


@app.post("/api/expenses")
def create_expense(payload: ExpenseIn):
    clean_item = sanitize_text(payload.item)
    if not clean_item:
        raise HTTPException(status_code=400, detail="Invalid item name.")

    exp_str = payload.exp_date or date.today().isoformat()
    try:
        datetime.strptime(exp_str, "%Y-%m-%d")
    except ValueError:
        exp_str = date.today().isoformat()

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO expenses (item, category, amount, exp_date, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                clean_item,
                payload.category[:60],
                round(float(payload.amount), 2),
                exp_str,
                datetime.utcnow().isoformat(),
            ),
        )
        new_id = cur.lastrowid
    return JSONResponse(status_code=201, content={"id": new_id, "ok": True})


@app.delete("/api/expenses/{expense_id}")
def delete_expense(expense_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    return {"ok": True}
    from fastapi import Header, HTTPException

def require_device_id(x_device_id: str | None = Header(default=None)) -> str:
    if not x_device_id or not x_device_id.strip():
        raise HTTPException(status_code=400, detail="Missing X-Device-Id header")
    return x_device_id.strip()