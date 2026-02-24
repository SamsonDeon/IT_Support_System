import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT
)
""")

cursor.execute("""
CREATE TABLE issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    description TEXT,
    source TEXT,
    category TEXT,
    status TEXT,
    date_reported TEXT,
    date_closed TEXT
)
""")

# Create default login
cursor.execute("""
INSERT INTO users (username, password)
VALUES ('admin', 'admin123')
""")

conn.commit()
conn.close()

print("Database created successfully.")
