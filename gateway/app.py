#!/usr/bin/env python3
"""Passerelle d'accès Qwen : clés par personne/projet, liens éphémères, forward_auth pour Caddy.

Écoute uniquement sur 127.0.0.1 ; Caddy publie /v1/* (via /auth), /claim/* et /admin* (protégé par basic_auth).
Seules les empreintes SHA-256 des clés sont stockées ; la clé en clair n'existe que dans un lien non encore consommé.
Bibliothèque standard uniquement : rien à installer, rien à mettre à jour sur le serveur.
"""
import hashlib, hmac, html, json, os, secrets, sqlite3, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.environ.get("GW_DB", "/opt/llm/gateway/data/gateway.db")
PUBLIC_URL = os.environ.get("GW_PUBLIC_URL", "https://llm.example.com").rstrip("/")
LISTEN_PORT = int(os.environ.get("GW_PORT", "8090"))
CSRF_SECRET = secrets.token_bytes(32)  # renouvelé à chaque démarrage : les formulaires ouverts avant un restart expirent
MODELS = ["qwen-27b", "qwen-flash"]

_lock = threading.Lock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_db.row_factory = sqlite3.Row
_db.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS keys (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, note TEXT DEFAULT '',
  key_hash TEXT UNIQUE NOT NULL, prefix TEXT NOT NULL,
  created_at INTEGER NOT NULL, expires_at INTEGER, revoked_at INTEGER,
  last_used_at INTEGER, requests INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS claims (
  token_hash TEXT PRIMARY KEY, key_id INTEGER NOT NULL REFERENCES keys(id),
  secret TEXT, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER, used_ip TEXT);
""")


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def q(sql, *args):
    with _lock:
        return _db.execute(sql, args).fetchall()


def now():
    return int(time.time())


def fmt_ts(ts):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts)) if ts else "—"


def new_key():
    k = "sk-gw-" + secrets.token_urlsafe(32)
    return k, sha(k), k[:12]


def purge_claims():
    # efface les secrets des liens expirés (la clé reste valide, seul le lien meurt)
    q("UPDATE claims SET secret=NULL WHERE secret IS NOT NULL AND expires_at < ?", now())


def create_claim(key_id, secret, hours):
    token = secrets.token_urlsafe(32)
    q("UPDATE claims SET secret=NULL, expires_at=MIN(expires_at, ?) WHERE key_id=? AND used_at IS NULL", now(), key_id)
    q("INSERT INTO claims(token_hash,key_id,secret,created_at,expires_at) VALUES(?,?,?,?,?)",
      sha(token), key_id, secret, now(), now() + int(hours * 3600))
    return f"{PUBLIC_URL}/claim/{token}"


def csrf_token():
    ts = str(now())
    return ts + "." + hmac.new(CSRF_SECRET, ts.encode(), "sha256").hexdigest()


def csrf_ok(tok):
    try:
        ts, sig = tok.split(".", 1)
        good = hmac.new(CSRF_SECRET, ts.encode(), "sha256").hexdigest()
        return hmac.compare_digest(sig, good) and now() - int(ts) < 4 * 3600
    except Exception:
        return False


E = html.escape
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--mut:#6b7385;--bd:#e3e6ec;--acc:#3a5bd9;--ok:#1f8a4c;--bad:#c23b3b;--code:#f0f2f6}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b1f27;--fg:#e6e9ef;--mut:#9aa3b5;--bd:#2c323d;--acc:#7c95ff;--ok:#4cc27e;--bad:#ef6b6b;--code:#232833}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:18px;margin:16px 0}
label{display:block;font-size:13px;color:var(--mut);margin-bottom:4px}input,select{width:100%;padding:8px 10px;border:1px solid var(--bd);border-radius:7px;background:var(--bg);color:var(--fg);font:inherit}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;align-items:end}
button{padding:8px 14px;border:0;border-radius:7px;background:var(--acc);color:#fff;font:inherit;cursor:pointer}button.sec{background:transparent;color:var(--acc);border:1px solid var(--bd)}button.bad{background:transparent;color:var(--bad);border:1px solid var(--bd)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--bd);vertical-align:middle}th{color:var(--mut);font-weight:500;font-size:12px;text-transform:uppercase}
.tw{overflow-x:auto}code,pre{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);border-radius:6px}code{padding:2px 6px}pre{padding:12px;overflow-x:auto;white-space:pre-wrap;word-break:break-all}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--bd)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.secret{display:flex;gap:8px;align-items:center}.secret code{flex:1;padding:10px;word-break:break-all}form.inl{display:inline}
.flash{border-color:var(--acc)}
"""
COPY_JS = """<script>document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>{navigator.clipboard.writeText(document.getElementById(b.dataset.copy).innerText);b.innerText='Copié ✓';setTimeout(()=>b.innerText='Copier',1500)})</script>"""


def page(title, body):
    return (f"<!doctype html><html lang=fr><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content='noindex,nofollow'><title>{E(title)}</title><style>{CSS}</style></head>"
            f"<body><main>{body}</main>{COPY_JS}</body></html>")


def secret_box(id_, value):
    return f"<div class=secret><code id={id_}>{E(value)}</code><button class=sec type=button data-copy={id_}>Copier</button></div>"


def credentials_block(key):
    ex = (f'curl {PUBLIC_URL}/v1/chat/completions \\\n  -H "Authorization: Bearer {key}" \\\n  -H "Content-Type: application/json" \\\n'
          f'  -d \'{{"model":"qwen-27b","messages":[{{"role":"user","content":"Bonjour"}}]}}\'')
    py = (f'from openai import OpenAI\nclient = OpenAI(base_url="{PUBLIC_URL}/v1", api_key="{key}")\n'
          f'r = client.chat.completions.create(model="qwen-27b", messages=[{{"role": "user", "content": "Bonjour"}}])\nprint(r.choices[0].message.content)')
    return (f"<label>URL de base (compatible OpenAI)</label>{secret_box('u', PUBLIC_URL + '/v1')}<br>"
            f"<label>Clé d'API</label>{secret_box('k', key)}<br>"
            f"<label>Modèles</label><p><code>qwen-27b</code> rapide, idéal pour le code et les agents · "
            f"<code>qwen-flash</code> plus lent, pour la réflexion approfondie (premier appel plus long : chargement)</p>"
            f"<label>Exemple curl</label><pre>{E(ex)}</pre><label>Exemple Python (SDK openai)</label><pre>{E(py)}</pre>"
            f"<p class=mut>Effort de raisonnement par requête : <code>\"chat_template_kwargs\": {{\"reasoning_effort\": \"low\"}}</code> (low, medium, xhigh).</p>")


class H(BaseHTTPRequestHandler):
    server_version = "gw"
    sys_version = ""

    def log_message(self, fmt, *a):
        pass  # pas de journalisation des URL (elles contiennent les jetons de lien) ; Caddy journalise déjà

    def client_ip(self):
        return self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def form(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > 65536:
            return {}
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}

    # ---------- routes ----------
    def do_GET(self):
        p = urllib.parse.urlsplit(self.path).path
        if p == "/auth":
            return self.auth()
        if p.startswith("/claim/"):
            return self.claim_get(p[7:])
        if p in ("/admin", "/admin/"):
            return self.admin_page()
        self.send(404, "Not found", "text/plain")

    def do_POST(self):
        p = urllib.parse.urlsplit(self.path).path
        if p.startswith("/claim/"):
            return self.claim_post(p[7:])
        if p.startswith("/admin/"):
            f = self.form()
            # anti-CSRF : jeton signé + requête same-origin
            site = self.headers.get("Sec-Fetch-Site", "same-origin")
            if not csrf_ok(f.get("csrf", "")) or site not in ("same-origin", "none"):
                return self.send(403, page("Refusé", "<h1>Formulaire expiré</h1><p><a href=/admin>Recharger le panneau</a></p>"))
            return self.admin_action(p[7:], f)
        self.send(404, "Not found", "text/plain")

    # ---------- forward_auth (Caddy) ----------
    def auth(self):
        a = self.headers.get("Authorization", "")
        key = a[7:].strip() if a.lower().startswith("bearer ") else self.headers.get("X-Api-Key", "").strip()
        row = None
        if key:
            r = q("SELECT id, expires_at, revoked_at FROM keys WHERE key_hash=?", sha(key))
            row = r[0] if r else None
        if not row or row["revoked_at"] or (row["expires_at"] and row["expires_at"] < now()):
            return self.send(401, json.dumps({"error": {"message": "Invalid or revoked API key", "type": "invalid_request_error", "code": "invalid_api_key"}}),
                             "application/json", {"WWW-Authenticate": "Bearer"})
        q("UPDATE keys SET last_used_at=?, requests=requests+1 WHERE id=?", now(), row["id"])
        self.send(200, "", "text/plain", {"X-Gateway-Key-Id": str(row["id"])})

    # ---------- lien éphémère ----------
    def claim_row(self, token):
        purge_claims()
        r = q("SELECT c.*, k.name, k.revoked_at FROM claims c JOIN keys k ON k.id=c.key_id WHERE c.token_hash=?", sha(token))
        r = r[0] if r else None
        if not r or r["used_at"] or not r["secret"] or r["expires_at"] < now() or r["revoked_at"]:
            return None
        return r

    def claim_get(self, token):
        r = self.claim_row(token)
        if not r:
            return self.send(410, page("Lien expiré", "<h1>Lien invalide ou déjà utilisé</h1><p class=mut>Ce lien d'accès a expiré ou a déjà été ouvert. Demandez-en un nouveau à l'administrateur.</p>"))
        host = urllib.parse.urlsplit(PUBLIC_URL).hostname or ""
        body = (f"<h1>Accès à l'IA Qwen — {E(host)}</h1><p class=mut>Bonjour <b>{E(r['name'])}</b>. Ce lien est à usage unique et expire le {fmt_ts(r['expires_at'])}.</p>"
                f"<div class=card><p>En cliquant ci-dessous, vos identifiants (URL + clé d'API) s'afficheront <b>une seule fois</b>. "
                f"Copiez-les immédiatement dans un endroit sûr (gestionnaire de mots de passe, fichier <code>.env</code>).</p>"
                f"<form method=post><button>Afficher mes identifiants</button></form></div>")
        self.send(200, page("Votre accès", body))

    def claim_post(self, token):
        with _lock:
            r = _db.execute("SELECT c.*, k.name, k.revoked_at FROM claims c JOIN keys k ON k.id=c.key_id WHERE c.token_hash=?", (sha(token),)).fetchone()
            ok = r and not r["used_at"] and r["secret"] and r["expires_at"] >= now() and not r["revoked_at"]
            if ok:  # consommation atomique : le secret est effacé avant l'affichage
                _db.execute("UPDATE claims SET used_at=?, used_ip=?, secret=NULL WHERE token_hash=?", (now(), self.client_ip(), sha(token)))
        if not ok:
            return self.send(410, page("Lien expiré", "<h1>Lien invalide ou déjà utilisé</h1><p class=mut>Demandez un nouveau lien à l'administrateur.</p>"))
        body = (f"<h1>Vos identifiants</h1><p class=bad><b>Copiez-les maintenant : cette page ne pourra plus être réaffichée.</b></p>"
                f"<div class=card>{credentials_block(r['secret'])}</div>")
        self.send(200, page("Vos identifiants", body))

    # ---------- admin ----------
    def admin_page(self, flash=""):
        purge_claims()
        rows = q("""SELECT k.*, (SELECT MAX(c.expires_at) FROM claims c WHERE c.key_id=k.id AND c.used_at IS NULL AND c.secret IS NOT NULL) AS pending,
                    (SELECT MAX(c.used_at) FROM claims c WHERE c.key_id=k.id) AS claimed
                    FROM keys k ORDER BY k.revoked_at IS NOT NULL, k.created_at DESC""")
        tok = E(csrf_token())
        trs = []
        for k in rows:
            if k["revoked_at"]:
                st = f"<span class='pill bad'>révoquée {fmt_ts(k['revoked_at'])}</span>"
            elif k["expires_at"] and k["expires_at"] < now():
                st = "<span class='pill bad'>expirée</span>"
            elif k["pending"]:
                st = f"<span class=pill>lien en attente → {fmt_ts(k['pending'])}</span>"
            else:
                st = "<span class='pill ok'>active</span>"
            acts = ""
            if k["revoked_at"]:
                acts = (f"<form class=inl method=post action=/admin/delete onsubmit=\"return confirm('Supprimer définitivement l’accès révoqué {E(k['name'])} ?')\">"
                        f"<input type=hidden name=csrf value='{tok}'><input type=hidden name=id value={k['id']}><button class=bad>Supprimer</button></form>")
            else:
                acts = (f"<form class=inl method=post action=/admin/rotate onsubmit=\"return confirm('Générer une NOUVELLE clé pour {E(k['name'])} ? L’ancienne cessera de fonctionner.')\">"
                        f"<input type=hidden name=csrf value='{tok}'><input type=hidden name=id value={k['id']}><button class=sec>Nouvelle clé + lien</button></form> "
                        f"<form class=inl method=post action=/admin/revoke onsubmit=\"return confirm('Révoquer l’accès de {E(k['name'])} ?')\">"
                        f"<input type=hidden name=csrf value='{tok}'><input type=hidden name=id value={k['id']}><button class=bad>Révoquer</button></form>")
            trs.append(f"<tr><td><b>{E(k['name'])}</b><br><span class=mut>{E(k['note'] or '')}</span></td><td>{'Projet' if k['kind']=='project' else 'Personne'}</td>"
                       f"<td><code>{E(k['prefix'])}…</code></td><td>{st}</td><td>{fmt_ts(k['created_at'])}<br><span class=mut>exp. {fmt_ts(k['expires_at']) if k['expires_at'] else 'jamais'}</span></td>"
                       f"<td>{fmt_ts(k['last_used_at'])}<br><span class=mut>{k['requests']} req.</span></td><td>{acts}</td></tr>")
        n_revoked = sum(1 for k in rows if k["revoked_at"])
        purge_btn = (f"<form method=post action=/admin/purge-revoked onsubmit=\"return confirm('Supprimer définitivement les {n_revoked} accès révoqués ?')\" style='margin-top:12px'>"
                     f"<input type=hidden name=csrf value='{tok}'><button class=bad>Supprimer tous les révoqués ({n_revoked})</button></form>") if n_revoked else ""
        table = ("<div class=tw><table><tr><th>Nom</th><th>Type</th><th>Clé</th><th>Statut</th><th>Créée</th><th>Dernier usage</th><th></th></tr>"
                 + "".join(trs) + "</table></div>") if trs else "<p class=mut>Aucun accès pour l'instant.</p>"
        body = (f"<h1>Passerelle IA — administration</h1><p class=mut>{E(PUBLIC_URL)} · modèles : {', '.join(MODELS)}</p>{flash}"
                f"<div class=card><h2>Créer un accès</h2><form method=post action=/admin/create><input type=hidden name=csrf value='{tok}'><div class=grid>"
                f"<div><label>Nom (personne ou projet)</label><input name=name required maxlength=80 placeholder='ex. Marie ou projet-crm'></div>"
                f"<div><label>Type</label><select name=kind><option value=person>Personne → lien éphémère</option><option value=project>Projet → clé affichée ici</option></select></div>"
                f"<div><label>Validité de la clé</label><select name=days><option value=0>Sans expiration</option><option value=7>7 jours</option><option value=30>30 jours</option><option value=90 selected>90 jours</option><option value=365>1 an</option></select></div>"
                f"<div><label>Validité du lien</label><select name=hours><option value=1>1 heure</option><option value=24 selected>24 heures</option><option value=72>3 jours</option><option value=168>7 jours</option></select></div>"
                f"<div><label>Note (optionnel)</label><input name=note maxlength=200></div><div><button>Créer</button></div></div></form></div>"
                f"<div class=card><h2>Accès</h2>{table}{purge_btn}</div>")
        self.send(200, page("Admin IA", body))

    def admin_action(self, action, f):
        if action == "create":
            name = (f.get("name") or "").strip()[:80]
            if not name:
                return self.redirect("/admin")
            kind = "project" if f.get("kind") == "project" else "person"
            days = max(0, min(int(f.get("days", "0") or 0), 3650))
            hours = max(1, min(int(f.get("hours", "24") or 24), 168))
            key, kh, prefix = new_key()
            with _lock:
                cur = _db.execute("INSERT INTO keys(name,kind,note,key_hash,prefix,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                                  (name, kind, (f.get("note") or "")[:200], kh, prefix, now(), now() + days * 86400 if days else None))
                kid = cur.lastrowid
            return self.show_created(kid, name, kind, key, hours)
        if action == "purge-revoked":
            with _lock:
                _db.execute("DELETE FROM claims WHERE key_id IN (SELECT id FROM keys WHERE revoked_at IS NOT NULL)")
                _db.execute("DELETE FROM keys WHERE revoked_at IS NOT NULL")
            return self.redirect("/admin")
        kid = int(f.get("id", "0") or 0)
        r = q("SELECT * FROM keys WHERE id=?", kid)
        if not r:
            return self.redirect("/admin")
        k = r[0]
        if action == "revoke":
            q("UPDATE keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", now(), kid)
            q("UPDATE claims SET secret=NULL WHERE key_id=?", kid)
            return self.redirect("/admin")
        if action == "delete" and k["revoked_at"]:  # seuls les accès déjà révoqués peuvent être supprimés
            with _lock:
                _db.execute("DELETE FROM claims WHERE key_id=?", (kid,))
                _db.execute("DELETE FROM keys WHERE id=? AND revoked_at IS NOT NULL", (kid,))
            return self.redirect("/admin")
        if action == "rotate" and not k["revoked_at"]:
            key, kh, prefix = new_key()
            q("UPDATE keys SET key_hash=?, prefix=? WHERE id=?", kh, prefix, kid)
            return self.show_created(kid, k["name"], k["kind"], key, 24)
        self.redirect("/admin")

    def show_created(self, kid, name, kind, key, hours):
        link = create_claim(kid, key, hours)
        if kind == "project":
            flash = (f"<div class='card flash'><h2>Accès « {E(name)} » créé</h2><p class=bad><b>Clé affichée une seule fois</b> — copiez-la dans le <code>.env</code> du projet.</p>"
                     f"{credentials_block(key)}<p class=mut>Un lien éphémère a aussi été généré si vous préférez le transmettre (valable {hours} h) :</p>{secret_box('l', link)}</div>")
        else:
            flash = (f"<div class='card flash'><h2>Accès « {E(name)} » créé</h2><p>Envoyez ce lien à la personne. Il est <b>à usage unique</b> et expire dans <b>{hours} h</b> ; "
                     f"la clé n'est pas affichée ici.</p>{secret_box('l', link)}</div>")
        self.admin_page(flash)


if __name__ == "__main__":
    os.umask(0o077)
    srv = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), H)
    srv.daemon_threads = True
    srv.serve_forever()
