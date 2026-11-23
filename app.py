import os
import sqlite3
from datetime import date, datetime
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, "index.html")
DB_PATH = os.path.join(BASE_DIR, "fintrack.db")

app = FastAPI(title="PocketLakaasu Expense Tracker")


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(settings)").fetchall()]
        if cols and "device_id" not in cols:
            conn.execute("DROP TABLE settings")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                device_id TEXT PRIMARY KEY,
                monthly_income REAL NOT NULL DEFAULT 6000.0,
                savings_goal REAL NOT NULL DEFAULT 1500.0
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL DEFAULT 'legacy',
                item TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                exp_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

        exp_cols = [r["name"] for r in conn.execute("PRAGMA table_info(expenses)").fetchall()]
        if "device_id" not in exp_cols:
            conn.execute("ALTER TABLE expenses ADD COLUMN device_id TEXT NOT NULL DEFAULT 'legacy'")


init_db()


def require_device_id(x_device_id: str | None = Header(default=None)) -> str:
    if not x_device_id or not x_device_id.strip():
        raise HTTPException(status_code=400, detail="Missing X-Device-Id header")
    return x_device_id.strip()[:120]


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


class DeviceBackupIn(BaseModel):
    monthly_income: float = Field(default=6000.0, ge=100)
    savings_goal: float = Field(default=1500.0, ge=0)
    expenses: list[ExpenseIn] = Field(default_factory=list)


@app.get("/")
def serve_frontend():
    if not os.path.exists(HTML_PATH):
        raise HTTPException(status_code=404, detail="index.html not found.")
    return FileResponse(HTML_PATH, media_type="text/html")


@app.get("/api/state")
def get_state(device_id: str = Depends(require_device_id)):
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO settings (device_id, monthly_income, savings_goal)
            VALUES (?, 6000.0, 1500.0)
            """,
            (device_id,),
        )
        cfg = conn.execute(
            "SELECT monthly_income, savings_goal FROM settings WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT id, item, category, amount, exp_date
            FROM expenses
            WHERE device_id = ?
            ORDER BY exp_date DESC, id DESC
            """,
            (device_id,),
        ).fetchall()
    return {
        "device_id": device_id,
        "income": cfg["monthly_income"],
        "goal": cfg["savings_goal"],
        "expenses": [dict(r) for r in rows],
    }


@app.put("/api/settings")
def update_settings(payload: SettingsIn, device_id: str = Depends(require_device_id)):
    safe_goal = min(payload.savings_goal, payload.monthly_income)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO settings (device_id, monthly_income, savings_goal)
            VALUES (?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                monthly_income = excluded.monthly_income,
                savings_goal = excluded.savings_goal
            """,
            (device_id, float(payload.monthly_income), float(safe_goal)),
        )
    return {"ok": True, "income": float(payload.monthly_income), "goal": float(safe_goal)}


@app.post("/api/expenses")
def create_expense(payload: ExpenseIn, device_id: str = Depends(require_device_id)):
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
            INSERT INTO expenses (device_id, item, category, amount, exp_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                clean_item,
                payload.category[:60],
                round(float(payload.amount), 2),
                exp_str,
                datetime.utcnow().isoformat(),
            ),
        )
        new_id = cur.lastrowid
    return JSONResponse(status_code=201, content={"id": new_id, "ok": True})


@app.post("/api/restore")
def restore_device_data(payload: DeviceBackupIn, device_id: str = Depends(require_device_id)):
    safe_goal = min(payload.savings_goal, payload.monthly_income)
    with get_db() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) AS cnt FROM expenses WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if existing["cnt"] == 0 and payload.expenses:
            conn.execute(
                """
                INSERT INTO settings (device_id, monthly_income, savings_goal)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    monthly_income = excluded.monthly_income,
                    savings_goal = excluded.savings_goal
                """,
                (device_id, float(payload.monthly_income), float(safe_goal)),
            )
            for exp in payload.expenses[:200]:
                clean_item = sanitize_text(exp.item)
                if not clean_item or exp.amount <= 0:
                    continue
                exp_str = exp.exp_date or date.today().isoformat()
                try:
                    datetime.strptime(exp_str, "%Y-%m-%d")
                except ValueError:
                    exp_str = date.today().isoformat()
                conn.execute(
                    """
                    INSERT INTO expenses (device_id, item, category, amount, exp_date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        clean_item,
                        exp.category[:60],
                        round(float(exp.amount), 2),
                        exp_str,
                        datetime.utcnow().isoformat(),
                    ),
                )
    return {"ok": True}


@app.delete("/api/expenses/{expense_id}")
def delete_expense(expense_id: int, device_id: str = Depends(require_device_id)):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM expenses WHERE id = ? AND device_id = ?",
            (expense_id, device_id),
        )
    return {"ok": True}