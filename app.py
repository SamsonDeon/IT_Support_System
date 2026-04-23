from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, g, jsonify
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
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ================= AI CATEGORY =================
def detect_category(description):
    text = description.lower()

    if "keyboard" in text or "screen" in text or "power" in text:
        return "Hardware"
    if "software" in text or "app" in text or "crash" in text:
        return "Software"
    if "internet" in text or "wifi" in text or "network" in text:
        return "Network"

    return "General"


# ================= AI ASSISTANT =================
def get_ai_suggestion(description):
    text = description.lower()

    if "not turning on" in text or "power" in text:
        return "Check power cable, switch socket, or try another outlet."
    if "keyboard" in text:
        return "Reconnect keyboard or try another USB port."
    if "crash" in text or "not opening" in text:
        return "Restart the application or reinstall it."
    if "slow" in text:
        return "Restart your computer and close unused programs."
    if "internet" in text or "wifi" in text:
        return "Check your router or reconnect to WiFi."

    return "We've logged your issue. Try the suggestion above while a technician is assigned."


# ================= CHATBOT =================
@app.route("/chatbot", methods=["POST"])
def chatbot():

    if "user" not in session:
        return jsonify({"reply": "Please login first."})

    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "")

    reply = get_ai_suggestion(user_message)

    return jsonify({"reply": reply})


# ================= AUDIT LOG =================
def log_action(action, target_user=None):
    if "user" not in session:
        return

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        INSERT INTO audit_logs (action, performed_by, target_user, timestamp)
        VALUES (%s, %s, %s, %s)
    """, (action, session["user"], target_user, datetime.now()))

    db.commit()
    cur.close()


# ================= SIGNUP =================
@app.route("/signup", methods=["GET", "POST"])
def signup():

    if "user" not in session or session["role"] != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])
        role = request.form["role"]

        try:
            cur.execute(
                "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
                (username, password, role)
            )
            db.commit()
            flash("User created successfully")

        except:
            db.rollback()
            flash("User already exists")

    return render_template("signup.html")


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

        if user and check_password_hash(user["password"], password):
            session["user"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password")

    return render_template("login.html")


# ================= DASHBOARD =================
@app.route("/dashboard")
def dashboard():

    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    cur.execute("""
    SELECT 
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE status='Open'),
        COUNT(*) FILTER (WHERE status='Closed')
    FROM issues
    """)

    total, pending, solved = cur.fetchone()

    cur.execute("""
    SELECT EXTRACT(MONTH FROM date_reported), COUNT(*) 
    FROM issues GROUP BY 1
    """)

    monthly_raw = cur.fetchall()
    monthly_data = [0] * 12

    for m, count in monthly_raw:
        monthly_data[int(m)-1] = count

    cur.close()

    percentage = round((solved / total) * 100, 2) if total > 0 else 0

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

        category = detect_category(description)
        ai_reply = get_ai_suggestion(description)

        db = get_db()
        cur = db.cursor()

        cur.execute("""
        INSERT INTO issues (title, description, source, category, status, date_reported)
        VALUES (%s,%s,%s,%s,'Open',%s)
        """, (title, description, source, category, datetime.now()))

        db.commit()
        cur.close()

        log_action("Logged Issue")

        flash(ai_reply)
        return redirect(url_for("view_issues"))

    return render_template("log_issue.html")


# ================= VIEW ISSUES =================
@app.route("/view_issues")
def view_issues():

    if "user" not in session:
        return redirect(url_for("login"))

    search = request.args.get("search")
    filter_type = request.args.get("filter", "all")
    status_filter = request.args.get("status")
    category_filter = request.args.get("category")

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    query = "SELECT * FROM issues"
    conditions = []
    params = []

    if search:
        conditions.append("title ILIKE %s")
        params.append(f"%{search}%")

    if status_filter:
        conditions.append("status = %s")
        params.append(status_filter)

    if category_filter:
        conditions.append("category = %s")
        params.append(category_filter)

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
            reported = issue["date_reported"]

            if issue.get("status") == "Closed" and issue.get("date_closed"):
                sla = issue["date_closed"] - reported
            else:
                sla = datetime.now() - reported

            issue["sla_hours"] = round(sla.total_seconds() / 3600, 2)
            issue["sla_breach"] = issue["status"] == "Open" and issue["sla_hours"] > 24

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
    """, (technician, issue_id))

    db.commit()
    cur.close()

    log_action("Assigned Issue", technician)

    return redirect(url_for("view_issues"))

