"""The chat window.

Tk owns the main thread; the agent runs on a worker. They talk through a queue
that the UI drains on a timer, which is also how approval prompts and window
cloaking stay thread-safe -- no widget is ever touched from the worker.
"""
from __future__ import annotations

import base64
import io
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk

from .agent import Events
from .safety import Aborted, describe

BG = "#11141a"
PANEL = "#171b23"
FG = "#d7dbe3"
DIM = "#79818f"
ACCENT = "#7aa2f7"
GOOD = "#8fbf7f"
WARN = "#e0a862"
BAD = "#e06c75"


class Cloak:
    """Hides the window while the model takes a screenshot.

    The worker thread asks; the UI thread acts on its next poll and reports back.
    Without this the agent spends every screenshot looking at its own chat window.
    """

    def __init__(self):
        self.want_hidden = threading.Event()
        self.settled = threading.Event()
        self.enabled = True

    def __enter__(self):
        if self.enabled:
            self.settled.clear()
            self.want_hidden.set()
            self.settled.wait(timeout=2.0)
        return self

    def __exit__(self, *exc):
        if self.enabled:
            self.settled.clear()
            self.want_hidden.clear()
            self.settled.wait(timeout=2.0)
        return False


class ChatWindow(Events):
    def __init__(self, pilot, kill, title="ScreenPilot", subtitle=""):
        self.pilot = pilot
        self.kill = kill
        self.events: queue.Queue = queue.Queue()
        self.cloak = Cloak()
        self.busy = False
        self._answer: tuple[str, str] | None = None
        self._answered = threading.Event()
        self._thumb_ref = None
        self._alpha_saved = 1.0

        root = self.root = tk.Tk()
        root.title(title)
        root.geometry("980x680")
        root.configure(bg=BG)
        root.minsize(640, 420)

        mono = tkfont.Font(family="Consolas", size=10)
        self.mono = mono

        # -- header -----------------------------------------------------------
        header = tk.Frame(root, bg=PANEL, height=38)
        header.pack(fill="x", side="top")
        tk.Label(header, text=subtitle, bg=PANEL, fg=DIM,
                 font=(mono.cget("family"), 9)).pack(side="left", padx=12)
        self.stop_btn = tk.Button(header, text="Stop", command=self.on_stop,
                                  bg="#3a2226", fg=BAD, relief="flat",
                                  activebackground="#4a2a30", state="disabled", padx=14)
        self.stop_btn.pack(side="right", padx=8, pady=5)
        self.status = tk.Label(header, text="ready", bg=PANEL, fg=DIM,
                               font=(mono.cget("family"), 9))
        self.status.pack(side="right", padx=8)

        # -- body: transcript + last screenshot -------------------------------
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        self.text = tk.Text(left, bg=BG, fg=FG, wrap="word", relief="flat",
                            font=mono, padx=14, pady=12, insertbackground=FG,
                            spacing1=2, spacing3=4)
        # Windows' classic scrollbar ignores most colour options; zeroing the
        # borders is what actually stops it rendering as a bright white bar.
        scroll = tk.Scrollbar(left, command=self.text.yview, bg=PANEL,
                              troughcolor=BG, activebackground="#39414f",
                              highlightthickness=0, borderwidth=0,
                              elementborderwidth=0, relief="flat", width=11)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(state="disabled")

        self.text.tag_configure("you", foreground=ACCENT, font=(mono.cget("family"), 10, "bold"))
        self.text.tag_configure("model", foreground=FG)
        self.text.tag_configure("think", foreground=DIM, font=(mono.cget("family"), 9, "italic"))
        self.text.tag_configure("act", foreground=GOOD, font=(mono.cget("family"), 9))
        self.text.tag_configure("warn", foreground=WARN)
        self.text.tag_configure("bad", foreground=BAD)

        right = tk.Frame(body, bg=PANEL, width=300)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        tk.Label(right, text="what the model sees", bg=PANEL, fg=DIM,
                 font=(mono.cget("family"), 8)).pack(pady=(10, 4))
        self.thumb = tk.Label(right, bg=PANEL, fg=DIM, text="(no screenshot yet)",
                              font=(mono.cget("family"), 8))
        self.thumb.pack(padx=10, pady=4)

        # -- approval bar (hidden until needed) --------------------------------
        self.approve_bar = tk.Frame(root, bg="#2a2118")
        self.approve_label = tk.Label(self.approve_bar, text="", bg="#2a2118", fg=WARN,
                                      font=(mono.cget("family"), 10, "bold"), anchor="w")
        self.approve_label.pack(side="left", padx=12, pady=8, fill="x", expand=True)
        for label, choice, colour in (("Run", "run", GOOD), ("Skip", "skip", WARN),
                                      ("Trust rest", "trust", ACCENT), ("Abort", "abort", BAD)):
            tk.Button(self.approve_bar, text=label, relief="flat", padx=10,
                      bg=PANEL, fg=colour, activebackground="#222833",
                      command=lambda c=choice: self._answer_approval(c)).pack(side="right", padx=4, pady=6)

        # -- input -------------------------------------------------------------
        bottom = tk.Frame(root, bg=PANEL)
        bottom.pack(fill="x", side="bottom")
        self.entry = tk.Text(bottom, height=3, bg="#1d222c", fg=FG, relief="flat",
                             font=mono, padx=10, pady=8, insertbackground=FG, wrap="word")
        self.entry.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=10)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<Shift-Return>", lambda e: None)
        self.send_btn = tk.Button(bottom, text="Send", command=self.submit, relief="flat",
                                  bg="#243044", fg=ACCENT, activebackground="#2c3a54", padx=18)
        self.send_btn.pack(side="right", padx=(0, 10), pady=10)

        self.entry.focus_set()
        self._say("Ask me anything about your screen, or tell me what to do on it.\n", "think")
        self.root.after(60, self._drain)

    # -- Events interface (called from the worker thread) ----------------------

    def thinking(self, text): self.events.put(("think", text))
    def say(self, text): self.events.put(("model", text))
    def action(self, label): self.events.put(("act", label))
    def status_(self, text): self.events.put(("status", text))
    def status(self, text): self.events.put(("status", text))

    def screenshot(self, grab):
        self.events.put(("shot", grab.png_b64))

    def ask(self, name: str, args: dict) -> tuple[str, str]:
        """Blocking approval request, answered by a button on the UI thread."""
        self._answered.clear()
        self.events.put(("approve", describe(name, args)))
        while not self._answered.wait(timeout=0.2):
            if self.kill._flag.is_set():
                return "abort", ""
        choice, reason = self._answer or ("abort", "")
        return choice, reason

    # -- UI thread -------------------------------------------------------------

    def _drain(self):
        # window cloaking, handled here so Tk is only touched on its own thread
        if self.cloak.want_hidden.is_set():
            if self._alpha_saved == 1.0:
                self.root.attributes("-alpha", 0.0)
                self.root.update_idletasks()
                self._alpha_saved = 0.0
                self.cloak.settled.set()
        elif self._alpha_saved == 0.0:
            self.root.attributes("-alpha", 1.0)
            self._alpha_saved = 1.0
            self.cloak.settled.set()

        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.configure(text=payload)
                elif kind == "shot":
                    self._set_thumb(payload)
                elif kind == "approve":
                    self._show_approval(payload)
                elif kind == "think":
                    self._say("  " + payload + "\n", "think")
                elif kind == "act":
                    self._say("  . " + payload + "\n", "act")
                elif kind == "model":
                    self._say(payload + "\n", "model")
                elif kind == "you":
                    self._say("\n> " + payload + "\n", "you")
                elif kind == "warn":
                    self._say(payload + "\n", "warn")
                elif kind == "bad":
                    self._say(payload + "\n", "bad")
                elif kind == "idle":
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(60, self._drain)

    def _say(self, text, tag):
        self.text.configure(state="normal")
        self.text.insert("end", text, tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def _set_thumb(self, png_b64):
        try:
            img = Image.open(io.BytesIO(base64.b64decode(png_b64)))
            img.thumbnail((280, 280))
            self._thumb_ref = ImageTk.PhotoImage(img)
            self.thumb.configure(image=self._thumb_ref, text="")
        except Exception:
            pass

    def _show_approval(self, label):
        self.approve_label.configure(text=label)
        self.approve_bar.pack(fill="x", side="bottom", before=self.entry.master)

    def _answer_approval(self, choice):
        self._answer = (choice, "")
        self.approve_bar.pack_forget()
        self._answered.set()

    def _set_busy(self, busy):
        self.busy = busy
        self.status.configure(text="working" if busy else "ready")
        self.send_btn.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy else "disabled")

    # -- actions ---------------------------------------------------------------

    def _on_return(self, event):
        if event.state & 0x0001:  # shift held -> newline
            return None
        self.submit()
        return "break"

    def submit(self):
        if self.busy:
            return
        task = self.entry.get("1.0", "end").strip()
        if not task:
            return
        self.entry.delete("1.0", "end")
        self.events.put(("you", task))
        self._set_busy(True)
        self.kill.arm()
        threading.Thread(target=self._work, args=(task,), daemon=True).start()

    def _work(self, task):
        try:
            answer = self.pilot.run(task)
            if answer:
                self.events.put(("model", answer))
        except Aborted as exc:
            self.events.put(("bad", "! aborted: " + str(exc)))
        except Exception as exc:
            self.events.put(("bad", "! " + type(exc).__name__ + ": " + str(exc)))
        finally:
            self.kill.disarm()
            self.cloak.want_hidden.clear()
            self.events.put(("idle", None))

    def on_stop(self):
        self.kill._flag.set()
        self.events.put(("warn", "! stopping after the current step"))

    def run(self):
        self.root.mainloop()
