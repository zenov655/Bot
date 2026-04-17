import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "orders.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def create_order(user_id: int, username: Optional[str], full_name: str, amount: int) -> Dict[str, Any]:
    init_db()
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO orders (user_id, username, full_name, amount, status) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, full_name, amount, "waiting_payment"),
        )
        order_id = cur.lastrowid
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row)


def get_order(order_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def set_order_status(order_id: int, status: str) -> None:
    init_db()
    with _get_conn() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


def list_orders_by_status(status: str) -> List[Dict[str, Any]]:
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders WHERE status = ? ORDER BY created_at", (status,)).fetchall()
    return [dict(r) for r in rows]


def list_user_orders(user_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Получить все заказы пользователя, опционально отфильтрованные по статусу."""
    init_db()
    with _get_conn() as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
                (user_id, status),
            ).fetchall()
    return [dict(r) for r in rows]
