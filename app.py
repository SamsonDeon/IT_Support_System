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
app.secret_key = os.environ.get("SECRET_KEY", "dev_fallback_key")

DATABASE = "database.db"


# ================= DATABASE =================
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# ================= AUDIT LOG =================
def log_action(action, target_user=None):
    if "user" not in session:
        return

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


# ================= LOGIN =================
@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()
        cur = db.cursor()

        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cur.fetchone()
        db.close()

        if user and check_password_hash(user["password"], password):

            session["user"] = user["username"]
            session["role"] = user["role"]

            return redirect(url_for("dashboard"))

        else:
            flash("Invalid username or password")

    return render_template("login.html")


# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ================= DASHBOARD =================
@app.route("/dashboard")
def dashboard():

    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM issues")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE status='Open'")
    pending = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE status='Closed'")
    solved = cur.fetchone()[0]

    monthly_data = []

    for month in range(1, 13):

        cur.execute("""
        SELECT COUNT(*) FROM issues
        WHERE strftime('%m', date_reported) = ?
        """, (f"{month:02}",))

        monthly_data.append(cur.fetchone()[0])

    db.close()

    percentage = 0
    if total > 0:
        percentage = round((solved / total) * 100, 2)

    return render_template(
        "dashboard.html",
        total=total,
        pending=pending,
        solved=solved,
        percentage=percentage,
        monthly_data=monthly_data
    )


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

        log_action("Logged Issue")

        return redirect(url_for("view_issues"))

    return render_template("log_issue.html")


# ================= VIEW ISSUES =================
@app.route("/view_issues")
def view_issues():

    if "user" not in session:
        return redirect(url_for("login"))

    search = request.args.get("search")
    filter_type = request.args.get("filter", "all")

    db = get_db()
    cur = db.cursor()

    query = "SELECT * FROM issues"
    conditions = []
    params = []

    # ================= SEARCH =================
    if search:
        conditions.append("title LIKE ?")
        params.append(f"%{search}%")

    # ================= DATE FILTER =================
    if filter_type == "today":
        conditions.append("date(date_reported) = date('now')")

    elif filter_type == "month":
        conditions.append("strftime('%Y-%m', date_reported) = strftime('%Y-%m','now')")

    elif filter_type == "year":
        conditions.append("strftime('%Y', date_reported) = strftime('%Y','now')")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY id DESC"

    cur.execute(query, params)
    issues = cur.fetchall()

    # ================= SLA CALCULATION =================
    issues_with_sla = []

    for issue in issues:

        issue = dict(issue)

        try:
            reported_time = datetime.strptime(issue["date_reported"], "%Y-%m-%d %H:%M")

            if issue.get("status") == "Closed" and issue.get("date_closed"):
                closed_time = datetime.strptime(issue["date_closed"], "%Y-%m-%d %H:%M")
                sla = closed_time - reported_time
            else:
                sla = datetime.now() - reported_time

            issue["sla_hours"] = round(sla.total_seconds() / 3600, 2)

        except:
            issue["sla_hours"] = 0

        issues_with_sla.append(issue)

    # ================= TECHNICIANS =================
    cur.execute("SELECT username FROM users WHERE role='Technician'")
    technicians = cur.fetchall()

    total = len(issues_with_sla)
    open_count = len([i for i in issues_with_sla if i["status"] == "Open"])
    closed_count = len([i for i in issues_with_sla if i["status"] == "Closed"])

    open_percent = round((open_count/total)*100,2) if total>0 else 0
    closed_percent = round((closed_count/total)*100,2) if total>0 else 0

    db.close()

    return render_template(
        "view_issues.html",
        issues=issues_with_sla,
        technicians=technicians,
        open_percent=open_percent,
        closed_percent=closed_percent
    )

# ================= ASSIGN ISSUE =================
@app.route("/assign_issue/<int:issue_id>", methods=["POST"])
def assign_issue(issue_id):

    if "user" not in session:
        return redirect(url_for("login"))

    technician = request.form.get("technician")

    db = get_db()
    cur = db.cursor()

    cur.execute("""
    UPDATE issues
    SET assigned_to=?
    WHERE id=?
    """,(technician, issue_id))

    db.commit()
    db.close()

    log_action("Assigned Issue", technician)

    return redirect(url_for("view_issues"))


# ================= CLOSE ISSUE =================
@app.route("/close_issue/<int:issue_id>", methods=["POST"])
def close_issue(issue_id):

    db = get_db()
    cur = db.cursor()

    date_closed = datetime.now().strftime("%Y-%m-%d %H:%M")

    cur.execute("""
    UPDATE issues
    SET status='Closed', date_closed=?
    WHERE id=?
    """,(date_closed, issue_id))

    db.commit()
    db.close()

    log_action("Closed Issue")

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
    """,(issue_id,))

    db.commit()
    db.close()

    log_action("Reopened Issue")

    return redirect(url_for("view_issues"))


# ================= MANAGE USERS =================
@app.route("/manage_users")
def manage_users():

    if session.get("role") != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT username, role FROM users")
    users = cur.fetchall()

    db.close()

    return render_template("manage_users.html", users=users)


# ================= SIGNUP =================
@app.route("/signup", methods=["GET","POST"])
def signup():

    if session.get("role") != "Admin":
        return redirect(url_for("dashboard"))

    if request.method == "POST":

        username = request.form.get("username")
        password = generate_password_hash(request.form.get("password"))
        role = request.form.get("role")

        db = get_db()
        cur = db.cursor()

        cur.execute("""
        INSERT INTO users (username,password,role)
        VALUES (?,?,?)
        """,(username,password,role))

        db.commit()
        db.close()

        log_action("Created User", username)

        return redirect(url_for("manage_users"))

    return render_template("signup.html")


# ================= AUDIT LOGS =================
@app.route("/audit_logs")
def audit_logs():

    if session.get("role") != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT * FROM audit_logs ORDER BY id DESC")
    logs = cur.fetchall()

    db.close()

    return render_template("audit_logs.html", logs=logs)


# ================= EXPORT =================
@app.route("/export_excel")
def export_excel():

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT * FROM issues")
    issues = cur.fetchall()

    db.close()

    wb = Workbook()
    ws = wb.active

    ws.append(["ID","Title","Category","Status","Assigned To","Date Reported"])

    for issue in issues:
        ws.append([
            issue["id"],
            issue["title"],
            issue["category"],
            issue["status"],
            issue["assigned_to"],
            issue["date_reported"]
        ])

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    return send_file(
        stream,
        as_attachment=True,
        download_name="IT_Issues_Report.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


if __name__ == "__main__":
    app.run(debug=True)