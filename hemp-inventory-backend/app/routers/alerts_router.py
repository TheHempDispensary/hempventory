import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import aiosqlite

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class EmailSettings(BaseModel):
    notification_email: str
    smtp_host: Optional[str] = "smtp.gmail.com"
    smtp_port: Optional[int] = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None


@router.get("/history")
async def get_alert_history(
    limit: int = 50,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("""
        SELECT a.id, a.sku, a.product_name, a.location_id, a.current_stock,
               a.par_level, a.alert_type, a.notified_at, a.email_sent, l.name as location_name
        FROM alert_history a
        JOIN locations l ON a.location_id = l.id
        ORDER BY a.notified_at DESC
        LIMIT ?
    """, (limit,))
    rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "sku": row[1],
            "product_name": row[2],
            "location_id": row[3],
            "current_stock": row[4],
            "par_level": row[5],
            "alert_type": row[6],
            "notified_at": row[7],
            "email_sent": bool(row[8]),
            "location_name": row[9],
        }
        for row in rows
    ]


@router.post("/check")
async def check_and_notify(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Check PAR levels and send email notifications for items below threshold."""
    # Get notification email
    cursor = await db.execute("SELECT value FROM settings WHERE key = 'notification_email'")
    row = await cursor.fetchone()
    notification_email = row[0] if row else None

    # Get all locations
    cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    locations = await cursor.fetchall()

    # Get all PAR levels
    cursor = await db.execute("SELECT sku, location_id, par_level FROM par_levels")
    par_rows = await cursor.fetchall()
    par_map: dict[tuple[str, int], float] = {(row[0], row[1]): row[2] for row in par_rows}

    if not par_map:
        return {"alerts_found": 0, "message": "No PAR levels configured"}

    alerts = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items()
            items = data.get("elements", [])
        except Exception as e:
            print(f"Error checking PAR for {loc_name}: {e}")
            continue

        for item in items:
            sku = item.get("sku", "") or item.get("id", "")
            par_level = par_map.get((sku, loc_id))
            if par_level is None:
                continue

            item_stock = item.get("itemStock", {})
            quantity = item_stock.get("quantity", 0) if item_stock else 0

            if quantity <= par_level:
                alerts.append({
                    "sku": sku,
                    "product_name": item.get("name", ""),
                    "location_id": loc_id,
                    "location_name": loc_name,
                    "current_stock": quantity,
                    "par_level": par_level,
                })

    # Save alerts to history
    for alert in alerts:
        await db.execute(
            """INSERT INTO alert_history (sku, product_name, location_id, current_stock, par_level, alert_type, email_sent)
               VALUES (?, ?, ?, ?, ?, 'below_par', 0)""",
            (alert["sku"], alert["product_name"], alert["location_id"], alert["current_stock"], alert["par_level"]),
        )
    await db.commit()

    # Send email if configured
    email_sent = False
    if notification_email and alerts:
        try:
            email_sent = await _send_alert_email(db, notification_email, alerts)
            if email_sent:
                await db.execute(
                    "UPDATE alert_history SET email_sent = 1 WHERE email_sent = 0"
                )
                await db.commit()
        except Exception as e:
            print(f"Error sending email: {e}")

    return {
        "alerts_found": len(alerts),
        "email_sent": email_sent,
        "notification_email": notification_email,
        "alerts": alerts,
    }


async def _send_alert_email(db: aiosqlite.Connection, to_email: str, alerts: list[dict]) -> bool:
    """Send alert email using configured SMTP settings."""
    # Get SMTP settings
    smtp_settings: dict[str, str] = {}
    for key in ["smtp_host", "smtp_port", "smtp_user", "smtp_password"]:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row:
            smtp_settings[key] = row[0]

    smtp_host = smtp_settings.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(smtp_settings.get("smtp_port", "587"))
    smtp_user = smtp_settings.get("smtp_user", "")
    smtp_password = smtp_settings.get("smtp_password", "")

    if not smtp_user or not smtp_password:
        print("SMTP credentials not configured, skipping email")
        return False

    # Build email
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Hemp Dispensary PAR Alert - {len(alerts)} item(s) below threshold"
    msg["From"] = smtp_user
    msg["To"] = to_email

    # Build HTML body
    rows_html = ""
    for alert in alerts:
        deficit = alert["par_level"] - alert["current_stock"]
        restock = int(deficit + alert["par_level"] * 0.5)
        rows_html += f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd;">{alert['product_name']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{alert['sku']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{alert['location_name']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; color: red; font-weight: bold;">{alert['current_stock']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{alert['par_level']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{restock}</td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #dc2626;">PAR Level Alert</h2>
        <p>The following items are at or below their PAR levels and need restocking:</p>
        <table style="border-collapse: collapse; width: 100%;">
            <thead>
                <tr style="background-color: #f3f4f6;">
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Product</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">SKU</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Location</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Current Stock</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">PAR Level</th>
                    <th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Suggested Restock</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        <p style="margin-top: 16px; color: #6b7280;">This alert was sent by the Hemp Dispensary Inventory Manager.</p>
    </body>
    </html>
    """

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


@router.get("/settings")
async def get_alert_settings(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    settings_keys = ["notification_email", "smtp_host", "smtp_port", "smtp_user"]
    result: dict[str, str] = {}
    for key in settings_keys:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        result[key] = row[0] if row else ""
    # Don't expose SMTP password
    cursor = await db.execute("SELECT value FROM settings WHERE key = 'smtp_password'")
    row = await cursor.fetchone()
    result["smtp_password_set"] = bool(row and row[0])
    return result


@router.post("/settings")
async def update_alert_settings(
    settings: EmailSettings,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    settings_dict: dict[str, str] = {
        "notification_email": settings.notification_email,
    }
    if settings.smtp_host:
        settings_dict["smtp_host"] = settings.smtp_host
    if settings.smtp_port:
        settings_dict["smtp_port"] = str(settings.smtp_port)
    if settings.smtp_user:
        settings_dict["smtp_user"] = settings.smtp_user
    if settings.smtp_password:
        settings_dict["smtp_password"] = settings.smtp_password

    for key, value in settings_dict.items():
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    await db.commit()
    return {"message": "Alert settings updated"}
