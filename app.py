import csv
import io
import os
import random
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file

app = Flask(__name__)
app.secret_key = "change-me"  # replace in production


# --- Persistence (SQLite) ----------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", "data/scoreboard.sqlite")

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                wins INTEGER NOT NULL DEFAULT 0,
                games_played INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()


def record_game_result(winner_name, participant_names):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        for n in participant_names:
            conn.execute(
                "INSERT OR IGNORE INTO players (name, wins, games_played, created_at, updated_at) VALUES (?, 0, 0, ?, ?)",
                (n, now, now),
            )
        conn.executemany(
            "UPDATE players SET games_played = games_played + 1, updated_at = ? WHERE name = ?",
            [(now, n) for n in participant_names],
        )
        conn.execute(
            "UPDATE players SET wins = wins + 1, updated_at = ? WHERE name = ?",
            (now, winner_name),
        )
        conn.commit()

def top_scoreboard(limit=50):
    with get_conn() as conn:
        cur = conn.execute("""
            SELECT name, wins, games_played,
                   CASE WHEN games_played > 0 THEN ROUND(100.0 * wins / games_played, 1) ELSE 0 END AS win_rate
            FROM players
            ORDER BY wins DESC, name ASC
            LIMIT ?
        """, (limit,))
        return cur.fetchall()

def export_scoreboard_csv():
    rows = top_scoreboard(limit=100000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "wins", "games_played", "win_rate_percent"])
    for r in rows:
        writer.writerow([r["name"], r["wins"], r["games_played"], r["win_rate"]])
    output.seek(0)
    return output

import os
from flask import abort

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")  # set this on Render

def require_admin():
    token = request.args.get("token") or request.headers.get("X-Admin-Token")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        abort(403, description="Forbidden")

@app.route("/admin/reset", methods=["GET"])
def admin_reset():
    """Delete ALL players/scores."""
    require_admin()
    with get_conn() as conn:
        conn.execute("DELETE FROM players")
        conn.commit()
    return "✅ Scoreboard cleared."

@app.route("/admin/delete_player", methods=["GET"])
def admin_delete_player():
    """Delete one player by exact name: /admin/delete_player?name=Alice&token=..."""
    require_admin()
    name = (request.args.get("name") or "").strip()
    if not name:
        abort(400, description="Missing ?name=")
    with get_conn() as conn:
        conn.execute("DELETE FROM players WHERE name = ?", (name,))
        conn.commit()
    return f"🗑️ Deleted player: {name}"


# --- Game logic --------------------------------------------------------------

DEFAULT_QUESTIONS = [
    {"q": "2 + 2", "a": "4"},
    {"q": "9 × 4", "a": "36"},
    {"q": "7 × 8", "a": "56"},
    {"q": "9 ÷ 3", "a": "3"},
    {"q": "√81", "a": "9"},
    {"q": "√49", "a": "7"},
    {"q": "12 ÷ 4", "a": "3"},
    {"q": "15 + 5", "a": "20"},
    {"q": "11 × 8", "a": "88"},
    {"q": "14²", "a": "196"},
]

BOARD_SIZE = 24
MAX_PLAYERS = 4
MAX_QUESTIONS_IN_SESSION = 300

def new_game():
    session.clear()
    session["players"] = []
    session["turn"] = 0
    session["awaiting_answer"] = False
    session["current_question"] = None
    session["last_roll"] = None
    session["message"] = "Set up players to start."
    session["questions"] = list(DEFAULT_QUESTIONS)

def current_player():
    players = session.get("players", [])
    if not players:
        return None
    return players[session.get("turn", 0) % len(players)]

def next_turn():
    session["turn"] = (session["turn"] + 1) % len(session["players"])

