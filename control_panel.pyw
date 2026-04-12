import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import BOTH, LEFT, RIGHT, StringVar, Text, Tk, Toplevel, W, E, N, S, messagebox
from tkinter import ttk

import psutil


APP_DIR = Path(__file__).resolve().parent
APP_SCRIPT = APP_DIR / "app.py"
ENV_FILE = APP_DIR / ".env"
STDOUT_LOG = APP_DIR / "flask.log"
STDERR_LOG = APP_DIR / "flask.err.log"
HOST = "127.0.0.1"
PORT = 5000
APP_URL = f"http://{HOST}:{PORT}"


def load_env_file() -> dict[str, str]:
    env = os.environ.copy()
    if not ENV_FILE.exists():
        return env

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def is_port_listening(host: str = HOST, port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def check_health() -> bool:
    try:
        req = urllib.request.Request(f"{APP_URL}/health", headers={"User-Agent": "StationControlPanel/1.0"})
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def find_listener_pid() -> int | None:
    for conn in psutil.net_connections(kind="inet"):
        if not conn.laddr:
            continue
        if conn.status != psutil.CONN_LISTEN:
            continue
        if conn.laddr.port != PORT:
            continue
        if conn.pid:
            return int(conn.pid)
    return None


def stop_running_app() -> tuple[bool, str]:
    pid = find_listener_pid()
    if not pid:
        return True, "当前没有监听 5000 端口的服务。"

    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        cwd=APP_DIR,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode == 0:
        return True, f"已停止服务，PID {pid}。"
    stderr = (completed.stderr or completed.stdout or "").strip()
    return False, stderr or f"停止失败，PID {pid}。"


def start_app(hidden: bool) -> tuple[bool, str]:
    if is_port_listening():
        pid = find_listener_pid()
        return False, f"服务已在运行，PID {pid or '-'}。"

    if not APP_SCRIPT.exists():
        return False, "找不到 app.py。"

    env = load_env_file()
    if not env.get("DATABASE_URL") and not (APP_DIR / "station_monitor.db").exists():
        return False, "未找到数据库，请先初始化数据库。"

    python_exe = sys.executable
    if hidden:
        pythonw_candidate = Path(python_exe).with_name("pythonw.exe")
        python_exe = str(pythonw_candidate if pythonw_candidate.exists() else python_exe)

    creationflags = 0
    startupinfo = None
    if os.name == "nt" and hidden:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    stdout_handle = open(STDOUT_LOG, "a", encoding="utf-8")
    stderr_handle = open(STDERR_LOG, "a", encoding="utf-8")
    try:
        subprocess.Popen(
            [python_exe, str(APP_SCRIPT)],
            cwd=APP_DIR,
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    for _ in range(20):
        time.sleep(0.4)
        if is_port_listening():
            mode = "后台隐藏" if hidden else "前台窗口"
            return True, f"已用{mode}方式启动。"
    return False, "启动命令已发出，但 5000 端口还未就绪。"


def restart_app(hidden: bool) -> tuple[bool, str]:
    stop_running_app()
    time.sleep(1)
    return start_app(hidden=hidden)


class LogViewer(Toplevel):
    def __init__(self, master: Tk, title: str, log_path: Path):
        super().__init__(master)
        self.title(title)
        self.geometry("860x560")
        self.configure(bg="#f3f6fb")
        self.log_path = log_path

        container = ttk.Frame(self, style="Panel.TFrame", padding=16)
        container.pack(fill=BOTH, expand=True)

        header = ttk.Frame(container, style="Panel.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text=log_path.name, style="SectionTitle.TLabel").pack(side=LEFT)
        ttk.Button(header, text="刷新", style="Secondary.TButton", command=self.refresh).pack(side=RIGHT)

        self.text = Text(
            container,
            wrap="word",
            bg="#0f172a",
            fg="#dbeafe",
            insertbackground="#dbeafe",
            relief="flat",
            font=("Consolas", 10),
            padx=14,
            pady=14,
        )
        self.text.pack(fill=BOTH, expand=True, pady=(12, 0))
        self.refresh()

    def refresh(self):
        if self.log_path.exists():
            content = self.log_path.read_text(encoding="utf-8", errors="replace")
        else:
            content = "日志文件还没有生成。"
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content or "日志为空。")
        self.text.see("end")


class StationMonitorControlPanel:
    def __init__(self):
        self.root = Tk()
        self.root.title("站点监控平台控制面板")
        self.root.geometry("960x640")
        self.root.minsize(900, 600)
        self.root.configure(bg="#eef3fb")

        self.status_var = StringVar(value="正在检查服务状态...")
        self.pid_var = StringVar(value="-")
        self.mode_var = StringVar(value="-")
        self.url_var = StringVar(value=APP_URL)
        self.health_var = StringVar(value="待检测")
        self.log_var = StringVar(value="欢迎使用新的图形控制面板。")

        self._build_styles()
        self._build_layout()
        self.refresh_status()
        self.root.after(5000, self._auto_refresh)

    def _build_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("App.TFrame", background="#eef3fb")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Hero.TFrame", background="#0f172a")

        style.configure("Title.TLabel", background="#0f172a", foreground="#f8fafc", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background="#0f172a", foreground="#cbd5e1", font=("Microsoft YaHei UI", 10))

        style.configure("SectionTitle.TLabel", background="#ffffff", foreground="#0f172a", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("MetricLabel.TLabel", background="#ffffff", foreground="#64748b", font=("Microsoft YaHei UI", 10))
        style.configure("MetricValue.TLabel", background="#ffffff", foreground="#0f172a", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Body.TLabel", background="#ffffff", foreground="#334155", font=("Microsoft YaHei UI", 10), wraplength=540)

        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 10), background="#2563eb", foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1d4ed8")], foreground=[("active", "#ffffff")])

        style.configure("Secondary.TButton", font=("Microsoft YaHei UI", 10), padding=(14, 10), background="#e2e8f0", foreground="#0f172a", borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#cbd5e1")])

        style.configure("Danger.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 10), background="#dc2626", foreground="#ffffff", borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#b91c1c")], foreground=[("active", "#ffffff")])

    def _build_layout(self):
        outer = ttk.Frame(self.root, style="App.TFrame", padding=18)
        outer.pack(fill=BOTH, expand=True)

        hero = ttk.Frame(outer, style="Hero.TFrame", padding=20)
        hero.pack(fill="x")
        ttk.Label(hero, text="站点监控平台控制面板", style="Title.TLabel").pack(anchor=W)
        ttk.Label(hero, text="替代 bat 菜单的图形面板，支持后台隐藏启动、状态检查、日志查看和快捷访问。", style="Subtitle.TLabel").pack(anchor=W, pady=(6, 0))

        content = ttk.Frame(outer, style="App.TFrame")
        content.pack(fill=BOTH, expand=True, pady=(16, 0))
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(1, weight=1)

        summary = ttk.Frame(content, style="Panel.TFrame", padding=18)
        summary.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._build_summary(summary)

        actions = ttk.Frame(content, style="Panel.TFrame", padding=18)
        actions.grid(row=0, column=1, sticky="nsew")
        self._build_actions(actions)

        log_panel = ttk.Frame(content, style="Panel.TFrame", padding=18)
        log_panel.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        self._build_log_panel(log_panel)

    def _build_summary(self, parent):
        ttk.Label(parent, text="运行状态", style="SectionTitle.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Button(parent, text="刷新", style="Secondary.TButton", command=self.refresh_status).grid(row=0, column=1, sticky=E)

        metrics = ttk.Frame(parent, style="Panel.TFrame")
        metrics.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        for idx in range(2):
            metrics.columnconfigure(idx, weight=1)

        self._metric_card(metrics, 0, 0, "服务状态", self.status_var)
        self._metric_card(metrics, 0, 1, "健康检查", self.health_var)
        self._metric_card(metrics, 1, 0, "监听 PID", self.pid_var)
        self._metric_card(metrics, 1, 1, "启动方式", self.mode_var)

        info = ttk.Frame(parent, style="Panel.TFrame")
        info.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        info.columnconfigure(1, weight=1)
        ttk.Label(info, text="访问地址", style="MetricLabel.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Label(info, textvariable=self.url_var, style="Body.TLabel").grid(row=0, column=1, sticky=W)
        ttk.Label(
            info,
            text="同网段电脑可直接访问上面的地址；隐藏启动会把输出写入 flask.log / flask.err.log。",
            style="Body.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky=W, pady=(10, 0))

    def _metric_card(self, parent, row: int, column: int, label: str, variable: StringVar):
        card = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        card.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0), pady=(0 if row == 0 else 8, 0))
        ttk.Label(card, text=label, style="MetricLabel.TLabel").pack(anchor=W)
        ttk.Label(card, textvariable=variable, style="MetricValue.TLabel").pack(anchor=W, pady=(6, 0))

    def _build_actions(self, parent):
        ttk.Label(parent, text="常用操作", style="SectionTitle.TLabel").pack(anchor=W)
        ttk.Label(parent, text="推荐直接用“后台隐藏启动”，不会弹出 Python 控制台窗口。", style="Body.TLabel").pack(anchor=W, pady=(8, 16))

        action_grid = ttk.Frame(parent, style="Panel.TFrame")
        action_grid.pack(fill="x")
        for idx in range(2):
            action_grid.columnconfigure(idx, weight=1)

        ttk.Button(action_grid, text="后台隐藏启动", style="Primary.TButton", command=lambda: self.run_action("启动", True, start_app, True)).grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 10))
        ttk.Button(action_grid, text="普通启动", style="Secondary.TButton", command=lambda: self.run_action("启动", True, start_app, False)).grid(row=0, column=1, sticky="ew", pady=(0, 10))
        ttk.Button(action_grid, text="后台重启", style="Secondary.TButton", command=lambda: self.run_action("重启", True, restart_app, True)).grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 10))
        ttk.Button(action_grid, text="停止服务", style="Danger.TButton", command=lambda: self.run_action("停止", False, stop_running_app)).grid(row=1, column=1, sticky="ew", pady=(0, 10))
        ttk.Button(action_grid, text="打开浏览器", style="Secondary.TButton", command=self.open_browser).grid(row=2, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(action_grid, text="打开项目目录", style="Secondary.TButton", command=self.open_folder).grid(row=2, column=1, sticky="ew")

        extra = ttk.Frame(parent, style="Panel.TFrame")
        extra.pack(fill="x", pady=(16, 0))
        extra.columnconfigure(0, weight=1)
        extra.columnconfigure(1, weight=1)
        ttk.Button(extra, text="查看运行日志", style="Secondary.TButton", command=lambda: self.open_log(STDOUT_LOG, "运行日志")).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(extra, text="查看错误日志", style="Secondary.TButton", command=lambda: self.open_log(STDERR_LOG, "错误日志")).grid(row=0, column=1, sticky="ew")

    def _build_log_panel(self, parent):
        ttk.Label(parent, text="操作反馈", style="SectionTitle.TLabel").pack(anchor=W)
        ttk.Label(parent, textvariable=self.log_var, style="Body.TLabel").pack(anchor=W, pady=(8, 0))

    def refresh_status(self):
        pid = find_listener_pid()
        running = pid is not None
        healthy = check_health() if running else False

        self.status_var.set("运行中" if running else "未运行")
        self.pid_var.set(str(pid) if pid else "-")
        self.health_var.set("正常" if healthy else ("待启动" if not running else "未通过"))
        if running:
            hidden_guess = "后台隐藏" if STDOUT_LOG.exists() else "运行中"
            self.mode_var.set(hidden_guess)
        else:
            self.mode_var.set("-")

    def _auto_refresh(self):
        self.refresh_status()
        self.root.after(5000, self._auto_refresh)

    def run_action(self, action_name: str, refresh_after: bool, handler, *args):
        def worker():
            ok, message = handler(*args)
            self.root.after(0, lambda: self._finish_action(action_name, ok, message, refresh_after))

        self.log_var.set(f"{action_name}中，请稍候...")
        threading.Thread(target=worker, daemon=True).start()

    def _finish_action(self, action_name: str, ok: bool, message: str, refresh_after: bool):
        if refresh_after:
            self.refresh_status()
        self.log_var.set(message)
        if ok:
            return
        messagebox.showwarning(f"{action_name}提示", message)

    def open_browser(self):
        webbrowser.open(APP_URL)
        self.log_var.set(f"已尝试打开 {APP_URL}")

    def open_folder(self):
        os.startfile(APP_DIR)  # type: ignore[attr-defined]
        self.log_var.set("已打开项目目录。")

    def open_log(self, path: Path, title: str):
        LogViewer(self.root, title, path)
        self.log_var.set(f"正在查看 {path.name}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    StationMonitorControlPanel().run()
