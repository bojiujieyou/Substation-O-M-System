# run_monitor.py — Flask 后端自动守护进程
"""
后台守护脚本：监控 Flask 应用进程，崩溃后自动重启

使用方式:
    python run_monitor.py                    # 前台运行（开发调试用）
    python run_monitor.py --background       # 后台运行（生产环境）
    python run_monitor.py --stop             # 停止守护进程
"""
import os
import sys
import time
import signal
import subprocess
import argparse
import urllib.request
import urllib.error
import psutil
import socket
import threading
import json
from pathlib import Path

# 配置
APP_DIR = Path(__file__).parent.resolve()
APP_SCRIPT = "app.py"
HOST = "127.0.0.1"
PORT = 5000
CHECK_INTERVAL = 10  # 健康检查间隔（秒）
RESTART_DELAY = 3    # 重启前等待（秒）
LOG_FILE = APP_DIR / "monitor.log"


def log(msg):
    """写日志到文件和 stdout"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_port_in_use(port, host=HOST):
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def check_health():
    """检查 Flask 应用是否响应"""
    try:
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/stats",
            headers={"User-Agent": "Monitor/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, socket.timeout, ConnectionRefusedError):
        return False


def get_process_by_port(port):
    """通过端口查找进程 PID"""
    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == "LISTEN":
            try:
                return psutil.Process(conn.pid)
            except psutil.NoSuchProcess:
                pass
    return None


def kill_process_tree(pid, timeout=5):
    """杀死进程树（包含所有子进程）"""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(children + [parent], timeout=timeout)
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        return True
    except psutil.NoSuchProcess:
        return True  # 进程已不存在
    except Exception as e:
        log(f"kill_process_tree 出错: {e}")
        return False


def start_flask():
    """启动 Flask 应用"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # 分离 stdout/stderr，避免输出混淆
    log_file = open(APP_DIR / "flask.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(APP_DIR / APP_SCRIPT)],
        cwd=str(APP_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )
    log_file.close()
    return proc


class Monitor:
    def __init__(self, background=False):
        self.background = background
        self.running = True
        self.proc = None
        self.pid_file = APP_DIR / "monitor.pid"

    def write_pid(self):
        with open(self.pid_file, "w") as f:
            f.write(str(os.getpid()))

    def read_pid(self):
        if self.pid_file.exists():
            with open(self.pid_file) as f:
                return int(f.read().strip())
        return None

    def is_running(self):
        """检查守护进程自身是否在运行"""
        pid = self.read_pid()
        if pid is None:
            return False
        try:
            p = psutil.Process(pid)
            return p.is_running() and p.ppid() != 0
        except (psutil.NoSuchProcess, TypeError):
            return False

    def start(self):
        """启动 Flask 并进入守护循环"""
        # 清理残留 PID 文件
        if self.pid_file.exists():
            old_pid = self.read_pid()
            try:
                old_proc = psutil.Process(old_pid) if old_pid else None
                if old_proc and old_proc.is_running():
                    log(f"检测到已有监控进程 (PID={old_pid})，退出")
                    return
            except (psutil.NoSuchProcess, TypeError):
                pass
            self.pid_file.unlink()

        self.write_pid()
        log("=" * 50)
        log("守护进程启动")
        log(f"检查间隔: {CHECK_INTERVAL}秒")

        # 启动 Flask
        self._ensure_flask()

        restart_count = 0
        consecutive_failures = 0

        while self.running:
            time.sleep(CHECK_INTERVAL)

            # 1. 进程是否存在？
            proc_alive = self.proc is not None and self.proc.poll() is None

            # 2. 端口是否监听？
            port_listening = is_port_in_use(PORT)

            # 3. 健康检查是否通过？
            healthy = check_health()

            if proc_alive and port_listening and healthy:
                consecutive_failures = 0
                continue

            # 检测到异常
            log(f"异常检测 — 进程存活:{proc_alive}, 端口占用:{port_listening}, 健康:{healthy}")

            if not proc_alive or not port_listening:
                consecutive_failures += 1
                log(f"Flask 应用已停止，尝试第 {restart_count + 1} 次重启...")
                if self._restart_flask():
                    restart_count += 1
                    log(f"重启成功 (累计重启 {restart_count} 次)")
                else:
                    log("重启失败，等待下次重试...")
                    time.sleep(RESTART_DELAY)
                    self._ensure_flask()
            elif not healthy:
                # 进程和端口都在，但响应异常（可能是假死）
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    log("连续3次健康检查失败，强制重启...")
                    self._restart_flask()
                    consecutive_failures = 0

        log("守护进程退出")

    def _ensure_flask(self):
        """确保 Flask 在运行"""
        if self.proc is None or self.proc.poll() is not None:
            self._start_flask_internal()
        elif not is_port_in_use(PORT):
            log("Flask 进程存在但端口未监听，重启...")
            self._restart_flask()

    def _start_flask_internal(self):
        """内部启动方法"""
        self.proc = start_flask()
        log(f"Flask 启动中，PID={self.proc.pid}")

    def _restart_flask(self):
        """重启 Flask"""
        log(f"正在停止 Flask (PID={self.proc.pid if self.proc else '?'})...")
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            except Exception as e:
                log(f"停止旧进程出错: {e}")

        time.sleep(RESTART_DELAY)
        self._start_flask_internal()
        return True

    def stop(self):
        """停止守护进程"""
        pid = self.read_pid()
        if pid is None:
            log("未找到 PID 文件，守护进程可能未运行")
            return

        log(f"正在停止守护进程 (PID={pid})...")
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            parent.terminate()
            parent.wait(timeout=5)
        except psutil.NoSuchProcess:
            log("守护进程已退出")
        except Exception as e:
            log(f"停止守护进程出错: {e}")

        if self.pid_file.exists():
            self.pid_file.unlink()
        log("守护进程已停止")


def handle_signal(signum, frame):
    """处理退出信号"""
    global monitor
    if monitor:
        monitor.running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Flask 应用守护进程")
    parser.add_argument("--stop", action="store_true", help="停止守护进程")
    parser.add_argument("--background", action="store_true", help="后台运行（生产环境）")
    args = parser.parse_args()

    monitor = Monitor(background=args.background)

    if args.stop:
        monitor.stop()
        sys.exit(0)

    # 注册信号处理（支持 Ctrl+C 和 kill 信号优雅退出）
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if args.background:
        # Windows 下后台运行：创建新会话
        if sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            subprocess.Popen(
                [sys.executable, str(__file__)],
                cwd=str(APP_DIR),
                creationflags=DETACHED_PROCESS,
                stdout=open(LOG_FILE, "a", encoding="utf-8"),
                stderr=subprocess.STDOUT
            )
            print(f"守护进程已在后台启动，PID: {os.getpid()}")
        else:
            # Unix 后台运行
            subprocess.Popen(
                [sys.executable, str(__file__)],
                cwd=str(APP_DIR),
                stdout=open(LOG_FIFO, "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            print(f"守护进程已在后台启动")
    else:
        monitor.start()
