import sqlite3
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from http import cookies
import urllib.parse
import uuid
import os

DATABASE = 'database.db'
SETTINGS_FILE = 'settings.json'

sessions = {}


def init_db():
    new_db = not os.path.exists(DATABASE)
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    if new_db:
        c.execute('''CREATE TABLE users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_manager INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            manager_id INTEGER,
            item TEXT,
            status TEXT DEFAULT 'pending',
            approved_by INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
    # ensure admin exists
    c.execute('INSERT OR IGNORE INTO users(username, password, is_manager) '
              "VALUES('admin','admin',1)")
    conn.commit()
    conn.close()

    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'w') as f:
            json.dump({'items': ['Laptop', 'Telefoon', 'Software']}, f)


class SimpleHandler(BaseHTTPRequestHandler):
    def parse_cookies(self):
        if 'Cookie' in self.headers:
            c = cookies.SimpleCookie(self.headers['Cookie'])
            if 'session_id' in c:
                return c['session_id'].value
        return None

    def get_current_user(self):
        session_id = self.parse_cookies()
        if session_id and session_id in sessions:
            user_id = sessions[session_id]
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            c.execute('SELECT id, username, is_manager FROM users WHERE id=?', (user_id,))
            row = c.fetchone()
            conn.close()
            if row:
                return {'id': row[0], 'username': row[1], 'is_manager': bool(row[2])}
        return None

    def send_html(self, html, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def redirect(self, path):
        self.send_response(302)
        self.send_header('Location', path)
        self.end_headers()

    def do_GET(self):
        user = self.get_current_user()
        if self.path == '/':
            if user:
                self.redirect('/dashboard')
            else:
                self.redirect('/login')
        elif self.path == '/login':
            self.send_html('<h1>Login</h1>'
                           '<form method="POST" action="/login">'
                           'Username: <input name="username"><br>'
                           'Password: <input name="password" type="password"><br>'
                           '<button type="submit">Login</button>'
                           '</form>'
                           '<a href="/register">Register</a>')
        elif self.path == '/register':
            self.send_html('<h1>Register</h1>'
                           '<form method="POST" action="/register">'
                           'Username: <input name="username"><br>'
                           'Password: <input name="password" type="password"><br>'
                           '<button type="submit">Register</button>'
                           '</form>')
        elif self.path == '/dashboard':
            if not user:
                self.redirect('/login')
                return
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            c.execute('SELECT item, status FROM requests WHERE user_id=?', (user['id'],))
            rows = c.fetchall()
            conn.close()
            items_html = ''.join(f'<li>{r[0]} - {r[1]}</li>' for r in rows)
            extra = ''
            if user['is_manager']:
                extra = '<br><a href="/approve">Aanvragen goedkeuren</a>'
            self.send_html(
                f"<h1>Dashboard</h1>"
                f"<p>Welkom, {user['username']}</p>"
                f"<ul>{items_html}</ul>"
                "<a href=\"/request\">Nieuwe aanvraag</a>" + extra +
                "<br><a href=\"/logout\">Logout</a>"
            )
        elif self.path == '/request':
            if not user:
                self.redirect('/login')
                return
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            c.execute('SELECT id, username FROM users WHERE is_manager=1')
            managers = c.fetchall()
            conn.close()
            options = ''.join(f'<option value="{item}">{item}</option>' for item in data['items'])
            manager_opts = ''.join(f'<option value="{m[0]}">{m[1]}</option>' for m in managers)
            self.send_html('<h1>Nieuwe aanvraag</h1>'
                           '<form method="POST" action="/request">'
                           f'<select name="item">{options}</select><br>'
                           f'Manager: <select name="manager">{manager_opts}</select><br>'
                           '<button type="submit">Aanvragen</button>'
                           '</form>'
                           '<a href="/dashboard">Terug</a>')
        elif self.path == '/logout':
            session_id = self.parse_cookies()
            if session_id and session_id in sessions:
                del sessions[session_id]
            self.send_response(302)
            self.send_header('Location', '/login')
            c = cookies.SimpleCookie()
            c['session_id'] = ''
            c['session_id']['expires'] = 'Thu, 01 Jan 1970 00:00:00 GMT'
            self.send_header('Set-Cookie', c.output(header='', sep=''))
            self.end_headers()
        elif self.path.startswith('/approve'):
            if not user or not user['is_manager']:
                self.send_html('Unauthorized', 403)
                return
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            req_id = params.get('id', [None])[0]
            action = params.get('action', [None])[0]
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            if req_id and action in ('approve', 'deny'):
                status = 'approved' if action == 'approve' else 'denied'
                c.execute('UPDATE requests SET status=?, approved_by=? WHERE id=?',
                          (status, user['id'], req_id))
                conn.commit()
                conn.close()
                self.redirect('/approve')
            else:
                c.execute('''SELECT requests.id, users.username, requests.item FROM requests
                             JOIN users ON requests.user_id = users.id
                             WHERE requests.manager_id=? AND requests.status='pending' ''',
                          (user['id'],))
                rows = c.fetchall()
                conn.close()
                items_html = ''.join(
                    f'<li>{r[1]} vraagt {r[2]} '
                    f'<a href="/approve?id={r[0]}&action=approve">Goedkeuren</a> '
                    f'<a href="/approve?id={r[0]}&action=deny">Afkeuren</a>'
                    f'</li>' for r in rows)
                self.send_html(f'<h1>Goedkeuren aanvragen</h1>'
                               f'<ul>{items_html}</ul>'
                               '<a href="/dashboard">Terug</a>')
        elif self.path.startswith('/set_manager'):
            if not user or not user['is_manager']:
                self.send_html('Unauthorized', 403)
                return
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            username = params.get('user', [''])[0]
            if username:
                conn = sqlite3.connect(DATABASE)
                c = conn.cursor()
                c.execute('UPDATE users SET is_manager=1 WHERE username=?', (username,))
                conn.commit()
                conn.close()
            self.redirect('/dashboard')
        else:
            self.send_html('Not Found', 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length).decode()
        params = urllib.parse.parse_qs(data)
        if self.path == '/login':
            username = params.get('username', [''])[0]
            password = params.get('password', [''])[0]
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            c.execute('SELECT id FROM users WHERE username=? AND password=?', (username, password))
            row = c.fetchone()
            conn.close()
            if row:
                session_id = str(uuid.uuid4())
                sessions[session_id] = row[0]
                self.send_response(302)
                self.send_header('Location', '/dashboard')
                c = cookies.SimpleCookie()
                c['session_id'] = session_id
                self.send_header('Set-Cookie', c.output(header='', sep=''))
                self.end_headers()
            else:
                self.send_html('Login mislukt')
        elif self.path == '/register':
            username = params.get('username', [''])[0]
            password = params.get('password', [''])[0]
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            try:
                c.execute('INSERT INTO users(username, password) VALUES(?, ?)', (username, password))
                conn.commit()
                conn.close()
                self.redirect('/login')
            except sqlite3.IntegrityError:
                conn.close()
                self.send_html('Gebruikersnaam bestaat al')
        elif self.path == '/request':
            user = self.get_current_user()
            if not user:
                self.redirect('/login')
                return
            item = params.get('item', [''])[0]
            manager_id = params.get('manager', [''])[0]
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            c.execute('INSERT INTO requests(user_id, manager_id, item) VALUES(?, ?, ?)',
                      (user['id'], manager_id, item))
            conn.commit()
            conn.close()
            self.redirect('/dashboard')
        else:
            self.send_html('Not Found', 404)


def run(server_class=HTTPServer, handler_class=SimpleHandler):
    init_db()
    server_address = ('', 8000)
    httpd = server_class(server_address, handler_class)
    print('Running on http://localhost:8000')
    httpd.serve_forever()


if __name__ == '__main__':
    run()
