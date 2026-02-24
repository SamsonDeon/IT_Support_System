from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import io

app = Flask(__name__)
app.secret_key = "super_secret_enterprise_key"

def log_action(action, target_user=None):
    db = get_db()
    cur = db.cursor()

    cur.execute("""
        INSERT INTO audit_logs (action, performed_by, target_user, timestamp)
        VALUES (?, ?, ?, ?)
    """, (
        action,
        session.get("user"),
        target_user,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))

    db.commit()
    db.close()

def check_sla_alerts():
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT * FROM issues WHERE status='Open'")
    issues = cur.fetchall()

    for issue in issues:
        opened_time = datetime.strptime(issue["date_reported"], "%Y-%m-%d %H:%M")
        hours_open = (datetime.now() - opened_time).total_seconds() / 3600

        if hours_open > 24:
            send_email(
                "🚨 SLA ALERT - Issue Over 24 Hours",
                f"""
                Issue ID: {issue['id']}
                Title: {issue['title']}
                Has been open for more than 24 hours.
                Immediate attention required.
                """
            )

    db.close()
# ================= EMAIL CONFIG =================
EMAIL_ADDRESS = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASS")

def send_email(subject, body):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        return

    message = MIMEMultipart()
    message["From"] = EMAIL_ADDRESS
    message["To"] = EMAIL_ADDRESS
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, message.as_string())
        server.quit()
    except Exception as e:
        print("Email failed:", e)

#========PROMOTE AND DEMOT ==========

@app.route("/change_role/<int:user_id>", methods=["POST"])
def change_role(user_id):
    if session.get("role") != "Admin":
        return "Access Denied"

    new_role = request.form.get("role")

    db = get_db()
    cur = db.cursor()

    cur.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))

    cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
    target = cur.fetchone()["username"]

    db.commit()
    db.close()

    log_action(f"Changed role to {new_role}", target)

    return redirect(url_for("manage_users"))

# ================= DATABASE =================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# ================= SIGN UP =================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user" not in session:
        return redirect(url_for("login"))

    # Only Admin can create users
    if session.get("role") != "Admin":
        return "Access Denied"

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        role = request.form.get("role")

        hashed_password = generate_password_hash(password)

        db = get_db()
        cur = db.cursor()

        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        existing = cur.fetchone()

        if existing:
            db.close()
            return "User already exists"

        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (username, hashed_password, role)
        )
        db.commit()
        db.close()

        return redirect(url_for("dashboard"))

    return render_template("signup.html")

@app.route("/manage_users")
def manage_users():
    if session.get("role") != "Admin":
        return "Access Denied"

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, username, role FROM users")
    users = cur.fetchall()
    db.close()

    return render_template("manage_users.html", users=users)

# ================= DELETE_USER =================


@app.route("/delete_user/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if session.get("role") != "Admin":
        return "Access Denied"

    db = get_db()
    cur = db.cursor()

    # Prevent deleting yourself
    cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
    user = cur.fetchone()

    if user and user["username"] == session["user"]:
        db.close()
        return "You cannot delete yourself."

    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    db.close()

    return redirect(url_for("manage_users"))
log_action("Deleted User", target)



# ================= LOGIN =================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username")
        pwd = request.form.get("password")

        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (user,))
        result = cur.fetchone()
        db.close()

        if result and check_password_hash(result["password"], pwd):
            session["user"] = result["username"]
            session["role"] = result["role"]
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password")

    return render_template("login.html")

# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

#==========AUDIT ===================
@app.route("/audit_logs")
def audit_logs():
    if session.get("role") != "Admin":
        return "Access Denied"

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC")
    logs = cur.fetchall()

    db.close()

    return render_template("audit_logs.html", logs=logs)

# ================= DASHBOARD =================
check_sla_alerts()
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM issues")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE status='Open'")
    open_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE status='Closed'")
    closed_count = cur.fetchone()[0]

    db.close()

    return render_template("dashboard.html", total=total, pending=open_count, solved=closed_count)

# ================= LOG ISSUE =================
@app.route("/log_issue", methods=["GET", "POST"])
def log_issue():
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        title = request.form.get("title")
        description = request.form.get("description")
        source = request.form.get("source")

        date_reported = datetime.now().strftime("%Y-%m-%d %H:%M")

        db = get_db()
        cur = db.cursor()
        cur.execute("""
            INSERT INTO issues (title, description, source, category, status, date_reported)
            VALUES (?, ?, ?, ?, 'Open', ?)
        """, (title, description, source, "General", date_reported))
        db.commit()
        db.close()

        send_email("New Issue Logged", f"Issue: {title}")

        return redirect(url_for("view_issues"))

    return render_template("log_issue.html")

