# Math Board Game — Multiplayer + Persistent Scoreboard

- 1–4 players, turn order
- Persistent scoreboard (SQLite) with wins, games played, win %
- CSV export of scoreboard
- Import questions via CSV upload or paste (headers: `q,a` or `question,answer`)
- Ready for Render (Procfile + render.yaml). On Render, a disk is mounted at `/var/data`.

## Local run
```
python -m venv .venv
# activate: .venv\Scripts\activate  (Windows) | source .venv/bin/activate (macOS/Linux)
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000
