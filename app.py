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

# ================= SECURITY =================
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

    # Monthly performance
    monthly_data = []
    for month in range(1, 13):
        cur.execute("""
            SELECT COUNT(*) FROM issues
            WHERE strftime('%m', date_reported) = ?
        """, (f"{month:02}",))
        monthly_data.append(cur.fetchone()[0])

    db.close()

    # 🔥 Calculate percentage solved
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

        send_email("New Issue Logged", f"Issue: {title}")
        log_action("Logged Issue")

        return redirect(url_for("view_issues"))

    return render_template("log_issue.html")


# ================= VIEW ISSUES =================
@app.route("/view_issues")
def view_issues():
    if "user" not in session:
        return redirect(url_for("login"))

    filter_type = request.args.get("filter", "all")

    db = get_db()
    cur = db.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    current_month = datetime.now().strftime("%m")
    current_year = datetime.now().strftime("%Y")

    if filter_type == "today":
        cur.execute("SELECT * FROM issues WHERE date(date_reported)=?", (today,))
    elif filter_type == "month":
        cur.execute("SELECT * FROM issues WHERE strftime('%m', date_reported)=?", (current_month,))
    elif filter_type == "year":
        cur.execute("SELECT * FROM issues WHERE strftime('%Y', date_reported)=?", (current_year,))
    else:
        cur.execute("SELECT * FROM issues ORDER BY id DESC")

    issues = cur.fetchall()
    db.close()

    return render_template(
        "view_issues.html",
        issues=issues,
        filter_type=filter_type
    )


# ================= CLOSE ISSUE =================
@app.route("/close_issue/<int:issue_id>", methods=["POST"])
def close_issue(issue_id):
    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    date_closed = datetime.now().strftime("%Y-%m-%d %H:%M")

    cur.execute("""
        UPDATE issues
        SET status='Closed', date_closed=?
        WHERE id=?
    """, (date_closed, issue_id))

    db.commit()
    db.close()

    send_email("Issue Closed", f"Issue {issue_id} closed.")
    log_action("Closed Issue")

    return redirect(url_for("view_issues"))


# ================= REOPEN ISSUE =================
@app.route("/reopen_issue/<int:issue_id>", methods=["POST"])
def reopen_issue(issue_id):
    if "user" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        UPDATE issues
        SET status='Open', date_closed=NULL
        WHERE id=?
    """, (issue_id,))

    db.commit()
    db.close()

    log_action("Reopened Issue")
    return redirect(url_for("view_issues"))


# ================= EXPORT EXCEL =================
@app.route("/export_excel")
def export_excel():
    if "user" not in session:
        return redirect(url_for("login"))

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