#==================CLOSE ISSUE ===============
@app.route("/close_issue/<int:issue_id>", methods=["POST"])
def close_issue(issue_id):

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        UPDATE issues
        SET status='Closed', date_closed=%s
        WHERE id=%s
    """, (datetime.now(), issue_id))

    db.commit()
    cur.close()

    log_action("Closed Issue")

    return redirect(url_for("view_issues"))

#================== RE- OPEN  ===============

@app.route("/reopen_issue/<int:issue_id>", methods=["POST"])
def reopen_issue(issue_id):

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        UPDATE issues
        SET status='Open', date_closed=NULL
        WHERE id=%s
    """, (issue_id,))

    db.commit()
    cur.close()

    log_action("Reopened Issue")

    return redirect(url_for("view_issues"))

#============AUDIT LOGS ===============

@app.route("/audit_logs")
def audit_logs():

    if "user" not in session or session.get("role") != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("""
        SELECT action, performed_by, target_user, timestamp
        FROM audit_logs
        ORDER BY timestamp DESC
    """)

    logs = cur.fetchall()
    cur.close()

    return render_template("audit_logs.html", logs=logs)

#========== MANAGE USERS ============

@app.route("/manage_users")
def manage_users():

    if "user" not in session or session.get("role") != "Admin":
        return redirect(url_for("dashboard"))

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT id, username, role FROM users ORDER BY username")
    users = cur.fetchall()

    cur.close()

    return render_template("manage_users.html", users=users)

# ================= MONTHLY PDF REPORT =================
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

# ================= MONTHLY PDF REPORT =================

@app.route("/monthly_report")
def monthly_report():

    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Get current month issues
    cur.execute("""
    SELECT * FROM issues
    WHERE DATE_TRUNC('month', date_reported) = DATE_TRUNC('month', CURRENT_DATE)
    ORDER BY date_reported DESC
    """)

    issues = cur.fetchall()

    total = len(issues)
    open_count = len([i for i in issues if i["status"] == "Open"])
    closed_count = len([i for i in issues if i["status"] == "Closed"])
    resolution_rate = round((closed_count / total) * 100, 2) if total > 0 else 0

    # Create PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)

    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph("Bellevue IT Support Monthly Report", styles['Title']))
    elements.append(Spacer(1, 12))

    # Summary
    elements.append(Paragraph(f"Total Issues: {total}", styles['Normal']))
    elements.append(Paragraph(f"Open Issues: {open_count}", styles['Normal']))
    elements.append(Paragraph(f"Closed Issues: {closed_count}", styles['Normal']))
    elements.append(Paragraph(f"Resolution Rate: {resolution_rate}%", styles['Normal']))
    elements.append(Spacer(1, 20))

    # Table Data
    data = [["ID", "Title", "Category", "Status", "Assigned To", "Date"]]

    for issue in issues:
        data.append([
            issue["id"],
            issue["title"],
            issue["category"],
            issue["status"],
            issue["assigned_to"] or "Unassigned",
            str(issue["date_reported"])[:10]
        ])

    table = Table(data)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.grey),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID", (0,0), (-1,-1), 1, colors.black),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold")
    ]))

    elements.append(table)

    doc.build(elements)

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="Monthly_Report.pdf",
        mimetype="application/pdf"
    )
# ================= INIT DB =================
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

    cur.execute("SELECT * FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute("""
        INSERT INTO users (username,password,role)
        VALUES (%s,%s,%s)
        """, ("admin", generate_password_hash("admin123"), "Admin"))

    db.commit()
    cur.close()


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True)