"""TikTok Coach - analyse tes videos avec Claude, sans jamais les modifier. Windows."""
import base64, hashlib, json, os, secrets, threading, webbrowser
import urllib.request, urllib.parse, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2

CFG = os.path.join(os.environ.get("APPDATA", "."), "TikTokCoach.json")
REDIRECT = "http://localhost:8765/callback"
SYSTEM = ("Tu es un coach TikTok. La video ne doit JAMAIS etre modifiee: tu donnes uniquement des conseils "
          "concrets et priorises (accroche des 3 premieres secondes, rythme, texte a l'ecran, legende, hashtags, "
          "son, heure de publication, appel a l'action). Tu ne vois que quelques images, pas le son: dis-le si "
          "c'est une limite. Ne promets jamais la viralite. Reponds en francais.")


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(d):
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(d, f)


def http(url, body=None, headers=None):
    req = urllib.request.Request(url, body, headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def tiktok_login(ck, cs):
    """Connexion OAuth officielle (Login Kit) avec PKCE. Ouvre le navigateur."""
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok attend du SHA256 en hexadecimal
    state = secrets.token_urlsafe(16)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": ck, "scope": "user.info.basic,video.list", "response_type": "code",
        "redirect_uri": REDIRECT, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})

    class H(BaseHTTPRequestHandler):
        code = st = None

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            H.code, H.st = q.get("code", [None])[0], q.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Connecté ! Tu peux fermer cet onglet.".encode("utf-8"))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("localhost", 8765), H)
    srv.timeout = 180
    webbrowser.open(url)
    srv.handle_request()
    srv.server_close()
    if not H.code or H.st != state:
        raise RuntimeError("Connexion TikTok annulée ou expirée.")
    data = urllib.parse.urlencode({
        "client_key": ck, "client_secret": cs, "code": H.code, "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code_verifier": verifier}).encode()
    tok = http("https://open.tiktokapis.com/v2/oauth/token/", data,
               {"Content-Type": "application/x-www-form-urlencoded"})
    if "access_token" not in tok:
        raise RuntimeError(str(tok)[:300])
    return tok["access_token"]


