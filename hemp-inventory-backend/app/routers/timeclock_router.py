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
    nickname: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = "Employee"
    pay_type: Optional[str] = "Hourly"
    pay_rate: Optional[float] = None
    pin: Optional[str] = None
    username: Optional[str] = None
    custom_id: Optional[str] = None

class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    nickname: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    pay_type: Optional[str] = None
    pay_rate: Optional[float] = None
    pin: Optional[str] = None
    username: Optional[str] = None
    custom_id: Optional[str] = None
    active: Optional[bool] = None

class ClockInRequest(BaseModel):
    employee_id: int

class ClockOutRequest(BaseModel):
    employee_id: int

class ScheduleEntry(BaseModel):
    employee_id: int
    date: str          # "YYYY-MM-DD"
    start_time: str    # "09:00"
    end_time: str      # "17:00"
    location: Optional[str] = None
    notes: Optional[str] = None

class TimeOffRequest(BaseModel):
    employee_id: int
    date: str  # "YYYY-MM-DD"
    reason: Optional[str] = None

class TimeOffUpdate(BaseModel):
    status: str  # "approved" or "denied"

class ScheduleNoteCreate(BaseModel):
    date: str  # "YYYY-MM-DD"
    note: str


# ---------- Employee CRUD ----------

@router.get("/employees")
async def list_employees(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute(
        "SELECT id, name, nickname, phone, email, role, pay_type, pay_rate, pin, username, custom_id, active, created_at FROM employees ORDER BY name"
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0], "name": r[1], "nickname": r[2], "phone": r[3],
            "email": r[4], "role": r[5], "pay_type": r[6], "pay_rate": r[7],
            "pin": r[8], "username": r[9], "custom_id": r[10],
            "active": bool(r[11]), "created_at": r[12],
        }
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
        """INSERT INTO employees (name, nickname, phone, email, role, pay_type, pay_rate, pin, username, custom_id, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (emp.name.strip(), emp.nickname, emp.phone, emp.email, emp.role, emp.pay_type, emp.pay_rate, emp.pin, emp.username, emp.custom_id),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "name": emp.name.strip(), "active": True}


@router.put("/employees/{employee_id}")
async def update_employee(
    employee_id: int,
    emp: EmployeeUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    sets = []
    vals = []
    field_map = {
        "name": emp.name.strip() if emp.name else None,
        "nickname": emp.nickname,
        "phone": emp.phone,
        "email": emp.email,
        "role": emp.role,
        "pay_type": emp.pay_type,
        "pay_rate": emp.pay_rate,
        "pin": emp.pin,
        "username": emp.username,
        "custom_id": emp.custom_id,
    }
    for field, value in field_map.items():
        if value is not None:
            sets.append(f"{field} = ?")
            vals.append(value)
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


# ---------- Manual Time Entry ----------

class ManualEntryCreate(BaseModel):
    employee_id: int
    clock_in: str   # ISO datetime string
    clock_out: str  # ISO datetime string

@router.post("/entries")
async def create_manual_entry(
    data: ManualEntryCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Create a manual time entry (e.g. for past days)."""
    # Verify employee exists
    cursor = await db.execute("SELECT id, name FROM employees WHERE id = ?", (data.employee_id,))
    emp = await cursor.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Calculate hours
    t_in = datetime.fromisoformat(data.clock_in)
    t_out = datetime.fromisoformat(data.clock_out)
    if t_in.tzinfo is None:
        t_in = t_in.replace(tzinfo=timezone.utc)
    if t_out.tzinfo is None:
        t_out = t_out.replace(tzinfo=timezone.utc)
    if t_out <= t_in:
        raise HTTPException(status_code=400, detail="Clock out must be after clock in")

    hours = (t_out - t_in).total_seconds() / 3600

    cursor = await db.execute(
        "INSERT INTO time_entries (employee_id, clock_in, clock_out, hours) VALUES (?, ?, ?, ?)",
        (data.employee_id, data.clock_in, data.clock_out, round(hours, 4)),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "employee": emp[1], "hours": round(hours, 4)}


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