# ================= VIEW ISSUES =================
@app.route("/view_issues")
def view_issues():
    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    search = request.args.get("search")
    filter_type = request.args.get("filter")

    query = "SELECT * FROM issues WHERE 1=1"
    params = []

    # 🔎 Search
    if search:
        query += " AND (title LIKE ? OR description LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    # 📅 Filters
    if filter_type == "today":
        query += " AND date(date_reported) = date('now')"

    elif filter_type == "week":
        query += " AND strftime('%Y-%W', date_reported) = strftime('%Y-%W', 'now')"

    elif filter_type == "month":
        query += " AND strftime('%Y-%m', date_reported) = strftime('%Y-%m', 'now')"

    elif filter_type == "open":
        query += " AND status='Open'"

    query += " ORDER BY date_reported DESC"

    cur.execute(query, params)
    issues = cur.fetchall()

    # Convert to list for SLA editing
    issues = [dict(issue) for issue in issues]

    # ⏰ SLA Calculation
    for issue in issues:
        if issue["status"] == "Open":
            opened_time = datetime.strptime(issue["date_reported"], "%Y-%m-%d %H:%M")
            issue["sla_hours"] = int((datetime.now() - opened_time).total_seconds() / 3600)
        else:
            issue["sla_hours"] = "-"

    # 📊 Progress bar based on filtered results
    open_count = sum(1 for i in issues if i["status"] == "Open")
    closed_count = sum(1 for i in issues if i["status"] == "Closed")

    total = open_count + closed_count
    open_percent = int((open_count / total) * 100) if total else 0
    closed_percent = 100 - open_percent if total else 0

    # 👨‍💻 Technicians
    cur.execute("SELECT username FROM users WHERE role='Technician'")
    technicians = cur.fetchall()

    db.close()

    return render_template(
        "view_issues.html",
        issues=issues,
        technicians=technicians,
        open_percent=open_percent,
        closed_percent=closed_percent,
        current_filter=filter_type
    )

# ================= ASSIGN ISSUE =================

@app.route("/assign_issue/<int:issue_id>", methods=["POST"])
def assign_issue(issue_id):
    if session.get("role") != "Admin":
        return "Access Denied"

    technician = request.form.get("technician")
    log_action("Assigned Issue", technician)

    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE issues SET assigned_to=? WHERE id=?", (technician, issue_id))
    db.commit()
    db.close()

    send_email("Issue Assigned", f"Issue {issue_id} assigned to {technician}")

    return redirect(url_for("view_issues"))

# ================= CLOSE ISSUE =================
@app.route("/close_issue/<int:issue_id>", methods=["POST"])
def close_issue(issue_id):
    db = get_db()
    cur = db.cursor()

    date_closed = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_action("Closed Issue")

    cur.execute("""
        UPDATE issues
        SET status='Closed', date_closed=?
        WHERE id=?
    """, (date_closed, issue_id))

    db.commit()
    db.close()

    send_email("Issue Closed", f"Issue {issue_id} closed.")

    return redirect(url_for("view_issues"))

# ================= REOPEN ISSUE =================
@app.route("/reopen_issue/<int:issue_id>", methods=["POST"])
def reopen_issue(issue_id):
    db = get_db()
    cur = db.cursor()

    cur.execute("""
        UPDATE issues
        SET status='Open', date_closed=NULL
        WHERE id=?
    """, (issue_id,))

    db.commit()
    db.close()

    return redirect(url_for("view_issues"))

# ================= EXPORT EXCEL =================
@app.route("/export_excel")
def export_excel():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM issues")
    issues = cur.fetchall()
    db.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "IT Issues"

    headers = ["ID", "Title", "Category", "Status", "Assigned To", "Date Reported"]
    ws.append(headers)

    for issue in issues:
        ws.append([
            issue["id"],
            issue["title"],
            issue["category"],
            issue["status"],
            issue["assigned_to"],
            issue["date_reported"]
        ])

    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    return send_file(
        file_stream,
        as_attachment=True,
        download_name="IT_Issues_Report.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

if __name__ == "__main__":
    app.run()