@app.route("/", methods=["GET"])
def index():
    if "players" not in session:
        new_game()
    state = {
        "players": session["players"],
        "turn": session["turn"],
        "awaiting_answer": session["awaiting_answer"],
        "current_question": session.get("current_question"),
        "last_roll": session.get("last_roll"),
        "message": session.get("message", ""),
        "board_size": BOARD_SIZE,
        "questions_count": len(session.get("questions", [])),
        "scoreboard": top_scoreboard(),
    }
    winner = None
    for p in session["players"]:
        if p["pos"] >= BOARD_SIZE:
            winner = p["name"]
            break
    state["winner"] = winner
    return render_template("index.html", **state)

@app.route("/setup", methods=["POST"])
def setup():
    names = []
    for i in range(1, MAX_PLAYERS + 1):
        name = request.form.get(f"name{i}", "").strip()
        if name:
            names.append(name[:20])
    if len(names) == 0:
        flash("Enter at least one player name.")
        return redirect(url_for("index"))
    if len(names) > MAX_PLAYERS:
        flash("Maximum 4 players.")
        return redirect(url_for("index"))
    session["players"] = [{"name": n, "pos": 0} for n in names]
    session["turn"] = 0
    session["message"] = f"Game ready! {names[0]}&#39;s turn. Roll to start."
    return redirect(url_for("index"))

@app.route("/roll", methods=["POST"])
def roll():
    if not session.get("players"):
        flash("Set up players first.")
        return redirect(url_for("index"))
    if session.get("awaiting_answer"):
        return redirect(url_for("index"))
    roll = random.randint(1, 6)
    session["last_roll"] = roll
    questions = session.get("questions", DEFAULT_QUESTIONS)
    question = random.choice(questions)
    session["current_question"] = question
    session["awaiting_answer"] = True
    session["message"] = f"{current_player()['name']} rolled a {roll}. Answer to move!"
    return redirect(url_for("index"))

@app.route("/answer", methods=["POST"])
def answer():
    if not session.get("awaiting_answer"):
        return redirect(url_for("index"))
    user_answer = request.form.get("answer", "").strip()
    correct_answer = str(session["current_question"]["a"]).strip()
    roll = session["last_roll"] or 0

    player = current_player()
    if user_answer == correct_answer:
        player["pos"] = min(BOARD_SIZE, player["pos"] + roll)
        session["message"] = f"Correct, {player['name']}! Move forward {roll}."
    else:
        player["pos"] = max(0, player["pos"] - 1)
        session["message"] = f"Oops, {player['name']}! Correct was {correct_answer}. Move back 1."

    session["awaiting_answer"] = False
    session["current_question"] = None
    session["last_roll"] = None

    if player["pos"] >= BOARD_SIZE:
        participant_names = [p["name"] for p in session["players"]]
        record_game_result(player["name"], participant_names)
        flash(f"Recorded win for {player['name']}!")
        return redirect(url_for("index"))

    next_turn()
    session["message"] += f" Next: {current_player()['name']}."
    return redirect(url_for("index"))

@app.route("/export_scoreboard.csv")
def export_csv():
    buf = export_scoreboard_csv()
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="scoreboard.csv",
    )

@app.route("/reset", methods=["POST"])
def reset():
    new_game()
    return redirect(url_for("index"))

@app.route("/upload_questions", methods=["POST"])
def upload_questions():
    new_items = []
    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        data = f.read().decode("utf-8", errors="ignore")
        new_items.extend(parse_csv_text(data))
    pasted = request.form.get("pasted", "").strip()
    if pasted:
        new_items.extend(parse_csv_text(pasted))
    if not new_items:
        flash("No questions found. Use CSV with headers q,a or question,answer.")
        return redirect(url_for("index"))
    qlist = session.get("questions", [])
    combined = (qlist + new_items)[:MAX_QUESTIONS_IN_SESSION]
    session["questions"] = combined
    flash(f"Loaded {len(new_items)} questions. Now {len(combined)} total.")
    return redirect(url_for("index"))

def parse_csv_text(text):
    text = text.strip()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    items = []
    for row in reader:
        q = (row.get("q") or row.get("question") or "").strip()
        a = (row.get("a") or row.get("answer") or "").strip()
        if q and a:
            items.append({"q": q, "a": a})
    return items

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)