# ---------- Seed Employees ----------

SEED_EMPLOYEES = [
    {
        "name": "Seamus Tozzi", "nickname": "Seamus", "phone": "+1 352 651 1919",
        "email": "shamustozzi@gmail.com", "role": "Employee", "pay_type": "Hourly",
        "pay_rate": 20.00, "pin": "104010", "username": "STozzi",
        "custom_id": "Budtender / Graphics Hybrid",
    },
    {
        "name": "Kimberly Tozzi", "nickname": "Kim", "phone": "+1 352 797 1078",
        "email": "kimbaat12@yahoo.com", "role": "Manager", "pay_type": "Hourly",
        "pay_rate": 22.50, "pin": "104210", "username": "KTozzi",
        "custom_id": None,
    },
    {
        "name": "Pamela Venters", "nickname": "Pamela", "phone": "+1 317 726 7260",
        "email": "pamelaventers39@gmail.com", "role": "Manager", "pay_type": "Hourly",
        "pay_rate": 20.00, "pin": "106910", "username": "PVenters",
        "custom_id": None,
    },
    {
        "name": "Tracy Daly", "nickname": "Tracy", "phone": "+1 352 650 5624",
        "email": "tbdangel2@aol.com", "role": "Employee", "pay_type": "Hourly",
        "pay_rate": 21.00, "pin": "101210", "username": "TDaly",
        "custom_id": "Budtender",
    },
    {
        "name": "Ashley Holton", "nickname": "Ashley", "phone": "+1 912 215 5789",
        "email": "ashleyjholton91@gmail.com", "role": "Employee", "pay_type": "Hourly",
        "pay_rate": 18.50, "pin": "102010", "username": "AHolton",
        "custom_id": "Budtender",
    },
    {
        "name": "Kayla Epperhart", "nickname": "Kayla", "phone": "+1 352 345 0131",
        "email": "Kepperhart0@gmail.com", "role": "Employee", "pay_type": "Hourly",
        "pay_rate": 23.50, "pin": "100810", "username": "KEpperhart",
        "custom_id": "Budtender",
    },
    {
        "name": "Mohammed Kalam", "nickname": "Moe", "phone": "+1 352 340 9598",
        "email": "modamage27@gmail.com", "role": "Employee", "pay_type": "Hourly",
        "pay_rate": 18.00, "pin": "105910", "username": "MKalam",
        "custom_id": "Budtender",
    },
    {
        "name": "Daniel Baker", "nickname": "Dan", "phone": "+1 727 271 2308",
        "email": "danielrobertbaker777@gmail.com", "role": "Employee", "pay_type": "Hourly",
        "pay_rate": 15.00, "pin": "107310", "username": "DBaker",
        "custom_id": "Budtender",
    },
    {
        "name": "Emilio Maric", "nickname": "Emilio", "phone": "+1 727 282 4989",
        "email": "emilio_maric@icloud.com", "role": "Employee", "pay_type": "Hourly",
        "pay_rate": 15.00, "pin": "103710", "username": "EMaric",
        "custom_id": "Budtender",
    },
]


