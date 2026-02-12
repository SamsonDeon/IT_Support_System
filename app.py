from model.classifier import classify_issue
from flask import Flask, render_template, request, redirect, url_for, session


import sqlite3
from datetime import datetime

app = Flask(__name__)
app.secret_key = "secret123"

def get_db():
    return sqlite3.connect("database.db")

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form["username"]
        pwd = request.form["password"]

        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=?", (user, pwd))
        result = cur.fetchone()

        if result:
            session["user"] = user
            return redirect("/dashboard")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM issues")
    total_issues = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE status='Pending'")
    pending_issues = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE category='Hardware'")
    hardware_issues = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE category='Software'")
    software_issues = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM issues WHERE category='Network'")
    network_issues = cur.fetchone()[0]

    return render_template(
        "dashboard.html",
        total=total_issues,
        pending=pending_issues,
        hardware=hardware_issues,
        software=software_issues,
        network=network_issues
    )


from model.classifier import classify_issue

@app.route('/log_issue', methods=['GET', 'POST'])
def log_issue():
    if request.method == 'POST':
        description = request.form['description']
        source = request.form['source']

        # 🔹 AI CLASSIFICATION (THIS WAS MISSING)
        category = classify_issue(description)
        print("PREDICTED CATEGORY:", category)

        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO issues (description, category, source) VALUES (?, ?, ?)",
            (description, category, source)
        )

        conn.commit()
        conn.close()

        return redirect(url_for('view_issues'))

    return render_template('log_issue.html')

@app.route("/view_issues")
def view_issues():
    if "user" not in session:
        return redirect("/")

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM issues")
    issues = cur.fetchall()

    return render_template("view_issues.html", issues=issues)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)


def create_default_user():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        password TEXT
    )
    """)

    cursor.execute("SELECT * FROM users WHERE username='admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", ("admin", "admin123"))

    conn.commit()
    conn.close()

create_default_user()