def tiktok_videos(token):
    fields = "id,title,view_count,like_count,comment_count,share_count,duration"
    r = http("https://open.tiktokapis.com/v2/video/list/?fields=" + fields,
             json.dumps({"max_count": 10}).encode(),
             {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    return r.get("data", {}).get("videos", [])


def extract_frames(path, n=6):
    cap = cv2.VideoCapture(path)
    total, fps = int(cap.get(7)), cap.get(5) or 30
    w, h = int(cap.get(3)), int(cap.get(4))
    frames = []
    for i in range(n):
        cap.set(1, int(total * (i + 0.5) / n))
        ok, fr = cap.read()
        if not ok:
            continue
        s = 512 / max(fr.shape[:2])
        if s < 1:
            fr = cv2.resize(fr, None, fx=s, fy=s)
        ok, buf = cv2.imencode(".jpg", fr)
        frames.append(base64.b64encode(buf).decode())
    cap.release()
    if not frames:
        raise RuntimeError("Impossible de lire la vidéo.")
    return frames, total / fps, w, h


def ask_claude(key, model, content):
    body = json.dumps({"model": model, "max_tokens": 1500, "system": SYSTEM,
                       "messages": [{"role": "user", "content": content}]}).encode()
    r = http("https://api.anthropic.com/v1/messages", body,
             {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    return "".join(b.get("text", "") for b in r["content"] if b["type"] == "text")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TikTok Coach")
        self.geometry("680x640")
        self.cfg, self.stats, self.token = load_cfg(), [], None
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=8, pady=8)
        a, b = ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(a, text="Connexions")
        tabs.add(b, text="Analyser une vidéo")
        self.build_conn(a)
        self.build_an(b)

    def build_conn(self, f):
        self.ent = {}
        fields = [("Clé API Claude", "key", True), ("Modèle Claude", "model", False),
                  ("TikTok Client Key", "ck", False), ("TikTok Client Secret", "cs", True)]
        for r, (lab, k, secret) in enumerate(fields):
            ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w", padx=10, pady=6)
            e = ttk.Entry(f, width=48, show="•" if secret else "")
            e.insert(0, self.cfg.get(k, "claude-sonnet-5-5" if k == "model" else ""))
            e.grid(row=r, column=1, padx=6)
            self.ent[k] = e
        ttk.Button(f, text="Enregistrer", command=self.save).grid(row=4, column=1, sticky="w", padx=6, pady=8)
        ttk.Button(f, text="Se connecter à TikTok", command=self.connect).grid(row=5, column=1, sticky="w", padx=6)
        self.status = ttk.Label(f, text="TikTok : non connecté", foreground="gray")
        self.status.grid(row=6, column=1, sticky="w", padx=6, pady=10)

    def save(self):
        self.cfg.update({k: e.get().strip() for k, e in self.ent.items()})
        save_cfg(self.cfg)

    def connect(self):
        self.save()
        self.status.config(text="Connexion en cours… regarde ton navigateur")

        def work():
            try:
                self.token = tiktok_login(self.cfg["ck"], self.cfg["cs"])
                self.stats = tiktok_videos(self.token)
                msg = f"TikTok : connecté ({len(self.stats)} vidéos lues)"
            except urllib.error.HTTPError as e:
                msg = "Erreur TikTok : " + e.read().decode(errors="ignore")[:200]
            except Exception as e:
                msg = "Erreur : " + str(e)[:200]
            self.after(0, lambda: self.status.config(text=msg))
        threading.Thread(target=work, daemon=True).start()

    def build_an(self, f):
        self.path = tk.StringVar()
        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Entry(row, textvariable=self.path).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choisir la vidéo…", command=lambda: self.path.set(
            filedialog.askopenfilename(filetypes=[("Vidéos", "*.mp4 *.mov *.avi *.mkv")]))).pack(side="left", padx=4)
        ttk.Label(f, text="Ta niche :").pack(anchor="w", padx=8)
        self.niche = ttk.Entry(f)
        self.niche.pack(fill="x", padx=8)
        ttk.Label(f, text="Légende prévue :").pack(anchor="w", padx=8)
        self.caption = ttk.Entry(f)
        self.caption.pack(fill="x", padx=8)
        ttk.Label(f, text="Tendances / vidéos qui marchent dans ta niche (optionnel, colle-les ici) :").pack(anchor="w", padx=8)
        self.trends = tk.Text(f, height=4, wrap="word")
        self.trends.pack(fill="x", padx=8)
        ttk.Button(f, text="Analyser (la vidéo n'est jamais modifiée)", command=self.analyse).pack(pady=8)
        self.out = tk.Text(f, wrap="word", state="disabled")
        self.out.pack(fill="both", expand=True, padx=8, pady=4)

    def show(self, text):
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("end", text)
        self.out.config(state="disabled")

    def analyse(self):
        self.save()
        if not self.cfg.get("key") or not os.path.isfile(self.path.get()):
            messagebox.showwarning("TikTok Coach", "Renseigne la clé API Claude et choisis une vidéo.")
            return
        self.show("Analyse en cours…")
        path, niche, cap = self.path.get(), self.niche.get(), self.caption.get()
        trends = self.trends.get("1.0", "end").strip()
        stats = list(self.stats)

        def work():
            try:
                frames, dur, w, h = extract_frames(path)
                txt = f"Vidéo de {dur:.0f}s, {w}x{h}. {len(frames)} images réparties sur la durée.\n"
                txt += f"Niche : {niche}\nLégende prévue : {cap}\n"
                if trends:
                    txt += f"Tendances de la niche :\n{trends}\n"
                if stats:
                    txt += "Performances de mes dernières vidéos :\n" + "\n".join(
                        f"- {v.get('title', '')[:60]} | {v.get('view_count')} vues, {v.get('like_count')} likes, "
                        f"{v.get('comment_count')} commentaires, {v.get('share_count')} partages" for v in stats)
                content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}
                           for b in frames] + [{"type": "text", "text": txt + "\nDonne-moi tes conseils."}]
                res = ask_claude(self.cfg["key"], self.cfg.get("model", "claude-sonnet-5-5"), content)
            except urllib.error.HTTPError as e:
                res = f"Erreur {e.code} : vérifie ta clé API et le modèle."
            except Exception as e:
                res = "Erreur : " + str(e)
            self.after(0, self.show, res)
        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
