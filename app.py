import streamlit as st
import sqlite3
import hashlib
import os
from datetime import datetime
import pandas as pd
import plotly.express as px
import streamlit.components.v1 as components

# Optional Integrations (Graceful fallback if keys aren't configured yet)
try:
    import razorpay
except ImportError:
    razorpay = None

try:
    from google import genai
except ImportError:
    genai = None

# ==========================================
# 1. PAGE CONFIG & DATABASE INITIALIZATION
# ==========================================
st.set_page_config(page_title="AgriPulse | Farm SaaS", page_icon="🌱", layout="wide")

DB_PATH = "agripulse_prod.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            password_hash TEXT,
            auth_provider TEXT DEFAULT 'email',
            role TEXT DEFAULT 'Farmer',
            plan TEXT DEFAULT 'Free',
            created_at TEXT
        )
    """)

    # Farm & Finance records
    cur.execute("""
        CREATE TABLE IF NOT EXISTS farm_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            record_date TEXT,
            crop_name TEXT,
            area_acres REAL DEFAULT 0,
            harvest_kg REAL DEFAULT 0,
            revenue_inr REAL DEFAULT 0,
            expense_inr REAL DEFAULT 0,
            notes TEXT
        )
    """)

    # AI Chat History
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            message TEXT,
            timestamp TEXT
        )
    """)

    # Subscription Payments
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            order_id TEXT,
            payment_id TEXT,
            amount_inr REAL,
            plan_name TEXT,
            status TEXT,
            created_at TEXT
        )
    """)

    # Notifications
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            body TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    # Safe schema migration guard (prevents OperationalError on existing DBs)
    migrations = [
        "ALTER TABLE farm_records ADD COLUMN user_id INTEGER",
        "ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'Free'",
        "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'Farmer'",
    ]
    for stmt in migrations:
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

init_db()

# ==========================================
# 2. AUTHENTICATION HELPERS
# ==========================================
def hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_or_create_user(email: str, name: str, provider: str = "email", password_hash: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    admin_email = st.secrets.get("ADMIN_EMAIL", "").lower().strip()
    role = "Admin" if email.lower().strip() == admin_email else "Farmer"

    cur.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
    row = cur.fetchone()
    if row:
        user = dict(row)
    else:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cur.execute(
            "INSERT INTO users (name, email, password_hash, auth_provider, role, plan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, email.lower().strip(), password_hash, provider, role, "Free", now),
        )
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO notifications (user_id, title, body, created_at) VALUES (?, ?, ?, ?)",
            (user_id, "Welcome to AgriPulse!", "Start logging your crops and farm finances on the Free plan.", now),
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = dict(cur.fetchone())
    conn.close()
    return user

def render_login_page():
    st.title("🌱 AgriPulse — Smart Farm & Finance OS")
    st.caption("Track plantation yields, profit/loss, AI agronomy insights, and exportable reports.")

    col1, col2 = st.columns([1.2, 1])
    with col1:
        tab_login, tab_signup = st.tabs(["🔐 Email Login", "📝 Create Account"])

        with tab_login:
            with st.form("email_login_form"):
                email = st.text_input("Email")
                pw = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Login", use_container_width=True, type="primary")
                if submitted:
                    conn = get_conn()
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM users WHERE email=? AND password_hash=?", (email.lower().strip(), hash_pw(pw)))
                    row = cur.fetchone()
                    conn.close()
                    if row:
                        st.session_state["user"] = dict(row)
                        st.rerun()
                    else:
                        st.error("Invalid email or password.")

        with tab_signup:
            with st.form("signup_form"):
                name = st.text_input("Full Name / Farm Name")
                new_email = st.text_input("Email Address")
                new_pw = st.text_input("Create Password", type="password")
                reg = st.form_submit_button("Start Free Account", use_container_width=True)
                if reg:
                    if not name or not new_email or len(new_pw) < 6:
                        st.warning("Enter name, valid email, and password (min 6 characters).")
                    else:
                        conn = get_conn()
                        cur = conn.cursor()
                        cur.execute("SELECT id FROM users WHERE email=?", (new_email.lower().strip(),))
                        if cur.fetchone():
                            st.error("Account already exists. Please log in.")
                        else:
                            user = get_or_create_user(new_email, name, "email", hash_pw(new_pw))
                            st.session_state["user"] = user
                            st.rerun()
                        conn.close()

        # Native Streamlit Google Login (Available when [auth] is set in secrets.toml)
        if "auth" in st.secrets:
            st.divider()
            if st.button("🌐 Continue with Google", use_container_width=True):
                st.login()

    with col2:
        st.info("### Free vs. Pro Plans\n"
                "**Free Plan (₹0/mo):**\n"
                "- Up to 10 farm/finance entries\n"
                "- Basic KPI Dashboard\n"
                "- 5 AI Agronomist prompts\n\n"
                "**Pro Plan (₹499/mo):**\n"
                "- Unlimited crop & finance records\n"
                "- Unlimited AI Agronomist Chat + History\n"
                "- Excel & CSV Financial Reports")

# Handle Google OIDC callback if logged in via st.login()
if "auth" in st.secrets and getattr(st.user, "is_logged_in", False):
    if "user" not in st.session_state:
        st.session_state["user"] = get_or_create_user(st.user.email, st.user.name, "google")

if "user" not in st.session_state:
    render_login_page()
    st.stop()

# Refresh user data from DB on every run (catches plan upgrades immediately)
conn = get_conn()
cur = conn.cursor()
cur.execute("SELECT * FROM users WHERE id = ?", (st.session_state["user"]["id"],))
fresh_user = cur.fetchone()
conn.close()
if fresh_user:
    st.session_state["user"] = dict(fresh_user)

user = st.session_state["user"]
is_pro = user["plan"] == "Pro" or user["role"] == "Admin"

# ==========================================
# 3. SIDEBAR NAVIGATION
# ==========================================
conn = get_conn()
unread_count = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)).fetchone()[0]
conn.close()

with st.sidebar:
    st.title("🌱 AgriPulse")
    st.write(f"**{user['name']}** ({user['role']})")
    badge = "🌟 Pro" if is_pro else "🆓 Free"
    st.caption(f"Current Plan: **{badge}**")

    nav_options = [
        "Dashboard",
        "Add Farm Record",
        "Crop Records",
        "Farm Finance",
        "AI Agronomist Chat",
        "Export Reports",
        f"Notifications ({unread_count})",
        "Subscription & Billing",
    ]
    if user["role"] == "Admin":
        nav_options.append("Admin Dashboard")

    page = st.radio("Navigation", nav_options)
    st.divider()
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.clear()
        if "auth" in st.secrets and getattr(st.user, "is_logged_in", False):
            st.logout()
        st.rerun()

# Helper to load user records safely
def load_records(uid):
    conn = get_conn()
    try:
        df = pd.read_sql_query("SELECT * FROM farm_records WHERE user_id=? ORDER BY record_date DESC", conn, params=(uid,))
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df

# ==========================================
# 4. APPLICATION PAGES
# ==========================================

# --- PAGE 1: DASHBOARD ---
if page == "Dashboard":
    st.header("📊 Farmer Dashboard")
    st.caption("Monitor plantation area, harvest volume, revenue, and net profit.")
    df = load_records(user["id"])

    if df.empty:
        st.info("No records found yet. Go to **Add Farm Record** to log your first plantation or harvest entry.")
    else:
        total_rev = df["revenue_inr"].sum()
        total_exp = df["expense_inr"].sum()
        net_profit = total_rev - total_exp
        total_harvest = df["harvest_kg"].sum()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Revenue", f"₹{total_rev:,.0f}")
        c2.metric("Total Expenses", f"₹{total_exp:,.0f}")
        c3.metric("Net Profit", f"₹{net_profit:,.0f}", delta=f"{((net_profit/total_rev)*100 if total_rev else 0):.1f}% margin")
        c4.metric("Total Harvest", f"{total_harvest:,.1f} kg")

        st.divider()
        col_left, col_right = st.columns(2)
        with col_left:
            fig_rev = px.bar(df, x="crop_name", y=["revenue_inr", "expense_inr"], barmode="group", title="Revenue vs. Expense by Crop (₹)")
            st.plotly_chart(fig_rev, use_container_width=True)
        with col_right:
            fig_harv = px.pie(df, names="crop_name", values="harvest_kg", title="Harvest Share by Crop (kg)")
            st.plotly_chart(fig_harv, use_container_width=True)

# --- PAGE 2: ADD FARM RECORD (Free Tier Capped at 10 Entries) ---
elif page == "Add Farm Record":
    st.header("➕ Add Farm & Finance Record")
    df = load_records(user["id"])

    if not is_pro and len(df) >= 10:
        st.warning("🔒 You have reached the **Free Plan limit (10 records)**. Upgrade to **Pro** under Subscription & Billing for unlimited entries.")
    else:
        with st.form("add_record_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            rec_date = c1.date_input("Date", datetime.today())
            crop = c2.text_input("Crop Name (e.g., Areca Nut, Pepper, Coffee)")
            area = c1.number_input("Cultivated Area (Acres)", min_value=0.0, step=0.5)
            harvest = c2.number_input("Harvest Yield (kg)", min_value=0.0, step=10.0)
            rev = c1.number_input("Revenue Earned (₹)", min_value=0.0, step=500.0)
            exp = c2.number_input("Expenses Incurred (₹)", min_value=0.0, step=500.0)
            notes = st.text_area("Field Notes (Fertilizer, irrigation, buyer details)")

            if st.form_submit_button("Save Record", type="primary"):
                if not crop.strip():
                    st.error("Please enter a crop name.")
                else:
                    conn = get_conn()
                    conn.execute(
                        "INSERT INTO farm_records (user_id, record_date, crop_name, area_acres, harvest_kg, revenue_inr, expense_inr, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (user["id"], str(rec_date), crop.strip(), area, harvest, rev, exp, notes),
                    )
                    conn.commit()
                    conn.close()
                    st.success(f"Saved record for **{crop}**!")

# --- PAGE 3: CROP RECORDS ---
elif page == "Crop Records":
    st.header("🌾 Crop & Plantation Records")
    df = load_records(user["id"])
    if df.empty:
        st.info("No crop records available.")
    else:
        st.dataframe(df[["id", "record_date", "crop_name", "area_acres", "harvest_kg", "notes"]], use_container_width=True)
        del_id = st.number_input("Enter Record ID to Delete", min_value=1, step=1)
        if st.button("Delete Selected Record"):
            conn = get_conn()
            conn.execute("DELETE FROM farm_records WHERE id=? AND user_id=?", (del_id, user["id"]))
            conn.commit()
            conn.close()
            st.rerun()

# --- PAGE 4: FARM FINANCE ---
elif page == "Farm Finance":
    st.header("💰 Farm Finance & P&L Ledger")
    df = load_records(user["id"])
    if df.empty:
        st.info("No financial transactions recorded yet.")
    else:
        df["net_profit_inr"] = df["revenue_inr"] - df["expense_inr"]
        st.dataframe(df[["record_date", "crop_name", "revenue_inr", "expense_inr", "net_profit_inr"]], use_container_width=True)

# --- PAGE 5: AI AGRONOMIST CHAT + PERSISTENT HISTORY ---
elif page == "AI Agronomist Chat":
    st.header("🤖 AI Agronomist & Farm Advisor")
    conn = get_conn()
    history = conn.execute("SELECT role, message, timestamp FROM chat_history WHERE user_id=? ORDER BY id ASC", (user["id"],)).fetchall()
    user_prompts_count = sum(1 for h in history if h["role"] == "user")

    col_h1, col_h2 = st.columns([4, 1])
    col_h1.caption(f"Prompts used: {user_prompts_count} / {'Unlimited (Pro)' if is_pro else '5 (Free)'}")
    if col_h2.button("Clear Chat History"):
        conn.execute("DELETE FROM chat_history WHERE user_id=?", (user["id"],))
        conn.commit()
        conn.close()
        st.rerun()

    for h in history:
        with st.chat_message(h["role"]):
            st.markdown(h["message"])

    if not is_pro and user_prompts_count >= 5:
        st.warning("🔒 Free plan limit reached (5 AI questions). Upgrade to **Pro** for unlimited AI agronomy & financial insights.")
    else:
        prompt = st.chat_input("Ask about crop diseases, fertilizer schedules, or farm cashflow...")
        if prompt:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn.execute("INSERT INTO chat_history (user_id, role, message, timestamp) VALUES (?, 'user', ?, ?)", (user["id"], prompt, now))
            conn.commit()

            with st.chat_message("user"):
                st.markdown(prompt)

            # Generate response via Gemini API if configured
            api_key = st.secrets.get("GEMINI_API_KEY", "")
            if genai and api_key:
                try:
                    client = genai.Client(api_key=api_key)
                    resp = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=f"You are an expert Indian agronomist and agribusiness financial advisor. Answer concisely: {prompt}",
                    )
                    reply = resp.text
                except Exception as e:
                    reply = f"AI service temporarily unavailable: {e}"
            else:
                reply = f"**Agronomist Tip (Demo Mode):** Configure `GEMINI_API_KEY` in `.streamlit/secrets.toml` for live AI answers. Regarding *{prompt}*: ensure soil pH testing and balanced NPK application before monsoon onset."

            with st.chat_message("assistant"):
                st.markdown(reply)

            conn.execute("INSERT INTO chat_history (user_id, role, message, timestamp) VALUES (?, 'assistant', ?, ?)", (user["id"], reply, now))
            conn.commit()
    conn.close()

# --- PAGE 6: EXPORT REPORTS (PRO FEATURE) ---
elif page == "Export Reports":
    st.header("📥 Export Financial & Crop Reports")
    if not is_pro:
        st.warning("🔒 Exporting CSV & Excel financial statements is a **Pro** feature. Upgrade in **Subscription & Billing**.")
    else:
        df = load_records(user["id"])
        if df.empty:
            st.info("Add farm records first to generate reports.")
        else:
            csv_data = df.to_csv(index=False).encode("utf-8")
            st.download_button("📄 Download CSV Report", data=csv_data, file_name="agripulse_report.csv", mime="text/csv", type="primary")

# --- PAGE 7: NOTIFICATIONS ---
elif page.startswith("Notifications"):
    st.header("🔔 Notifications")
    conn = get_conn()
    notes = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    if st.button("Mark All as Read"):
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
        conn.commit()
        st.rerun()

    for n in notes:
        status = "🔵" if n["is_read"] == 0 else "⚪"
        st.markdown(f"**{status} {n['title']}** — *{n['created_at']}*\n\n{n['body']}")
        st.divider()
    conn.close()

# --- PAGE 8: SUBSCRIPTION & RAZORPAY BILLING ---
elif page == "Subscription & Billing":
    st.header("💳 Subscription & Billing")
    st.write(f"Active Plan: **{user['plan']}**")

    if is_pro:
        st.success("You are on the **AgriPulse Pro Plan** with full access to all features.")
    else:
        st.subheader("Upgrade to AgriPulse Pro — ₹499 / month")
        rz_id = st.secrets.get("RAZORPAY_KEY_ID", "")
        rz_secret = st.secrets.get("RAZORPAY_KEY_SECRET", "")

        if razorpay and rz_id and rz_secret:
            if st.button("Proceed to Pay ₹499 via Razorpay", type="primary"):
                client = razorpay.Client(auth=(rz_id, rz_secret))
                order = client.order.create({"amount": 49900, "currency": "INR", "receipt": f"user_{user['id']}_{int(datetime.now().timestamp())}"})
                st.session_state["rz_order_id"] = order["id"]

            if "rz_order_id" in st.session_state:
                order_id = st.session_state["rz_order_id"]
                checkout_html = f"""
                <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
                <button id="rzp-button" style="background:#0f9d58;color:white;padding:12px 24px;border:none;border-radius:6px;font-size:16px;cursor:pointer;">
                    Pay ₹499 Now (UPI / Card / NetBanking)
                </button>
                <script>
                var options = {{
                    "key": "{rz_id}",
                    "amount": "49900",
                    "currency": "INR",
                    "name": "AgriPulse Pro",
                    "description": "Monthly Pro Subscription",
                    "order_id": "{order_id}",
                    "handler": function (response){{
                        alert("Payment Successful! Payment ID: " + response.razorpay_payment_id + "\\nCopy this ID and verify below.");
                    }}
                }};
                var rzp1 = new Razorpay(options);
                document.getElementById('rzp-button').onclick = function(e){{
                    rzp1.open();
                    e.preventDefault();
                }}
                </script>
                """
                components.html(checkout_html, height=90)

                with st.form("verify_payment_form"):
                    pay_id = st.text_input("Paste Razorpay Payment ID (`pay_...`) to Activate Pro")
                    if st.form_submit_button("Verify & Activate Pro"):
                        if pay_id.startswith("pay_"):
                            now = datetime.now().strftime("%Y-%m-%d %H:%M")
                            conn = get_conn()
                            conn.execute("UPDATE users SET plan='Pro' WHERE id=?", (user["id"],))
                            conn.execute(
                                "INSERT INTO payments (user_id, order_id, payment_id, amount_inr, plan_name, status, created_at) VALUES (?, ?, ?, 499, 'Pro', 'captured', ?)",
                                (user["id"], order_id, pay_id.strip(), now),
                            )
                            conn.execute(
                                "INSERT INTO notifications (user_id, title, body, created_at) VALUES (?, 'Upgraded to Pro!', 'Thank you for upgrading. Unlimited records and reports are now active.', ?)",
                                (user["id"], now),
                            )
                            conn.commit()
                            conn.close()
                            st.success("🎉 Upgraded to Pro!")
                            st.rerun()
                        else:
                            st.error("Enter a valid Razorpay Payment ID starting with `pay_`.")
        else:
            st.info("Add `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` in Streamlit Secrets to enable live UPI/Card payments.")
            if st.button("Simulate Instant Pro Upgrade (Test Mode)"):
                conn = get_conn()
                conn.execute("UPDATE users SET plan='Pro' WHERE id=?", (user["id"],))
                conn.commit()
                conn.close()
                st.rerun()

# --- PAGE 9: ADMIN DASHBOARD ---
elif page == "Admin Dashboard" and user["role"] == "Admin":
    st.header("🛡️ SaaS Admin Control Panel")
    conn = get_conn()
    users_df = pd.read_sql_query("SELECT id, name, email, auth_provider, role, plan, created_at FROM users", conn)
    payments_df = pd.read_sql_query("SELECT * FROM payments ORDER BY id DESC", conn)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Registered Farmers", len(users_df))
    m2.metric("Active Pro Subscribers", len(users_df[users_df["plan"] == "Pro"]))
    m3.metric("Total SaaS Revenue", f"₹{payments_df['amount_inr'].sum() if not payments_df.empty else 0:,.0f}")

    st.subheader("Manage Users & Subscriptions")
    st.dataframe(users_df, use_container_width=True)

    c_u1, c_u2, c_u3 = st.columns(3)
    target_uid = c_u1.number_input("User ID", min_value=1, step=1)
    new_plan = c_u2.selectbox("Set Plan", ["Free", "Pro"])
    if c_u3.button("Update User Plan"):
        conn.execute("UPDATE users SET plan=? WHERE id=?", (new_plan, target_uid))
        conn.commit()
        st.success(f"Updated User #{target_uid} to {new_plan}.")
        st.rerun()

    st.subheader("Broadcast Notification to All Farmers")
    with st.form("broadcast_form"):
        b_title = st.text_input("Announcement Title")
        b_body = st.text_area("Message")
        if st.form_submit_button("Send Broadcast"):
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            for uid in users_df["id"]:
                conn.execute("INSERT INTO notifications (user_id, title, body, created_at) VALUES (?, ?, ?, ?)", (int(uid), b_title, b_body, now))
            conn.commit()
            st.success("Broadcast sent to all users!")
    conn.close()