@router.post("/seed-employees")
async def seed_employees(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Seed/update the 9 employees with full Clover profile data."""
    updated = 0
    created = 0
    for emp in SEED_EMPLOYEES:
        # Check if employee exists by name
        cursor = await db.execute(
            "SELECT id FROM employees WHERE LOWER(name) = LOWER(?)", (emp["name"],)
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                """UPDATE employees SET nickname=?, phone=?, email=?, role=?, pay_type=?,
                   pay_rate=?, pin=?, username=?, custom_id=?, active=1 WHERE id=?""",
                (emp["nickname"], emp["phone"], emp["email"], emp["role"], emp["pay_type"],
                 emp["pay_rate"], emp["pin"], emp["username"], emp["custom_id"], existing[0]),
            )
            updated += 1
        else:
            await db.execute(
                """INSERT INTO employees (name, nickname, phone, email, role, pay_type, pay_rate, pin, username, custom_id, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (emp["name"], emp["nickname"], emp["phone"], emp["email"], emp["role"],
                 emp["pay_type"], emp["pay_rate"], emp["pin"], emp["username"], emp["custom_id"]),
            )
            created += 1

    # Delete employees NOT in the seed list
    seed_names = [e["name"].lower() for e in SEED_EMPLOYEES]
    cursor = await db.execute("SELECT id, name FROM employees")
    all_emps = await cursor.fetchall()
    deleted = 0
    for eid, ename in all_emps:
        if ename.lower() not in seed_names:
            await db.execute("DELETE FROM time_entries WHERE employee_id = ?", (eid,))
            await db.execute("DELETE FROM employees WHERE id = ?", (eid,))
            deleted += 1

    await db.commit()
    return {"created": created, "updated": updated, "deleted": deleted}


# ---------- Employee Self-Service Endpoints ----------

@router.get("/my-profile")
async def get_my_profile(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get the logged-in employee's own profile. Requires employee token."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    cursor = await db.execute(
        "SELECT id, name, nickname, phone, email, role, pay_type, pay_rate, username, custom_id, created_at FROM employees WHERE id = ?",
        (emp_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {
        "id": row[0], "name": row[1], "nickname": row[2], "phone": row[3],
        "email": row[4], "role": row[5], "pay_type": row[6], "pay_rate": row[7],
        "username": row[8], "custom_id": row[9], "created_at": row[10],
    }


@router.post("/my-clock-in")
async def employee_clock_in(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Employee clocks themselves in."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    cursor = await db.execute("SELECT id, name, active FROM employees WHERE id = ?", (emp_id,))
    emp = await cursor.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if not emp[2]:
        raise HTTPException(status_code=400, detail="Account is inactive")

    cursor = await db.execute(
        "SELECT id FROM time_entries WHERE employee_id = ? AND clock_out IS NULL",
        (emp_id,),
    )
    if await cursor.fetchone():
        raise HTTPException(status_code=400, detail="You are already clocked in")

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO time_entries (employee_id, clock_in) VALUES (?, ?)",
        (emp_id, now),
    )
    await db.commit()
    return {"status": "clocked_in", "employee": emp[1], "clock_in": now}


@router.post("/my-clock-out")
async def employee_clock_out(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Employee clocks themselves out."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    cursor = await db.execute("SELECT id, name FROM employees WHERE id = ?", (emp_id,))
    emp = await cursor.fetchone()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    cursor = await db.execute(
        "SELECT id, clock_in FROM time_entries WHERE employee_id = ? AND clock_out IS NULL ORDER BY clock_in DESC LIMIT 1",
        (emp_id,),
    )
    entry = await cursor.fetchone()
    if not entry:
        raise HTTPException(status_code=400, detail="You are not clocked in")

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


@router.get("/my-status")
async def get_my_clock_status(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Check if the employee is currently clocked in."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    cursor = await db.execute(
        "SELECT t.id, t.clock_in FROM time_entries t WHERE t.employee_id = ? AND t.clock_out IS NULL ORDER BY t.clock_in DESC LIMIT 1",
        (emp_id,),
    )
    entry = await cursor.fetchone()
    if entry:
        now = datetime.now(timezone.utc)
        clock_in_time = datetime.fromisoformat(entry[1])
        if clock_in_time.tzinfo is None:
            clock_in_time = clock_in_time.replace(tzinfo=timezone.utc)
        elapsed = (now - clock_in_time).total_seconds() / 3600
        return {"clocked_in": True, "clock_in": entry[1], "hours_elapsed": round(elapsed, 2)}
    return {"clocked_in": False}


@router.get("/my-entries")
async def get_my_entries(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get the logged-in employee's own time entries."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    query = """
        SELECT t.id, e.name, t.clock_in, t.clock_out, t.hours
        FROM time_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE t.employee_id = ?
    """
    params: list = [emp_id]
    if start_date:
        query += " AND t.clock_in >= ?"
        params.append(start_date)
    if end_date:
        query += " AND t.clock_in <= ?"
        params.append(end_date + "T23:59:59")
    query += " ORDER BY t.clock_in DESC"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "employee_name": r[1],
            "clock_in": r[2],
            "clock_out": r[3],
            "hours": r[4],
        }
        for r in rows
    ]


# ---------- Schedules (Admin) ----------

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

@router.get("/schedules")
async def list_schedules(
    employee_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: List date-specific schedules, optionally filtered by employee and date range."""
    query = """
        SELECT s.id, s.employee_id, e.name, s.date, s.start_time, s.end_time, s.location, s.notes
        FROM date_schedules s
        JOIN employees e ON e.id = s.employee_id
        WHERE 1=1
    """
    params: list = []
    if employee_id:
        query += " AND s.employee_id = ?"
        params.append(employee_id)
    if start_date:
        query += " AND s.date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND s.date <= ?"
        params.append(end_date)
    query += " ORDER BY s.date, e.name"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "employee_id": r[1],
            "employee_name": r[2],
            "date": r[3],
            "start_time": r[4],
            "end_time": r[5],
            "location": r[6],
            "notes": r[7],
        }
        for r in rows
    ]


@router.post("/schedules")
async def save_schedule(
    entry: ScheduleEntry,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Create or update a date-specific schedule entry (upserts by employee_id + date)."""
    # Verify employee exists
    cursor = await db.execute("SELECT id FROM employees WHERE id = ?", (entry.employee_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Employee not found")

    await db.execute(
        """INSERT INTO date_schedules (employee_id, date, start_time, end_time, location, notes, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(employee_id, date) DO UPDATE SET
             start_time = excluded.start_time,
             end_time = excluded.end_time,
             location = excluded.location,
             notes = excluded.notes,
             updated_at = CURRENT_TIMESTAMP""",
        (entry.employee_id, entry.date, entry.start_time, entry.end_time, entry.location, entry.notes),
    )
    await db.commit()
    return {"status": "saved"}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Delete a schedule entry."""
    await db.execute("DELETE FROM date_schedules WHERE id = ?", (schedule_id,))
    await db.commit()
    return {"status": "deleted"}


@router.delete("/schedules/employee/{employee_id}/date/{date}")
async def delete_schedule_by_date(
    employee_id: int,
    date: str,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Delete a schedule entry by employee and date."""
    await db.execute(
        "DELETE FROM date_schedules WHERE employee_id = ? AND date = ?",
        (employee_id, date),
    )
    await db.commit()
    return {"status": "deleted"}


# ---------- My Schedule (Employee) ----------

@router.get("/my-schedule")
async def get_my_schedule(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Employee: Get my date-specific schedule."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    query = """SELECT id, date, start_time, end_time, location, notes
               FROM date_schedules WHERE employee_id = ?"""
    params: list = [emp_id]
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    query += " ORDER BY date"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "date": r[1],
            "start_time": r[2],
            "end_time": r[3],
            "location": r[4],
            "notes": r[5],
        }
        for r in rows
    ]


# ---------- Employee Self-Service: Time-Off & Notes ----------

class MyTimeOffRequest(BaseModel):
    date: str  # "YYYY-MM-DD"
    reason: Optional[str] = None

@router.get("/my-time-off")
async def get_my_time_off(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Employee: Get my time-off requests."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    cursor = await db.execute(
        """SELECT id, date, reason, status, reviewed_by, created_at
           FROM time_off_requests WHERE employee_id = ? ORDER BY date""",
        (emp_id,),
    )
    rows = await cursor.fetchall()
    return [
        {"id": r[0], "date": r[1], "reason": r[2], "status": r[3], "reviewed_by": r[4], "created_at": r[5]}
        for r in rows
    ]


@router.post("/my-time-off")
async def submit_my_time_off(
    req: MyTimeOffRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Employee: Submit a time-off request."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    try:
        await db.execute(
            "INSERT INTO time_off_requests (employee_id, date, reason) VALUES (?, ?, ?)",
            (emp_id, req.date, req.reason),
        )
        await db.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="Request already exists for this date")
    return {"status": "created"}


@router.delete("/my-time-off/{request_id}")
async def cancel_my_time_off(
    request_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Employee: Cancel a pending time-off request."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    emp_id = user.get("employee_id")
    cursor = await db.execute(
        "SELECT status FROM time_off_requests WHERE id = ? AND employee_id = ?",
        (request_id, emp_id),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row[0] != "pending":
        raise HTTPException(status_code=400, detail="Can only cancel pending requests")
    await db.execute("DELETE FROM time_off_requests WHERE id = ?", (request_id,))
    await db.commit()
    return {"status": "deleted"}


@router.get("/my-schedule-notes")
async def get_my_schedule_notes(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Employee: Get schedule notes."""
    if user.get("role") != "employee":
        raise HTTPException(status_code=403, detail="Employee access only")
    query = "SELECT id, date, note, created_by, created_at FROM schedule_notes WHERE 1=1"
    params: list = []
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    query += " ORDER BY date"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        {"id": r[0], "date": r[1], "note": r[2], "created_by": r[3], "created_at": r[4]}
        for r in rows
    ]


# ---------- Time-Off Requests ----------

@router.get("/time-off")
async def list_time_off_requests(
    employee_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: List time-off requests with optional filters."""
    query = """
        SELECT t.id, t.employee_id, e.name, t.date, t.reason, t.status, t.reviewed_by, t.created_at
        FROM time_off_requests t
        JOIN employees e ON e.id = t.employee_id
        WHERE 1=1
    """
    params: list = []
    if employee_id:
        query += " AND t.employee_id = ?"
        params.append(employee_id)
    if start_date:
        query += " AND t.date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND t.date <= ?"
        params.append(end_date)
    if status:
        query += " AND t.status = ?"
        params.append(status)
    query += " ORDER BY t.date, e.name"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "employee_id": r[1],
            "employee_name": r[2],
            "date": r[3],
            "reason": r[4],
            "status": r[5],
            "reviewed_by": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]


@router.post("/time-off")
async def create_time_off_request(
    req: TimeOffRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Create a time-off request for an employee."""
    cursor = await db.execute("SELECT id FROM employees WHERE id = ?", (req.employee_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        await db.execute(
            "INSERT INTO time_off_requests (employee_id, date, reason) VALUES (?, ?, ?)",
            (req.employee_id, req.date, req.reason),
        )
        await db.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="Time-off request already exists for this date")
    return {"status": "created"}


@router.put("/time-off/{request_id}")
async def update_time_off_request(
    request_id: int,
    data: TimeOffUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Approve or deny a time-off request."""
    if data.status not in ("approved", "denied", "pending"):
        raise HTTPException(status_code=400, detail="Status must be approved, denied, or pending")
    await db.execute(
        "UPDATE time_off_requests SET status = ?, reviewed_by = ? WHERE id = ?",
        (data.status, user.get("username", "admin"), request_id),
    )
    await db.commit()
    return {"status": "updated"}


@router.delete("/time-off/{request_id}")
async def delete_time_off_request(
    request_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Delete a time-off request."""
    await db.execute("DELETE FROM time_off_requests WHERE id = ?", (request_id,))
    await db.commit()
    return {"status": "deleted"}


# ---------- Schedule Notes ----------

@router.get("/schedule-notes")
async def list_schedule_notes(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: List schedule notes for a date range."""
    query = "SELECT id, date, note, created_by, created_at FROM schedule_notes WHERE 1=1"
    params: list = []
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    query += " ORDER BY date"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        {"id": r[0], "date": r[1], "note": r[2], "created_by": r[3], "created_at": r[4]}
        for r in rows
    ]


@router.post("/schedule-notes")
async def create_schedule_note(
    data: ScheduleNoteCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Add a note to a specific date."""
    cursor = await db.execute(
        "INSERT INTO schedule_notes (date, note, created_by) VALUES (?, ?, ?)",
        (data.date, data.note, user.get("username", "admin")),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "status": "created"}


@router.delete("/schedule-notes/{note_id}")
async def delete_schedule_note(
    note_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Delete a schedule note."""
    await db.execute("DELETE FROM schedule_notes WHERE id = ?", (note_id,))
    await db.commit()
    return {"status": "deleted"}
