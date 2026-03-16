from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import aiosqlite
import io
import csv
from datetime import datetime, timezone

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient

router = APIRouter(prefix="/api/timeclock", tags=["timeclock"])


async def _get_locations(db: aiosqlite.Connection):
    cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    return await cursor.fetchall()


# ---------- Models ----------

class EmployeeCreate(BaseModel):
    name: str
    pin: Optional[str] = None

class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    pin: Optional[str] = None
    active: Optional[bool] = None

class ClockInRequest(BaseModel):
    employee_id: int

class ClockOutRequest(BaseModel):
    employee_id: int


# ---------- Employee CRUD ----------

@router.get("/employees")
async def list_employees(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute(
        "SELECT id, name, pin, active, created_at FROM employees ORDER BY name"
    )
    rows = await cursor.fetchall()
    return [
        {"id": r[0], "name": r[1], "pin": r[2], "active": bool(r[3]), "created_at": r[4]}
        for r in rows
    ]


@router.post("/employees")
async def create_employee(
    emp: EmployeeCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    if not emp.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    cursor = await db.execute(
        "INSERT INTO employees (name, pin, active) VALUES (?, ?, 1)",
        (emp.name.strip(), emp.pin or None),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "name": emp.name.strip(), "pin": emp.pin, "active": True}


@router.put("/employees/{employee_id}")
async def update_employee(
    employee_id: int,
    emp: EmployeeUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    sets = []
    vals = []
    if emp.name is not None:
        sets.append("name = ?")
        vals.append(emp.name.strip())
    if emp.pin is not None:
        sets.append("pin = ?")
        vals.append(emp.pin)
    if emp.active is not None:
        sets.append("active = ?")
        vals.append(1 if emp.active else 0)
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update")
    vals.append(employee_id)
    await db.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()
    return {"status": "updated"}


@router.delete("/employees/{employee_id}")
async def delete_employee(
    employee_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute("DELETE FROM time_entries WHERE employee_id = ?", (employee_id,))
    await db.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
    await db.commit()
    return {"status": "deleted"}


# ---------- Clock In / Out ----------

@router.post("/clock-in")
async def clock_in(
    req: ClockInRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    # Check employee exists and is active
    cursor = await db.execute("SELECT id, name, active FROM employees WHERE id = ?", (req.employee_id,))
    emp = await cursor.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not emp[2]:
        raise HTTPException(status_code=400, detail="Employee is inactive")

    # Check if already clocked in
    cursor = await db.execute(
        "SELECT id FROM time_entries WHERE employee_id = ? AND clock_out IS NULL",
        (req.employee_id,),
    )
    if await cursor.fetchone():
        raise HTTPException(status_code=400, detail=f"{emp[1]} is already clocked in")

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO time_entries (employee_id, clock_in) VALUES (?, ?)",
        (req.employee_id, now),
    )
    await db.commit()
    return {"status": "clocked_in", "employee": emp[1], "clock_in": now}


@router.post("/clock-out")
async def clock_out(
    req: ClockOutRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("SELECT id, name FROM employees WHERE id = ?", (req.employee_id,))
    emp = await cursor.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Find open clock-in entry
    cursor = await db.execute(
        "SELECT id, clock_in FROM time_entries WHERE employee_id = ? AND clock_out IS NULL ORDER BY clock_in DESC LIMIT 1",
        (req.employee_id,),
    )
    entry = await cursor.fetchone()
    if not entry:
        raise HTTPException(status_code=400, detail=f"{emp[1]} is not clocked in")

    now = datetime.now(timezone.utc)
    clock_in_time = datetime.fromisoformat(entry[1])
    if clock_in_time.tzinfo is None:
        clock_in_time = clock_in_time.replace(tzinfo=timezone.utc)
    hours = (now - clock_in_time).total_seconds() / 3600

    await db.execute(
        "UPDATE time_entries SET clock_out = ?, hours = ? WHERE id = ?",
        (now.isoformat(), round(hours, 2), entry[0]),
    )
    await db.commit()
    return {"status": "clocked_out", "employee": emp[1], "clock_out": now.isoformat(), "hours": round(hours, 2)}


# ---------- Active Clocks ----------

@router.get("/active")
async def get_active_clocks(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("""
        SELECT e.id, e.name, t.clock_in, t.id as entry_id
        FROM time_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE t.clock_out IS NULL
        ORDER BY t.clock_in
    """)
    rows = await cursor.fetchall()
    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        clock_in_time = datetime.fromisoformat(r[2])
        if clock_in_time.tzinfo is None:
            clock_in_time = clock_in_time.replace(tzinfo=timezone.utc)
        elapsed = (now - clock_in_time).total_seconds() / 3600
        result.append({
            "employee_id": r[0],
            "employee_name": r[1],
            "clock_in": r[2],
            "entry_id": r[3],
            "hours_elapsed": round(elapsed, 2),
        })
    return result


# ---------- Time Entries ----------

@router.get("/entries")
async def get_time_entries(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    employee_id: Optional[int] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    query = """
        SELECT t.id, e.id as emp_id, e.name, t.clock_in, t.clock_out, t.hours
        FROM time_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE 1=1
    """
    params = []
    if start_date:
        query += " AND t.clock_in >= ?"
        params.append(start_date)
    if end_date:
        query += " AND t.clock_in <= ?"
        params.append(end_date + "T23:59:59")
    if employee_id:
        query += " AND t.employee_id = ?"
        params.append(employee_id)
    query += " ORDER BY t.clock_in DESC"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "employee_id": r[1],
            "employee_name": r[2],
            "clock_in": r[3],
            "clock_out": r[4],
            "hours": r[5],
        }
        for r in rows
    ]


# ---------- Edit / Delete Entry ----------

class EntryUpdate(BaseModel):
    clock_in: Optional[str] = None
    clock_out: Optional[str] = None

@router.put("/entries/{entry_id}")
async def update_entry(
    entry_id: int,
    data: EntryUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    sets = []
    vals = []
    if data.clock_in is not None:
        sets.append("clock_in = ?")
        vals.append(data.clock_in)
    if data.clock_out is not None:
        sets.append("clock_out = ?")
        vals.append(data.clock_out)

    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update")

    # Recalculate hours if we have both times
    cursor = await db.execute("SELECT clock_in, clock_out FROM time_entries WHERE id = ?", (entry_id,))
    existing = await cursor.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Entry not found")

    new_in = data.clock_in or existing[0]
    new_out = data.clock_out or existing[1]
    if new_in and new_out:
        t_in = datetime.fromisoformat(new_in)
        t_out = datetime.fromisoformat(new_out)
        if t_in.tzinfo is None:
            t_in = t_in.replace(tzinfo=timezone.utc)
        if t_out.tzinfo is None:
            t_out = t_out.replace(tzinfo=timezone.utc)
        hours = (t_out - t_in).total_seconds() / 3600
        sets.append("hours = ?")
        vals.append(round(hours, 2))

    vals.append(entry_id)
    await db.execute(f"UPDATE time_entries SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()
    return {"status": "updated"}


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute("DELETE FROM time_entries WHERE id = ?", (entry_id,))
    await db.commit()
    return {"status": "deleted"}


# ---------- CSV Export ----------

@router.get("/export")
async def export_csv(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    employee_id: Optional[int] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    query = """
        SELECT e.name, t.clock_in, t.clock_out, t.hours
        FROM time_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE t.clock_out IS NOT NULL
    """
    params = []
    if start_date:
        query += " AND t.clock_in >= ?"
        params.append(start_date)
    if end_date:
        query += " AND t.clock_in <= ?"
        params.append(end_date + "T23:59:59")
    if employee_id:
        query += " AND t.employee_id = ?"
        params.append(employee_id)
    query += " ORDER BY e.name, t.clock_in"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee", "Clock In", "Clock Out", "Hours"])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3]])

    # Add summary by employee
    writer.writerow([])
    writer.writerow(["--- Summary ---"])
    writer.writerow(["Employee", "Total Hours"])
    summary = {}
    for r in rows:
        name = r[0]
        hrs = r[3] or 0
        summary[name] = summary.get(name, 0) + hrs
    for name, total in sorted(summary.items()):
        writer.writerow([name, round(total, 2)])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=timesheet.csv"},
    )


# ---------- Sync from Clover ----------

@router.post("/sync-employees")
async def sync_employees_from_clover(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Import employees from all Clover locations. Skips duplicates by name."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    imported = 0
    skipped = 0
    errors = []

    # Get existing employee names for dedup
    cursor = await db.execute("SELECT LOWER(name) FROM employees")
    existing_names = {r[0] for r in await cursor.fetchall()}

    seen_names = set()
    for loc in locations:
        loc_name, merchant_id, api_token = loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_employees()
            for emp in data.get("elements", []):
                name = emp.get("name", "").strip()
                if not name:
                    continue
                name_lower = name.lower()
                if name_lower in existing_names or name_lower in seen_names:
                    skipped += 1
                    continue
                seen_names.add(name_lower)
                await db.execute(
                    "INSERT INTO employees (name, pin, active) VALUES (?, ?, 1)",
                    (name, None),
                )
                imported += 1
        except Exception as e:
            errors.append({"location": loc_name, "error": str(e)})

    await db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors}
