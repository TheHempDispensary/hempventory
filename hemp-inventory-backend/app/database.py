import aiosqlite
import os

DB_PATH = os.environ.get("DB_PATH", "/data/app.db")

async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                merchant_id TEXT NOT NULL UNIQUE,
                api_token TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS par_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                location_id INTEGER NOT NULL,
                par_level REAL NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (location_id) REFERENCES locations(id),
                UNIQUE(sku, location_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL,
                product_name TEXT NOT NULL,
                location_id INTEGER NOT NULL,
                current_stock REAL,
                par_level REAL,
                alert_type TEXT DEFAULT 'below_par',
                notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                email_sent INTEGER DEFAULT 0,
                FOREIGN KEY (location_id) REFERENCES locations(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL UNIQUE,
                image_data TEXT NOT NULL,
                content_type TEXT DEFAULT 'image/png',
                product_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add product_name column if missing (migration)
        try:
            await db.execute("ALTER TABLE product_images ADD COLUMN product_name TEXT")
        except Exception:
            pass  # Column already exists

        # Loyalty program tables
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT,
                phone TEXT UNIQUE,
                email TEXT,
                birthday TEXT,
                points_balance INTEGER DEFAULT 0,
                lifetime_points INTEGER DEFAULT 0,
                lifetime_redeemed INTEGER DEFAULT 0,
                clover_customer_id TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                points INTEGER NOT NULL,
                description TEXT,
                order_id TEXT,
                location_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES loyalty_customers(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                points_required INTEGER NOT NULL,
                reward_type TEXT NOT NULL DEFAULT 'discount',
                reward_value REAL NOT NULL,
                description TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                reward_id INTEGER NOT NULL,
                points_spent INTEGER NOT NULL,
                location_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES loyalty_customers(id),
                FOREIGN KEY (reward_id) REFERENCES loyalty_rewards(id)
            )
        """)

        # Orders table for e-commerce
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ecommerce_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'pending',
                customer_first_name TEXT,
                customer_last_name TEXT,
                customer_email TEXT,
                customer_phone TEXT,
                shipping_address TEXT,
                shipping_apartment TEXT,
                shipping_city TEXT,
                shipping_state TEXT,
                shipping_zip TEXT,
                subtotal INTEGER DEFAULT 0,
                shipping_cost INTEGER DEFAULT 0,
                tax INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                notes TEXT,
                charge_id TEXT,
                payment_status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migration: add columns if missing
        for col, coldef in [
            ("charge_id", "TEXT"),
            ("payment_status", "TEXT DEFAULT 'pending'"),
            ("tracking_number", "TEXT"),
            ("tracking_url", "TEXT"),
            ("label_url", "TEXT"),
            ("shippo_transaction_id", "TEXT"),
            ("staff_notes", "TEXT"),
            ("refund_id", "TEXT"),
            ("refund_amount", "INTEGER"),
            ("tracking_status", "TEXT"),
            ("discount", "INTEGER DEFAULT 0"),
            ("promo_code", "TEXT"),
            ("fulfillment_type", "TEXT DEFAULT 'shipping'"),
        ]:
            try:
                await db.execute(f"ALTER TABLE ecommerce_orders ADD COLUMN {col} {coldef}")
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ecommerce_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id TEXT,
                product_name TEXT,
                sku TEXT,
                price INTEGER DEFAULT 0,
                quantity INTEGER DEFAULT 1,
                FOREIGN KEY (order_id) REFERENCES ecommerce_orders(id)
            )
        """)

        # Loyalty order sync tracking table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_synced_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clover_order_id TEXT NOT NULL,
                location_merchant_id TEXT NOT NULL,
                location_name TEXT,
                order_total INTEGER DEFAULT 0,
                customer_id INTEGER,
                points_awarded INTEGER DEFAULT 0,
                status TEXT DEFAULT 'synced',
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(clover_order_id, location_merchant_id)
            )
        """)

        # Migration: add clover_customer_id column if missing
        try:
            await db.execute("ALTER TABLE loyalty_customers ADD COLUMN clover_customer_id TEXT")
        except Exception:
            pass  # Column already exists

        # Multi-location Clover customer ID mapping
        # Each loyalty customer can have different Clover IDs at different locations
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_clover_id_map (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loyalty_customer_id INTEGER NOT NULL,
                clover_customer_id TEXT NOT NULL,
                merchant_id TEXT NOT NULL,
                location_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (loyalty_customer_id) REFERENCES loyalty_customers(id),
                UNIQUE(clover_customer_id, merchant_id)
            )
        """)

        # Refund tracking table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS synced_refunds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clover_order_id TEXT NOT NULL,
                location_merchant_id TEXT NOT NULL,
                location_name TEXT,
                refund_total INTEGER DEFAULT 0,
                items_returned TEXT,
                status TEXT DEFAULT 'synced',
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(clover_order_id, location_merchant_id)
            )
        """)

        # Time clock tables
        await db.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                nickname TEXT,
                phone TEXT,
                email TEXT,
                role TEXT DEFAULT 'Employee',
                pay_type TEXT DEFAULT 'Hourly',
                pay_rate REAL,
                pin TEXT,
                username TEXT UNIQUE,
                custom_id TEXT,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migration: add new employee columns if missing
        for col, coldef in [
            ("nickname", "TEXT"),
            ("phone", "TEXT"),
            ("email", "TEXT"),
            ("role", "TEXT DEFAULT 'Employee'"),
            ("pay_type", "TEXT DEFAULT 'Hourly'"),
            ("pay_rate", "REAL"),
            ("username", "TEXT"),
            ("custom_id", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE employees ADD COLUMN {col} {coldef}")
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS time_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                clock_in TEXT NOT NULL,
                clock_out TEXT,
                hours REAL,
                FOREIGN KEY (employee_id) REFERENCES employees(id)
            )
        """)

        # Employee schedules table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS employee_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                location TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                UNIQUE(employee_id, day_of_week)
            )
        """)

        # Promo codes table for discount management
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                discount_pct REAL NOT NULL DEFAULT 0,
                discount_amount INTEGER DEFAULT 0,
                single_use INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                max_uses INTEGER DEFAULT 0,
                times_used INTEGER DEFAULT 0,
                expires_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Seed FIRST15 if promo_codes table is empty
        cursor = await db.execute("SELECT COUNT(*) FROM promo_codes")
        count = (await cursor.fetchone())[0]
        if count == 0:
            await db.execute(
                "INSERT INTO promo_codes (code, discount_pct, single_use, is_active) VALUES (?, ?, ?, ?)",
                ("FIRST15", 0.15, 1, 1),
            )

        # Seed default loyalty settings if empty
        cursor = await db.execute("SELECT COUNT(*) FROM loyalty_settings")
        count = (await cursor.fetchone())[0]
        if count == 0:
            await db.executemany(
                "INSERT INTO loyalty_settings (key, value) VALUES (?, ?)",
                [
                    ("points_per_dollar", "1"),
                    ("signup_bonus", "10"),
                    ("birthday_bonus", "25"),
                    ("program_name", "Hemp Rewards"),
                ]
            )

        # Seed default reward if empty
        cursor = await db.execute("SELECT COUNT(*) FROM loyalty_rewards")
        count = (await cursor.fetchone())[0]
        if count == 0:
            await db.execute(
                """INSERT INTO loyalty_rewards (name, points_required, reward_type, reward_value, description)
                   VALUES (?, ?, ?, ?, ?)""",
                ("$5 off any purchase", 100, "discount", 5.00, "Get $5 off when you earn 100 points")
            )

        await db.commit()
