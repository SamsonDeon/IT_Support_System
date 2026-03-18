from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
import psycopg2.extras
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
import os
import io

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_fallback_key")

DATABASE_URL = os.environ.get("DATABASE_URL")


# ================= DATABASE =================
from flask import g

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ================= AUDIT LOG =================

@app.route("/audit_logs")
def audit_logs():

    if "user" not in session or session["role"] != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT * FROM audit_logs
        ORDER BY id DESC
    """)

    logs = cur.fetchall()

    cur.close()
    db.close()

    return render_template("audit_logs.html", logs=logs)

#==================SIGNUP =================

@app.route("/signup", methods=["GET", "POST"])
def signup():

    if "user" not in session or session["role"] != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]
        role = request.form["role"]

        try:
            cur.execute(
                "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
                (username, password, role)
            )
            db.commit()

            flash("User created successfully")

        except Exception as e:
            db.rollback()
            flash("User already exists")

    db.close()

    return render_template("signup.html")

# ================= MANAGE USERS =================
@app.route("/manage_users")
def manage_users():

    if "user" not in session or session["role"] != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT id, username, role FROM users ORDER BY username")
    users = cur.fetchall()

    cur.close()
    db.close()

    return render_template("manage_users.html", users=users)
# ================= LOGIN =================
@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()
        cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()

        cur.close()
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

 # TOTAL, OPEN, CLOSED (ONE QUERY)
    cur.execute("""
    SELECT 
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE status='Open') AS open,
        COUNT(*) FILTER (WHERE status='Closed') AS closed
    FROM issues
    """)

    result = cur.fetchone()
    total = result[0]
    pending = result[1]
    solved = result[2]

    # MONTHLY DATA (ONE QUERY)
    cur.execute("""
    SELECT EXTRACT(MONTH FROM date_reported) AS month, COUNT(*) 
        FROM issues
    GROUP BY month
    """)

    monthly_raw = cur.fetchall()

    # fill missing months
    monthly_data = [0] * 12
    for row in monthly_raw:
        month_index = int(row[0]) - 1
        monthly_data[month_index] = row[1]

    cur.close()
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

        date_reported = datetime.now()

        db = get_db()
        cur = db.cursor()

        cur.execute("""
        INSERT INTO issues (title, description, source, category, status, date_reported)
        VALUES (%s,%s,%s,%s,'Open',%s)
        """, (title, description, source, "General", date_reported))

        db.commit()
        cur.close()
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
    status_filter = request.args.get("status")   # ← NEW (from dashboard pie chart)

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    query = "SELECT * FROM issues"
    conditions = []
    params = []

    if search:
        conditions.append("title ILIKE %s")
        params.append(f"%{search}%")

    # ================= STATUS FILTER (NEW) =================
    if status_filter:
        conditions.append("status = %s")
        params.append(status_filter)
    # =======================================================

    if filter_type == "today":
        conditions.append("DATE(date_reported) = CURRENT_DATE")

    elif filter_type == "month":
        conditions.append("DATE_TRUNC('month', date_reported) = DATE_TRUNC('month', CURRENT_DATE)")

    elif filter_type == "year":
        conditions.append("DATE_TRUNC('year', date_reported) = DATE_TRUNC('year', CURRENT_DATE)")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY id DESC"

    cur.execute(query, params)
    issues = cur.fetchall()

    issues_with_sla = []

    for issue in issues:

        issue = dict(issue)

        try:

            reported_time = issue["date_reported"]

            if issue.get("status") == "Closed" and issue.get("date_closed"):
                closed_time = issue["date_closed"]
                sla = closed_time - reported_time
            else:
                sla = datetime.now() - reported_time

            issue["sla_hours"] = round(sla.total_seconds() / 3600, 2)

            if issue["status"] == "Open" and issue["sla_hours"] > 24:
                issue["sla_breach"] = True
            else:
                issue["sla_breach"] = False

        except:
            issue["sla_hours"] = 0
            issue["sla_breach"] = False

        issues_with_sla.append(issue)

    cur.execute("SELECT username FROM users WHERE role='Technician'")
    technicians = cur.fetchall()

    total = len(issues_with_sla)
    open_count = len([i for i in issues_with_sla if i["status"] == "Open"])
    closed_count = len([i for i in issues_with_sla if i["status"] == "Closed"])

    open_percent = round((open_count/total)*100,2) if total>0 else 0
    closed_percent = round((closed_count/total)*100,2) if total>0 else 0

    cur.close()
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
    SET assigned_to=%s
    WHERE id=%s
    """,(technician, issue_id))

    db.commit()
    cur.close()
    db.close()

    log_action("Assigned Issue", technician)

    return redirect(url_for("view_issues"))


# ================= CLOSE ISSUE =================
@app.route("/close_issue/<int:issue_id>", methods=["POST"])
def close_issue(issue_id):

    db = get_db()
    cur = db.cursor()

    date_closed = datetime.now()

    cur.execute("""
    UPDATE issues
    SET status='Closed', date_closed=%s
    WHERE id=%s
    """,(date_closed, issue_id))

    db.commit()
    cur.close()
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
    WHERE id=%s
    """,(issue_id,))

    db.commit()
    cur.close()
    db.close()

    log_action("Reopened Issue")

    return redirect(url_for("view_issues"))


# ================= EXPORT =================
@app.route("/export_excel")
def export_excel():

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT * FROM issues")
    issues = cur.fetchall()

    cur.close()
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
            str(issue["date_reported"])
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


# ================= AUTO CREATE TABLES =================
def init_db():

    db = get_db()
    cur = db.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS issues (
        id SERIAL PRIMARY KEY,
        title TEXT,
        description TEXT,
        source TEXT,
        category TEXT,
        status TEXT,
        assigned_to TEXT,
        date_reported TIMESTAMP,
        date_closed TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id SERIAL PRIMARY KEY,
        action TEXT,
        performed_by TEXT,
        target_user TEXT,
        timestamp TIMESTAMP
    )
    """)

    # ================= DEFAULT ADMIN =================
    cur.execute("SELECT * FROM users WHERE username='admin'")
    admin = cur.fetchone()

    if not admin:

        cur.execute("""
        INSERT INTO users (username,password,role)
        VALUES (%s,%s,%s)
        """, (
            "admin",
            generate_password_hash("admin123"),
            "Admin"
        ))

        print("Default admin created: admin / admin123")

    db.commit()
    cur.close()
    db.close()


# initialize database when app starts
init_db()

if __name__ == "__main__":
    app.run(debug=True)