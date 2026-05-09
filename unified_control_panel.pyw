#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Control Panel — 统一运维控制面板
整合：变电站图像监控运维平台、Hermes、NanoBot、OpenClaw
风格：深色工业风 (Dark Industrial Theme)
框架：PyQt6
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QSize, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette, QAction
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTabBar, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QFileDialog, QMenu,
)

# =============================================================================
# 全局常量与主题配置
# =============================================================================

DARK_BG = "#0d1117"           # 最深背景
PANEL_BG = "#161b22"          # 面板背景
CARD_BG = "#1c2128"           # 卡片背景
CARD_HOVER = "#21262d"        # 卡片悬停
BORDER = "#30363d"            # 边框
BORDER_LIGHT = "#484f58"      # 亮边框
TEXT_PRIMARY = "#c9d1d9"      # 主文本
TEXT_SECONDARY = "#8b949e"    # 次要文本
TEXT_MUTED = "#6e7681"        # 弱化文本
ACCENT_BLUE = "#58a6ff"       # 科技蓝
ACCENT_GREEN = "#3fb950"      # 成功绿
ACCENT_ORANGE = "#f0883e"     # 警告橙
ACCENT_RED = "#f85149"        # 错误红
ACCENT_PURPLE = "#a371f7"     # 强调紫
STATUS_RUNNING = ACCENT_GREEN
STATUS_STOPPED = ACCENT_RED
STATUS_WARNING = ACCENT_ORANGE
STATUS_UNKNOWN = TEXT_MUTED
FONT_FAMILY = "Microsoft YaHei UI"
MONO_FONT = "Consolas, JetBrains Mono, monospace"
REFRESH_INTERVAL_MS = 5000

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def run_subprocess(cmd: list[str], timeout: int = 15, **kwargs) -> subprocess.CompletedProcess[str]:
    """通用子进程执行，隐藏窗口。"""
    kw = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "creationflags": CREATE_NO_WINDOW,
    }
    kw.update(kwargs)
    return subprocess.run(cmd, **kw)


def command_output(completed: subprocess.CompletedProcess[str]) -> str:
    return (completed.stdout or completed.stderr or "").strip()


def is_port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


def find_listener_pid(port: int) -> int | None:
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr and conn.status == psutil.CONN_LISTEN and conn.laddr.port == port and conn.pid:
            return int(conn.pid)
    return None


def get_process_label(pid: int | None) -> str:
    if not pid:
        return "-"
    try:
        proc = psutil.Process(pid)
        cmd = " ".join(proc.cmdline())
        return os.path.basename(cmd.split()[0]) if cmd else proc.name()
    except Exception:
        return f"PID {pid}"


def read_log_tail(path: Path, lines: int = 30) -> str:
    if not path.exists():
        return f"日志文件尚未生成: {path.name}"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not content:
            return "日志文件为空。"
        return "\n".join(content[-lines:])
    except Exception as e:
        return f"读取日志失败: {e}"


# =============================================================================
# 平台配置定义
# =============================================================================

@dataclass
class PlatformConfig:
    key: str
    name: str
    display_name: str
    app_dir: Path
    port: int
    health_url: str | None
    app_script: Path | None = None
    start_script: Path | None = None
    gateway_cmd: Path | None = None
    env_file: Path | None = None
    stdout_log: Path | None = None
    stderr_log: Path | None = None
    service_log: Path | None = None
    # Hermes / OpenClaw WSL 相关
    is_wsl: bool = False
    wsl_distro: str = ""
    wsl_user: str = ""
    wsl_home: str = ""
    wsl_bin: str = ""
    wsl_service: str = ""
    wsl_config_dir: Path | None = None
    wsl_config_file: Path | None = None
    wsl_keepalive_script: Path | None = None
    wsl_keepalive_task: str = ""
    # 自定义启动/停止/状态函数名
    custom_actions: bool = False
    # 颜色
    accent: str = ACCENT_BLUE
    # 额外日志
    extra_logs: dict[str, Path] = field(default_factory=dict)


PLATFORMS: list[PlatformConfig] = []


def _init_platforms():
    """初始化各平台配置。在用户主目录和已知路径下查找。"""
    global PLATFORMS
    user_home = Path.home()

    # 1. 变电站图像监控运维平台
    station_paths = [
        Path(r"E:\项目\变电站图像监控运维平台"),
        user_home / "变电站图像监控运维平台",
    ]
    station_dir = None
    for p in station_paths:
        if (p / "control_panel.pyw").exists():
            station_dir = p
            break
    if station_dir:
        PLATFORMS.append(PlatformConfig(
            key="station",
            name="station",
            display_name="运维平台",
            app_dir=station_dir,
            port=5000,
            health_url="http://127.0.0.1:5000/health",
            app_script=station_dir / "app.py",
            env_file=station_dir / ".env",
            stdout_log=station_dir / "flask.log",
            stderr_log=station_dir / "flask.err.log",
            accent="#2563eb",
        ))

    # 2. Hermes
    hermes_paths = [
        Path(r"E:\项目\hermes-panel"),
        user_home / "hermes-panel",
    ]
    hermes_dir = None
    for p in hermes_paths:
        if (p / "control_panel.pyw").exists():
            hermes_dir = p
            break
    if hermes_dir:
        distro = os.environ.get("HERMES_WSL_DISTRO", "Ubuntu")
        wsl_user = os.environ.get("HERMES_WSL_USER", "administrator")
        wsl_home = os.environ.get("HERMES_WSL_HOME", f"/home/{wsl_user}")
        wsl_hermes_home = os.environ.get("HERMES_WSL_CONFIG_HOME", f"{wsl_home}/.hermes")
        wsl_bin = os.environ.get("HERMES_WSL_BIN", f"{wsl_home}/.local/bin/hermes")
        _sep = "\\"
        wsl_dir = Path(rf"\\wsl$\{distro}" + wsl_hermes_home.replace("/", _sep))
        PLATFORMS.append(PlatformConfig(
            key="hermes",
            name="hermes",
            display_name="Hermes",
            app_dir=hermes_dir,
            port=8642,
            health_url="http://127.0.0.1:8642/health",
            is_wsl=True,
            wsl_distro=distro,
            wsl_user=wsl_user,
            wsl_home=wsl_home,
            wsl_bin=wsl_bin,
            wsl_config_dir=wsl_dir,
            wsl_config_file=wsl_dir / "config.yaml",
            accent="#0f9d71",
        ))

    # 3. NanoBot
    nanobot_paths = [
        user_home / ".nanobot",
        Path(r"C:\Users\Administrator\.nanobot"),
    ]
    nanobot_dir = None
    for p in nanobot_paths:
        if (p / "control_panel.pyw").exists():
            nanobot_dir = p
            break
    if nanobot_dir:
        PLATFORMS.append(PlatformConfig(
            key="nanobot",
            name="nanobot",
            display_name="NanoBot",
            app_dir=nanobot_dir,
            port=18790,
            health_url="http://127.0.0.1:18790/health",
            start_script=nanobot_dir / "start_nanobot.bat",
            stdout_log=nanobot_dir / "nanobot-panel.log",
            stderr_log=nanobot_dir / "nanobot-panel.err.log",
            accent="#a371f7",
            extra_logs={
                "配置": nanobot_dir / "config.json",
                "Profiles": nanobot_dir / "api_profiles.json",
            },
        ))

    # 4. OpenClaw
    openclaw_paths = [
        user_home / ".openclaw",
        Path(r"C:\Users\Administrator\.openclaw"),
    ]
    openclaw_dir = None
    for p in openclaw_paths:
        if (p / "control_panel.pyw").exists():
            openclaw_dir = p
            break
    if openclaw_dir:
        win_port = int(os.environ.get("OPENCLAW_WINDOWS_PORT", "18790"))
        distro = os.environ.get("OPENCLAW_WSL_DISTRO", "Ubuntu")
        wsl_user = os.environ.get("OPENCLAW_WSL_USER", "administrator")
        wsl_home = os.environ.get("OPENCLAW_WSL_HOME", f"/home/{wsl_user}")
        _sep = "\\"
        wsl_config_dir = Path(rf"\\wsl$\{distro}" + wsl_home.replace("/", _sep) + "\\.openclaw")
        keepalive_script = openclaw_dir / "wsl-keepalive.ps1"
        keepalive_task = os.environ.get("OPENCLAW_WSL_KEEPALIVE_TASK", "OpenClaw WSL Keepalive")
        PLATFORMS.append(PlatformConfig(
            key="openclaw",
            name="openclaw",
            display_name="OpenClaw",
            app_dir=openclaw_dir,
            port=win_port,
            health_url=f"http://127.0.0.1:{win_port}/health",
            gateway_cmd=openclaw_dir / "gateway.cmd",
            stdout_log=openclaw_dir / "gateway-panel.log",
            stderr_log=openclaw_dir / "gateway-panel.err.log",
            service_log=openclaw_dir / "gateway.log",
            is_wsl=True,
            wsl_distro=distro,
            wsl_user=wsl_user,
            wsl_home=wsl_home,
            wsl_bin=os.environ.get("OPENCLAW_WSL_OPENCLAW", "/usr/local/bin/openclaw"),
            wsl_service=os.environ.get("OPENCLAW_WSL_SERVICE", "openclaw-gateway.service"),
            wsl_config_dir=wsl_config_dir,
            wsl_config_file=wsl_config_dir / "openclaw.json",
            wsl_keepalive_script=keepalive_script if keepalive_script.exists() else None,
            wsl_keepalive_task=keepalive_task,
            custom_actions=True,
            accent="#f0883e",
            extra_logs={
                "gateway.log": openclaw_dir / "gateway.log",
                "手动日志": openclaw_dir / "gateway-manual-run.log",
                "WSL保活": openclaw_dir / "wsl-keepalive.log",
            },
        ))


# =============================================================================
# 平台动作封装
# =============================================================================

class PlatformActions:
    def __init__(self, cfg: PlatformConfig):
        self.cfg = cfg

    def _is_wsl_running(self) -> bool:
        """检查指定的 WSL 发行版是否已运行。
        通过检查 \\wsl$\<distro> 文件系统路径是否存在来判断，
        不会意外启动 WSL，也不依赖命令行输出解析。"""
        if not self.cfg.is_wsl:
            return True
        try:
            # \\wsl$\<distro> 只在 WSL 运行时存在
            wsl_path = Path(rf"\\wsl$\{self.cfg.wsl_distro}")
            return wsl_path.exists()
        except Exception:
            pass
        return False

    # ---- 通用: 状态检测 ----
    def status(self) -> dict:
        if self.cfg.is_wsl and not self._is_wsl_running():
            return {
                "pid": None,
                "running": False,
                "healthy": False,
                "process": "WSL 未启动",
            }
        if self.cfg.key == "hermes":
            return self._status_hermes()
        pid = find_listener_pid(self.cfg.port)
        running = pid is not None
        healthy = False
        if running and self.cfg.health_url:
            healthy = self._check_health()
        return {
            "pid": pid,
            "running": running,
            "healthy": healthy,
            "process": get_process_label(pid),
        }

    def _status_hermes(self) -> dict:
        """Hermes 运行在 WSL 内部，通过 WSL 命令检测状态"""
        bin_path = self.cfg.wsl_bin
        try:
            res = self._run_wsl("sh", "-lc", f"{shlex.quote(bin_path)} gateway status", timeout=8)
            text = command_output(res)
            if "running" in text.lower():
                # 提取 PID
                pid_match = re.search(r"PID:\s*(\d+)", text)
                pid = int(pid_match.group(1)) if pid_match else None
                return {
                    "pid": pid,
                    "running": True,
                    "healthy": True,
                    "process": "hermes-gateway",
                }
            else:
                return {
                    "pid": None,
                    "running": False,
                    "healthy": False,
                    "process": "-",
                }
        except Exception:
            return {
                "pid": None,
                "running": False,
                "healthy": False,
                "process": "-",
            }

    def _check_health(self) -> bool:
        if not self.cfg.health_url:
            return False
        try:
            req = urllib.request.Request(
                self.cfg.health_url,
                headers={"User-Agent": "UnifiedControlPanel/1.0"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ---- 通用: 启动 ----
    def start(self, hidden: bool = True) -> tuple[bool, str]:
        st = self.status()
        if st["running"]:
            return False, f"服务已在运行 (PID {st['pid']})"

        if self.cfg.key == "station":
            return self._start_station(hidden)
        elif self.cfg.key == "nanobot":
            return self._start_nanobot(hidden)
        elif self.cfg.key == "openclaw":
            return self._start_openclaw(hidden)
        elif self.cfg.key == "hermes":
            return self._start_hermes()
        return False, "未实现启动逻辑"

    def _start_station(self, hidden: bool) -> tuple[bool, str]:
        script = self.cfg.app_script
        if not script or not script.exists():
            return False, "找不到 app.py"
        python_exe = sys.executable
        if hidden:
            pw = Path(python_exe).with_name("pythonw.exe")
            if pw.exists():
                python_exe = str(pw)
        stdout_log = self.cfg.stdout_log or (self.cfg.app_dir / "flask.log")
        stderr_log = self.cfg.stderr_log or (self.cfg.app_dir / "flask.err.log")
        env = os.environ.copy()
        if self.cfg.env_file and self.cfg.env_file.exists():
            for line in self.cfg.env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        flags = CREATE_NO_WINDOW if hidden and os.name == "nt" else 0
        try:
            with open(stdout_log, "a", encoding="utf-8") as so, open(stderr_log, "a", encoding="utf-8") as se:
                subprocess.Popen(
                    [python_exe, str(script)],
                    cwd=self.cfg.app_dir,
                    env=env,
                    stdout=so,
                    stderr=se,
                    creationflags=flags,
                )
        except Exception as e:
            return False, f"启动失败: {e}"
        for _ in range(20):
            time.sleep(0.5)
            if is_port_listening("127.0.0.1", self.cfg.port):
                return True, f"已{'隐藏' if hidden else '前台'}启动"
        return False, "启动命令已发出，但端口未就绪"

    def _start_nanobot(self, hidden: bool) -> tuple[bool, str]:
        bat = self.cfg.start_script
        if not bat or not bat.exists():
            return False, "找不到 start_nanobot.bat"
        stdout_log = self.cfg.stdout_log or (self.cfg.app_dir / "nanobot-panel.log")
        stderr_log = self.cfg.stderr_log or (self.cfg.app_dir / "nanobot-panel.err.log")
        if hidden:
            try:
                with open(stdout_log, "a", encoding="utf-8") as so, open(stderr_log, "a", encoding="utf-8") as se:
                    subprocess.Popen(
                        ["cmd.exe", "/c", str(bat)],
                        cwd=self.cfg.app_dir,
                        stdout=so,
                        stderr=se,
                        creationflags=CREATE_NO_WINDOW,
                    )
            except Exception as e:
                return False, f"启动失败: {e}"
        else:
            subprocess.Popen(
                ["cmd.exe", "/k", str(bat)],
                cwd=self.cfg.app_dir,
                creationflags=CREATE_NEW_CONSOLE,
            )
        for _ in range(30):
            time.sleep(0.5)
            if is_port_listening("127.0.0.1", self.cfg.port):
                return True, f"已{'隐藏' if hidden else '前台'}启动"
        return False, "启动命令已发出，但端口未就绪"

    def _start_openclaw(self, hidden: bool) -> tuple[bool, str]:
        cmd_file = self.cfg.gateway_cmd
        if not cmd_file or not cmd_file.exists():
            return False, "找不到 gateway.cmd"
        stdout_log = self.cfg.stdout_log or (self.cfg.app_dir / "gateway-panel.log")
        stderr_log = self.cfg.stderr_log or (self.cfg.app_dir / "gateway-panel.err.log")
        if hidden:
            try:
                with open(stdout_log, "a", encoding="utf-8") as so, open(stderr_log, "a", encoding="utf-8") as se:
                    subprocess.Popen(
                        ["cmd.exe", "/c", str(cmd_file)],
                        cwd=self.cfg.app_dir,
                        stdout=so,
                        stderr=se,
                        creationflags=CREATE_NO_WINDOW,
                    )
            except Exception as e:
                return False, f"启动失败: {e}"
        else:
            subprocess.Popen(
                ["cmd.exe", "/k", str(cmd_file)],
                cwd=self.cfg.app_dir,
                creationflags=CREATE_NEW_CONSOLE,
            )
        for _ in range(40):
            time.sleep(1)
            if is_port_listening("127.0.0.1", self.cfg.port):
                return True, f"已{'隐藏' if hidden else '前台'}启动"
        return False, "启动命令已发出，但端口未就绪"

    def _start_hermes(self) -> tuple[bool, str]:
        """Hermes 启动：使用 nohup 后台运行"""
        bin_path = self.cfg.wsl_bin
        # 先检查是否已在运行
        try:
            res = self._run_wsl("sh", "-lc", f"{shlex.quote(bin_path)} gateway status", timeout=8)
            text = command_output(res)
            if "running" in text.lower():
                pid_match = re.search(r"PID:\s*(\d+)", text)
                pid = pid_match.group(1) if pid_match else "-"
                return False, f"Hermes 网关已在运行 (PID {pid})"
        except Exception:
            pass

        # 使用 nohup 在后台启动
        self._run_wsl("sh", "-lc", f"nohup {shlex.quote(bin_path)} gateway run > /dev/null 2>&1 &", timeout=8)

        # 等待确认运行
        for _ in range(20):
            time.sleep(0.5)
            try:
                res = self._run_wsl("sh", "-lc", f"{shlex.quote(bin_path)} gateway status", timeout=8)
                text = command_output(res)
                if "running" in text.lower():
                    pid_match = re.search(r"PID:\s*(\d+)", text)
                    pid = pid_match.group(1) if pid_match else "-"
                    return True, f"已启动 Hermes 网关 (PID {pid})"
            except Exception:
                continue
        return False, "启动命令已发出，但未确认运行状态"

    # ---- 通用: 停止 ----
    def stop(self) -> tuple[bool, str]:
        if self.cfg.key == "openclaw":
            return self._stop_openclaw()
        elif self.cfg.key == "hermes":
            return self._stop_hermes()
        pid = find_listener_pid(self.cfg.port)
        if not pid:
            return True, "当前没有运行中的服务"
        result = run_subprocess(["taskkill", "/PID", str(pid), "/F"], timeout=10)
        if result.returncode == 0:
            return True, f"已停止 (PID {pid})"
        err = command_output(result)
        return False, err or f"停止失败 (PID {pid})"

    def _stop_openclaw(self) -> tuple[bool, str]:
        task_name = "OpenClaw Gateway"
        msgs: list[str] = []
        task_check = run_subprocess(["schtasks", "/Query", "/TN", task_name], timeout=8)
        if task_check.returncode == 0:
            end_result = run_subprocess(["schtasks", "/End", "/TN", task_name], timeout=8)
            if end_result.returncode == 0:
                msgs.append("已停止计划任务")
        pid = find_listener_pid(self.cfg.port)
        if not pid:
            return True, " ".join(msgs) if msgs else "当前没有运行中的服务"
        result = run_subprocess(["taskkill", "/PID", str(pid), "/F"], timeout=10)
        if result.returncode == 0:
            msgs.append(f"已停止进程 (PID {pid})")
            return True, " ".join(msgs)
        err = command_output(result)
        return False, err or f"停止失败 (PID {pid})"

    def _stop_hermes(self) -> tuple[bool, str]:
        bin_path = self.cfg.wsl_bin

        # 1. 获取 PID 文件中的 PID（Hermes 自己维护的）
        pid = None
        pid_file = self.cfg.wsl_config_dir / "gateway.pid" if self.cfg.wsl_config_dir else None
        if pid_file and pid_file.exists():
            try:
                payload = json.loads(pid_file.read_text(encoding="utf-8", errors="replace"))
                pid = payload.get("pid") if isinstance(payload, dict) else None
            except Exception:
                pass

        # 2. Hermes 自带 stop 命令
        self._run_wsl("sh", "-lc", f"{shlex.quote(bin_path)} gateway stop --all >/dev/null 2>&1 || true")
        time.sleep(0.5)

        # 3. 按 PID kill
        if pid:
            self._run_wsl("sh", "-lc", f"kill {pid} >/dev/null 2>&1 || true")
            time.sleep(0.3)
            self._run_wsl("sh", "-lc", f"kill -9 {pid} >/dev/null 2>&1 || true")

        # 4. pkill 兜底（按进程名，不按完整命令行）
        self._run_wsl("sh", "-lc", f"pkill -f hermes >/dev/null 2>&1 || true")
        time.sleep(0.3)
        self._run_wsl("sh", "-lc", f"pkill -9 -f hermes >/dev/null 2>&1 || true")

        # 5. 强制释放端口
        self._run_wsl("sh", "-lc", "fuser -k 8642/tcp >/dev/null 2>&1 || true")

        time.sleep(1)

        # 6. 验证：检查 PID 文件中的 PID 是否还存在
        if pid_file and pid_file.exists():
            try:
                payload = json.loads(pid_file.read_text(encoding="utf-8", errors="replace"))
                after_pid = payload.get("pid") if isinstance(payload, dict) else None
                if after_pid:
                    # 检查这个 PID 是否还活着
                    check = self._run_wsl("sh", "-lc", f"kill -0 {after_pid} >/dev/null 2>&1 && echo alive || echo dead", timeout=5)
                    if "dead" in command_output(check):
                        return True, "已停止 Hermes 网关"
            except Exception:
                pass

        # 额外验证：gateway status
        try:
            res = self._run_wsl("sh", "-lc", f"{shlex.quote(bin_path)} gateway status", timeout=8)
            text = command_output(res)
            if "running" not in text.lower():
                return True, "已停止 Hermes 网关"
        except Exception:
            return True, "已发送停止命令"

        return False, "停止命令已执行，但服务仍在运行"

    def _run_wsl(self, *args: str, timeout: int = 12) -> subprocess.CompletedProcess[str]:
        cmd = ["wsl", "-d", self.cfg.wsl_distro, "-u", self.cfg.wsl_user, "--cd", self.cfg.wsl_home, "env"]
        env = {"HOME": self.cfg.wsl_home, "USER": self.cfg.wsl_user, "PATH": f"{self.cfg.wsl_home}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        for k, v in env.items():
            cmd.append(f"{k}={v}")
        cmd.extend(args)
        return run_subprocess(cmd, timeout=timeout)

    # ---- 通用: 重启 ----
    def restart(self, hidden: bool = True) -> tuple[bool, str]:
        self.stop()
        time.sleep(1)
        return self.start(hidden)

    # ---- 通用: 打开浏览器 ----
    def open_browser(self) -> tuple[bool, str]:
        url = f"http://127.0.0.1:{self.cfg.port}"
        if self.cfg.health_url:
            url = self.cfg.health_url.rsplit("/", 1)[0]
        webbrowser.open(url)
        return True, f"已打开 {url}"

    # ---- 通用: 打开目录 ----
    def open_folder(self) -> tuple[bool, str]:
        if self.cfg.app_dir.exists():
            os.startfile(str(self.cfg.app_dir))
            return True, "已打开目录"
        return False, "目录不存在"

    # ---- OpenClaw WSL 相关 ----
    def wsl_status(self) -> dict:
        if self.cfg.key != "openclaw":
            return {}
        if not self._is_wsl_running():
            return {"available": False, "service": "WSL 未启动", "health": "-", "version": "-", "pid": "-"}
        distro = self.cfg.wsl_distro
        user = self.cfg.wsl_user
        home = self.cfg.wsl_home
        bin_path = self.cfg.wsl_bin
        service = self.cfg.wsl_service
        env = {"HOME": home, "USER": user, "PATH": f"{home}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        cmd_base = ["wsl", "-d", distro, "-u", user, "--cd", home, "env"]
        for k, v in env.items():
            cmd_base.append(f"{k}={v}")

        result = {"available": False, "service": "unknown", "health": "unknown", "version": "-", "pid": "-"}

        # version
        try:
            version_res = run_subprocess(cmd_base + ["sh", "-lc", f"{shlex.quote(bin_path)} --version"], timeout=10)
            version_text = command_output(version_res)
            result["version"] = version_text.replace("OpenClaw ", "", 1) if version_res.returncode == 0 else "命令失败"
        except Exception:
            result["version"] = "不可用"
            return result

        # service state
        try:
            svc_res = run_subprocess(cmd_base + ["systemctl", "show", service, "--property=ActiveState", "--property=SubState", "--property=MainPID", "--no-page"], timeout=10)
            svc_data: dict[str, str] = {}
            for line in command_output(svc_res).splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    svc_data[k.strip()] = v.strip()
            active = svc_data.get("ActiveState", "unknown")
            sub = svc_data.get("SubState", "unknown")
            result["service"] = f"{active}/{sub}"
            result["pid"] = svc_data.get("MainPID", "0")
            if result["pid"] == "0":
                result["pid"] = "-"
        except Exception:
            result["service"] = "不可用"

        # health
        if result["service"].startswith("active/running"):
            try:
                health_res = run_subprocess(cmd_base + ["sh", "-lc", f"{shlex.quote(bin_path)} health"], timeout=12)
                health_text = command_output(health_res)
                if health_res.returncode == 0:
                    dc = re.search(r"^Discord:\s*(.+)$", health_text, re.M | re.I)
                    fs = re.search(r"^Feishu:\s*(.+)$", health_text, re.M | re.I)
                    dc_st = dc.group(1).strip() if dc else "未知"
                    fs_st = fs.group(1).strip() if fs else "未知"
                    result["channels"] = f"Discord: {dc_st} | 飞书: {fs_st}"
                    if "ok" in dc_st.lower() and "ok" in fs_st.lower():
                        result["health"] = "正常"
                    elif "ok" in dc_st.lower() or "ok" in fs_st.lower():
                        result["health"] = "部分正常"
                    else:
                        result["health"] = "需关注"
                else:
                    result["health"] = "命令失败"
            except Exception:
                result["health"] = "超时"
        else:
            result["health"] = "未运行"

        result["available"] = True
        return result

    def wsl_action(self, action: str) -> tuple[bool, str]:
        if self.cfg.key != "openclaw":
            return False, "不支持"
        distro = self.cfg.wsl_distro
        user = self.cfg.wsl_user
        home = self.cfg.wsl_home
        service = self.cfg.wsl_service
        env = {"HOME": home, "USER": user, "PATH": f"{home}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        cmd_base = ["wsl", "-d", distro, "-u", "root", "--"]
        try:
            res = run_subprocess(cmd_base + ["systemctl", action, service], timeout=20)
            if res.returncode != 0:
                return False, command_output(res) or f"{action} 失败"
        except Exception as e:
            return False, str(e)
        time.sleep(1)
        st = self.wsl_status()
        if action in ("start", "restart"):
            action_name = "启动" if action == "start" else "重启"
            if st.get("service", "").startswith("active"):
                return True, f"已{action_name} WSL 服务"
            return False, f"操作后状态: {st.get('service', 'unknown')}"
        else:
            if "inactive" in st.get("service", ""):
                return True, "已停止 WSL 服务"
            return False, f"操作后状态: {st.get('service', 'unknown')}"

    def wsl_keepalive_status(self) -> dict:
        if self.cfg.key != "openclaw":
            return {}
        task_name = self.cfg.wsl_keepalive_task
        result = {"exists": False, "state": "未安装", "pid": "-"}
        try:
            exists_res = run_subprocess(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                 f"$t=Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; if($t){{'true'}}else{{'false'}}"],
                timeout=10,
            )
            if exists_res.returncode == 0 and "true" in (exists_res.stdout or "").lower():
                result["exists"] = True
                state_res = run_subprocess(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                     f"(Get-ScheduledTask -TaskName '{task_name}' | Select-Object -ExpandProperty State)"],
                    timeout=10,
                )
                result["state"] = (state_res.stdout or "").strip() or "Unknown"
                # find keepalive pids
                marker = str(self.cfg.wsl_keepalive_script or "").lower()
                pids: list[int] = []
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    try:
                        name = (proc.info.get("name") or "").lower()
                        cmdline = " ".join(proc.info.get("cmdline") or [])
                        if "powershell" in name and marker in cmdline.lower():
                            pids.append(int(proc.info["pid"]))
                    except Exception:
                        continue
                result["pid"] = str(pids[0]) if pids else "-"
                if result["pid"] != "-":
                    result["state"] = "Running"
            else:
                result["state"] = "未安装"
        except Exception:
            result["state"] = "不可用"
        return result

    def wsl_keepalive_action(self, action: str) -> tuple[bool, str]:
        if self.cfg.key != "openclaw" or not self.cfg.wsl_keepalive_script:
            return False, "不支持"
        script = str(self.cfg.wsl_keepalive_script).replace("'", "''")
        task_name = self.cfg.wsl_keepalive_task
        if action == "start":
            try:
                res = run_subprocess(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                     f"Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File','{script}' -WindowStyle Hidden -PassThru | Select-Object -ExpandProperty Id"],
                    timeout=10,
                )
                if res.returncode != 0:
                    return False, command_output(res) or "启动失败"
                return True, "已启动 WSL 保活任务"
            except Exception as e:
                return False, str(e)
        elif action == "stop":
            try:
                run_subprocess(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                     f"Stop-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue"],
                    timeout=12,
                )
                # kill processes
                marker = str(self.cfg.wsl_keepalive_script).lower()
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    try:
                        name = (proc.info.get("name") or "").lower()
                        cmdline = " ".join(proc.info.get("cmdline") or [])
                        if "powershell" in name and marker in cmdline.lower():
                            psutil.Process(int(proc.info["pid"])).kill()
                    except Exception:
                        continue
                return True, "已停止 WSL 保活任务"
            except Exception as e:
                return False, str(e)
        return False, "不支持的操作"


# =============================================================================
# PyQt6 自定义控件
# =============================================================================

class StatusBadge(QFrame):
    def __init__(self, text: str = "-", color: str = STATUS_UNKNOWN, parent=None):
        super().__init__(parent)
        self._text = text
        self._color = color
        self.setFixedHeight(28)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            StatusBadge {{
                background-color: {self._color}22;
                border: 1px solid {self._color}66;
                border-radius: 4px;
            }}
        """)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 0, 10, 0)
        self.label = QLabel(text)
        self.label.setStyleSheet(f"color: {self._color}; font-weight: bold; font-size: 12px;")
        self.layout.addWidget(self.label)

    def set_status(self, text: str, color: str):
        self._text = text
        self._color = color
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 12px;")
        self.setStyleSheet(f"""
            StatusBadge {{
                background-color: {color}22;
                border: 1px solid {color}66;
                border-radius: 4px;
            }}
        """)


class MetricCard(QFrame):
    def __init__(self, label: str, value: str = "-", note: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            MetricCard {{
                background-color: {CARD_BG};
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.label_lbl = QLabel(label)
        self.label_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self.label_lbl)
        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: bold;")
        layout.addWidget(self.value_lbl)
        if note:
            self.note_lbl = QLabel(note)
            self.note_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px;")
            self.note_lbl.setWordWrap(True)
            layout.addWidget(self.note_lbl)

    def set_value(self, value: str):
        self.value_lbl.setText(value)


class StyledButton(QPushButton):
    def __init__(self, text: str, btn_type: str = "secondary", parent=None):
        super().__init__(text, parent)
        self.btn_type = btn_type
        self._update_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)

    def _update_style(self):
        if self.btn_type == "primary":
            color = ACCENT_BLUE
            hover = "#79b8ff"
            text = "#0d1117"
        elif self.btn_type == "success":
            color = ACCENT_GREEN
            hover = "#56d364"
            text = "#0d1117"
        elif self.btn_type == "danger":
            color = ACCENT_RED
            hover = "#ff7b72"
            text = "#0d1117"
        elif self.btn_type == "warning":
            color = ACCENT_ORANGE
            hover = "#ffa657"
            text = "#0d1117"
        else:
            color = BORDER
            hover = BORDER_LIGHT
            text = TEXT_PRIMARY
        self.setStyleSheet(f"""
            StyledButton {{
                background-color: {color};
                color: {text};
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-weight: bold;
                font-size: 13px;
            }}
            StyledButton:hover {{
                background-color: {hover};
            }}
            StyledButton:pressed {{
                background-color: {color};
                opacity: 0.8;
            }}
            StyledButton:disabled {{
                background-color: {BORDER};
                color: {TEXT_MUTED};
            }}
        """)


class LogViewer(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setStyleSheet(f"""
            LogViewer {{
                background-color: {DARK_BG};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 12px;
                font-family: {MONO_FONT};
                font-size: 12px;
                line-height: 1.5;
            }}
        """)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

    def set_log_text(self, text: str):
        self.setPlainText(text)
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class ActionThread(QThread):
    finished_sig = pyqtSignal(bool, str)

    def __init__(self, handler: Callable, *args):
        super().__init__()
        self.handler = handler
        self.args = args

    def run(self):
        try:
            ok, message = self.handler(*self.args)
            self.finished_sig.emit(ok, message)
        except Exception as e:
            self.finished_sig.emit(False, f"执行异常: {e}")


# =============================================================================
# 平台标签页
# =============================================================================

class PlatformTab(QWidget):
    def __init__(self, cfg: PlatformConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.actions = PlatformActions(cfg)
        self._init_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_status)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)
        self.refresh_status()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部状态栏
        self.header = QFrame()
        self.header.setStyleSheet(f"background-color: {PANEL_BG}; border-bottom: 1px solid {BORDER};")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(20, 14, 20, 14)

        self.title_lbl = QLabel(f"<span style='color:{self.cfg.accent}; font-size:20px; font-weight:bold;'>{self.cfg.display_name}</span>")
        self.title_lbl.setTextFormat(Qt.TextFormat.RichText)
        header_layout.addWidget(self.title_lbl)

        header_layout.addStretch()

        self.badge = StatusBadge("检查中...", STATUS_UNKNOWN)
        header_layout.addWidget(self.badge)

        self.refresh_btn = StyledButton("刷新", "secondary")
        self.refresh_btn.setFixedWidth(80)
        self.refresh_btn.clicked.connect(self.refresh_status)
        header_layout.addWidget(self.refresh_btn)

        layout.addWidget(self.header)

        # 滚动内容区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setStyleSheet(f"background-color: {DARK_BG};")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)

        # 指标卡片行
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)
        self.metric_pid = MetricCard("进程 PID", "-", "当前监听端口的进程")
        self.metric_status = MetricCard("服务状态", "-", "进程是否在运行")
        self.metric_health = MetricCard("健康检查", "-", "HTTP /health 探测")
        self.metric_process = MetricCard("进程名称", "-", "进程命令行识别")
        metrics_row.addWidget(self.metric_pid, 1)
        metrics_row.addWidget(self.metric_status, 1)
        metrics_row.addWidget(self.metric_health, 1)
        metrics_row.addWidget(self.metric_process, 1)
        content_layout.addLayout(metrics_row)

        # 操作按钮行
        actions_group = QGroupBox("进程控制")
        actions_group.setStyleSheet(f"""
            QGroupBox {{
                color: {TEXT_SECONDARY};
                font-weight: bold;
                font-size: 13px;
                border: 1px solid {BORDER};
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
        """)
        actions_layout = QHBoxLayout(actions_group)
        actions_layout.setSpacing(10)
        self.btn_start_hidden = StyledButton("隐藏启动", "success")
        self.btn_start_hidden.clicked.connect(lambda: self._run_action("隐藏启动", self.actions.start, True))
        self.btn_start_visible = StyledButton("前台启动", "secondary")
        self.btn_start_visible.clicked.connect(lambda: self._run_action("前台启动", self.actions.start, False))
        self.btn_restart = StyledButton("重启服务", "warning")
        self.btn_restart.clicked.connect(lambda: self._run_action("重启服务", self.actions.restart, True))
        self.btn_stop = StyledButton("停止服务", "danger")
        self.btn_stop.clicked.connect(lambda: self._run_action("停止服务", self.actions.stop))
        actions_layout.addWidget(self.btn_start_hidden)
        actions_layout.addWidget(self.btn_start_visible)
        actions_layout.addWidget(self.btn_restart)
        actions_layout.addWidget(self.btn_stop)
        actions_layout.addStretch()
        content_layout.addWidget(actions_group)

        # 快捷入口
        shortcuts_group = QGroupBox("快捷入口")
        shortcuts_group.setStyleSheet(actions_group.styleSheet())
        shortcuts_layout = QHBoxLayout(shortcuts_group)
        shortcuts_layout.setSpacing(10)
        self.btn_browser = StyledButton("打开浏览器", "secondary")
        self.btn_browser.clicked.connect(lambda: self._run_action("打开浏览器", self.actions.open_browser))
        self.btn_folder = StyledButton("打开项目目录", "secondary")
        self.btn_folder.clicked.connect(lambda: self._run_action("打开目录", self.actions.open_folder))
        shortcuts_layout.addWidget(self.btn_browser)
        shortcuts_layout.addWidget(self.btn_folder)
        # 平台特定按钮
        if self.cfg.key == "nanobot":
            self.btn_config = StyledButton("打开配置", "secondary")
            self.btn_config.clicked.connect(self._open_nanobot_config)
            shortcuts_layout.addWidget(self.btn_config)
        shortcuts_layout.addStretch()
        content_layout.addWidget(shortcuts_group)

        # WSL 控制 (OpenClaw / Hermes)
        if self.cfg.is_wsl:
            wsl_group = QGroupBox("WSL 控制")
            wsl_group.setStyleSheet(actions_group.styleSheet())
            wsl_layout = QVBoxLayout(wsl_group)

            wsl_buttons = QHBoxLayout()
            if self.cfg.key == "openclaw":
                self.btn_wsl_start = StyledButton("启动 WSL", "success")
                self.btn_wsl_start.clicked.connect(lambda: self._run_action("启动 WSL", self.actions.wsl_action, "start"))
                self.btn_wsl_restart = StyledButton("重启 WSL", "warning")
                self.btn_wsl_restart.clicked.connect(lambda: self._run_action("重启 WSL", self.actions.wsl_action, "restart"))
                self.btn_wsl_stop = StyledButton("停止 WSL", "danger")
                self.btn_wsl_stop.clicked.connect(lambda: self._run_action("停止 WSL", self.actions.wsl_action, "stop"))
                wsl_buttons.addWidget(self.btn_wsl_start)
                wsl_buttons.addWidget(self.btn_wsl_restart)
                wsl_buttons.addWidget(self.btn_wsl_stop)

                # 保活控制
                wsl_buttons.addSpacing(20)
                self.btn_keepalive_start = StyledButton("启动保活", "success")
                self.btn_keepalive_start.clicked.connect(lambda: self._run_action("启动保活", self.actions.wsl_keepalive_action, "start"))
                self.btn_keepalive_stop = StyledButton("停止保活", "danger")
                self.btn_keepalive_stop.clicked.connect(lambda: self._run_action("停止保活", self.actions.wsl_keepalive_action, "stop"))
                wsl_buttons.addWidget(self.btn_keepalive_start)
                wsl_buttons.addWidget(self.btn_keepalive_stop)

            wsl_buttons.addStretch()
            wsl_layout.addLayout(wsl_buttons)

            # WSL 状态卡片
            wsl_metrics = QHBoxLayout()
            self.wsl_metric_service = MetricCard("WSL 服务", "-", "systemd 服务状态")
            self.wsl_metric_health = MetricCard("WSL 健康", "-", "WSL 内部 health 检查")
            self.wsl_metric_version = MetricCard("WSL 版本", "-", "openclaw --version")
            wsl_metrics.addWidget(self.wsl_metric_service, 1)
            wsl_metrics.addWidget(self.wsl_metric_health, 1)
            wsl_metrics.addWidget(self.wsl_metric_version, 1)
            wsl_layout.addLayout(wsl_metrics)
            content_layout.addWidget(wsl_group)

        # 日志区
        log_group = QGroupBox("日志监视")
        log_group.setStyleSheet(actions_group.styleSheet())
        log_layout = QVBoxLayout(log_group)
        log_layout.setSpacing(8)

        # 日志选择 + 控制
        log_toolbar = QHBoxLayout()
        self.log_combo = QComboBox()
        self.log_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {CARD_BG};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 6px 10px;
                min-width: 200px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {CARD_BG};
                color: {TEXT_PRIMARY};
                selection-background-color: {ACCENT_BLUE}33;
            }}
        """)
        self.log_combo.currentTextChanged.connect(self._on_log_changed)
        log_toolbar.addWidget(QLabel("选择日志:"))
        log_toolbar.addWidget(self.log_combo)
        log_toolbar.addStretch()

        self.btn_log_refresh = StyledButton("刷新", "secondary")
        self.btn_log_refresh.setFixedWidth(70)
        self.btn_log_refresh.clicked.connect(self._refresh_current_log)
        log_toolbar.addWidget(self.btn_log_refresh)

        self.btn_log_export = StyledButton("导出", "secondary")
        self.btn_log_export.setFixedWidth(70)
        self.btn_log_export.clicked.connect(self._export_log)
        log_toolbar.addWidget(self.btn_log_export)

        log_layout.addLayout(log_toolbar)

        self.log_viewer = LogViewer()
        self.log_viewer.setMinimumHeight(300)
        log_layout.addWidget(self.log_viewer)
        content_layout.addWidget(log_group, 1)

        self._populate_log_combo()

        # 操作反馈
        self.feedback_lbl = QLabel("就绪")
        self.feedback_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px; padding: 6px 0;")
        content_layout.addWidget(self.feedback_lbl)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def _populate_log_combo(self):
        self.log_combo.clear()
        logs: dict[str, Path] = {}
        if self.cfg.stdout_log and self.cfg.stdout_log.exists():
            logs["标准输出"] = self.cfg.stdout_log
        if self.cfg.stderr_log and self.cfg.stderr_log.exists():
            logs["错误输出"] = self.cfg.stderr_log
        if self.cfg.service_log and self.cfg.service_log.exists():
            logs["服务日志"] = self.cfg.service_log
        # logs/ 目录下的日志
        logs_dir = self.cfg.app_dir / "logs"
        if logs_dir.exists() and logs_dir.is_dir():
            for f in sorted(logs_dir.iterdir()):
                if f.is_file() and f.suffix in (".log", ".txt"):
                    logs[f"logs/{f.name}"] = f
        # 额外日志
        for name, path in self.cfg.extra_logs.items():
            if path.exists():
                logs[name] = path
        self._log_map = logs
        for name in logs:
            self.log_combo.addItem(name)
        if logs:
            self._on_log_changed(list(logs.keys())[0])

    def _on_log_changed(self, name: str):
        self._current_log_name = name
        self._refresh_current_log()

    def _refresh_current_log(self):
        if not hasattr(self, "_log_map"):
            return
        path = self._log_map.get(self._current_log_name)
        if path:
            text = read_log_tail(path, lines=200)
            self.log_viewer.set_log_text(text)

    def _export_log(self):
        if not hasattr(self, "_log_map"):
            return
        path = self._log_map.get(self._current_log_name)
        if not path:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "导出日志", f"{self.cfg.key}_{path.name}", "日志文件 (*.log *.txt)")
        if dest:
            try:
                import shutil
                shutil.copy2(path, dest)
                self._set_feedback(f"已导出到: {dest}")
            except Exception as e:
                self._set_feedback(f"导出失败: {e}")

    def _open_nanobot_config(self):
        cfg = self.cfg.app_dir / "config.json"
        if cfg.exists():
            os.startfile(str(cfg))

    def _run_action(self, name: str, handler: Callable, *args):
        self._set_feedback(f"{name} 中...")
        self.thread = ActionThread(handler, *args)
        self.thread.finished_sig.connect(lambda ok, msg: self._on_action_done(name, ok, msg))
        self.thread.start()

    def _on_action_done(self, name: str, ok: bool, message: str):
        self._set_feedback(f"{name}: {message}")
        self.refresh_status()
        if not ok:
            QMessageBox.warning(self, f"{name} 提示", message)

    def _set_feedback(self, text: str):
        self.feedback_lbl.setText(text)

    def refresh_status(self):
        st = self.actions.status()
        pid = st.get("pid")
        running = st.get("running", False)
        healthy = st.get("healthy", False)
        process = st.get("process", "-")
        is_wsl_offline = process == "WSL 未启动"

        self.metric_pid.set_value(str(pid) if pid else "-")
        if is_wsl_offline:
            self.metric_status.set_value("WSL 未启动")
            self.metric_health.set_value("-")
            self.badge.set_status("WSL 未启动", STATUS_UNKNOWN)
        else:
            self.metric_status.set_value("运行中" if running else "已停止")
            self.metric_health.set_value("正常" if healthy else ("待启动" if not running else "未通过"))
            if running and healthy:
                self.badge.set_status("运行正常", STATUS_RUNNING)
            elif running:
                self.badge.set_status("运行中 (未通过健康检查)", STATUS_WARNING)
            else:
                self.badge.set_status("已停止", STATUS_STOPPED)
        self.metric_process.set_value(process)

        # WSL 状态 (OpenClaw)
        if self.cfg.key == "openclaw" and hasattr(self, "wsl_metric_service"):
            wsl_st = self.actions.wsl_status()
            if wsl_st.get("service") == "WSL 未启动":
                self.wsl_metric_service.set_value("WSL 未启动")
                self.wsl_metric_health.set_value("-")
                self.wsl_metric_version.set_value("-")
            elif wsl_st.get("available"):
                self.wsl_metric_service.set_value(wsl_st.get("service", "-"))
                self.wsl_metric_health.set_value(wsl_st.get("health", "-"))
                self.wsl_metric_version.set_value(wsl_st.get("version", "-"))
            else:
                self.wsl_metric_service.set_value("不可用")
                self.wsl_metric_health.set_value("-")
                self.wsl_metric_version.set_value("-")

        # 刷新日志列表 (可能新增了日志文件)
        self._populate_log_combo()


# =============================================================================
# 自定义 TabBar (深色工业风)
# =============================================================================

class DarkTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QTabBar::tab {{
                background-color: {PANEL_BG};
                color: {TEXT_SECONDARY};
                border: none;
                border-bottom: 3px solid transparent;
                padding: 14px 28px;
                font-weight: bold;
                font-size: 14px;
                margin-right: 2px;
            }}
            QTabBar::tab:hover {{
                background-color: {CARD_BG};
                color: {TEXT_PRIMARY};
            }}
            QTabBar::tab:selected {{
                background-color: {DARK_BG};
                color: {ACCENT_BLUE};
                border-bottom: 3px solid {ACCENT_BLUE};
            }}
        """)
        self.setExpanding(False)


# =============================================================================
# 主窗口
# =============================================================================

class UnifiedControlPanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("统一运维控制面板")
        self.setMinimumSize(1280, 800)
        self.resize(1400, 900)
        self._apply_dark_theme()
        self._build_ui()

    def _apply_dark_theme(self):
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {DARK_BG}; }}
            QWidget {{ font-family: {FONT_FAMILY}; }}
            QScrollBar:vertical {{
                background: {PANEL_BG};
                width: 10px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER};
                border-radius: 5px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {BORDER_LIGHT}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QMessageBox {{ background-color: {PANEL_BG}; }}
            QMessageBox QLabel {{ color: {TEXT_PRIMARY}; }}
        """)
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(DARK_BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.ColorRole.Base, QColor(CARD_BG))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL_BG))
        palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.ColorRole.Button, QColor(BORDER))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
        self.setPalette(palette)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部标题栏
        title_bar = QFrame()
        title_bar.setStyleSheet(f"background-color: {PANEL_BG}; border-bottom: 1px solid {BORDER};")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(20, 12, 20, 12)
        title = QLabel("<span style='font-size:22px; font-weight:bold; color:#c9d1d9;'>统一运维控制面板</span> <span style='font-size:12px; color:#6e7681;'>| 变电站 / Hermes / NanoBot / OpenClaw</span>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title_layout.addWidget(title)
        title_layout.addStretch()

        # 全局刷新
        self.global_refresh = StyledButton("全部刷新", "secondary")
        self.global_refresh.setFixedWidth(100)
        self.global_refresh.clicked.connect(self._refresh_all)
        title_layout.addWidget(self.global_refresh)
        layout.addWidget(title_bar)

        # Tab 容器
        self.tabs = QTabWidget()
        self.tabs.setTabBar(DarkTabBar())
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background-color: {DARK_BG};
            }}
            QTabWidget::tab-bar {{ left: 0; }}
        """)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.tab_widgets: dict[str, PlatformTab] = {}
        for cfg in PLATFORMS:
            tab = PlatformTab(cfg)
            self.tabs.addTab(tab, cfg.display_name)
            self.tab_widgets[cfg.key] = tab

        if not PLATFORMS:
            paths_html = r"E:\\项目\\变电站图像监控运维平台<br>E:\\项目\\hermes-panel<br>C:\\Users\\Administrator\\.nanobot<br>C:\\Users\\Administrator\\.openclaw"
            no_platform = QLabel(f"<center><h2 style='color:#8b949e;'>未检测到任何平台</h2><p style='color:#6e7681;'>请在以下路径放置 control_panel.pyw:</p><p style='color:#58a6ff;'>{paths_html}</p></center>")
            no_platform.setTextFormat(Qt.TextFormat.RichText)
            self.tabs.addTab(no_platform, "未检测到")

        layout.addWidget(self.tabs, 1)

        # 底部状态栏
        footer = QFrame()
        footer.setStyleSheet(f"background-color: {PANEL_BG}; border-top: 1px solid {BORDER};")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 8, 20, 8)
        self.footer_lbl = QLabel(f"就绪 | 检测到 {len(PLATFORMS)} 个平台")
        self.footer_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        footer_layout.addWidget(self.footer_lbl)
        footer_layout.addStretch()
        version_lbl = QLabel("Unified Control Panel v1.0 | PyQt6")
        version_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        footer_layout.addWidget(version_lbl)
        layout.addWidget(footer)

    def _refresh_all(self):
        for tab in self.tab_widgets.values():
            tab.refresh_status()
        self.footer_lbl.setText(f"全部刷新完成 | {time.strftime('%H:%M:%S')}")

    def closeEvent(self, event):
        for tab in self.tab_widgets.values():
            tab._refresh_timer.stop()
        event.accept()


# =============================================================================
# 命令行入口
# =============================================================================

def print_all_status():
    for cfg in PLATFORMS:
        print(f"\n=== {cfg.display_name} ===")
        actions = PlatformActions(cfg)
        st = actions.status()
        print(f"  running={st.get('running', False)}")
        print(f"  pid={st.get('pid') or '-'}")
        print(f"  healthy={st.get('healthy', False)}")
        print(f"  process={st.get('process', '-')}")
        if cfg.is_wsl:
            wsl = actions.wsl_status()
            print(f"  wsl_service={wsl.get('service', '-')}")
            print(f"  wsl_health={wsl.get('health', '-')}")


def main() -> int:
    _init_platforms()

    parser = argparse.ArgumentParser(description="统一运维控制面板")
    parser.add_argument("--action", choices=["panel", "status", "start-all", "stop-all"], default="panel")
    parser.add_argument("--platform", choices=[p.key for p in PLATFORMS] + ["all"], default="all")
    parser.add_argument("--hidden", action="store_true", help="后台隐藏启动")
    args = parser.parse_args()

    if args.action == "status":
        print_all_status()
        return 0

    if args.action == "start-all":
        for cfg in PLATFORMS:
            if args.platform != "all" and cfg.key != args.platform:
                continue
            actions = PlatformActions(cfg)
            ok, msg = actions.start(hidden=args.hidden)
            print(f"[{cfg.display_name}] {'OK' if ok else 'FAIL'}: {msg}")
        return 0

    if args.action == "stop-all":
        for cfg in PLATFORMS:
            if args.platform != "all" and cfg.key != args.platform:
                continue
            actions = PlatformActions(cfg)
            ok, msg = actions.stop()
            print(f"[{cfg.display_name}] {'OK' if ok else 'FAIL'}: {msg}")
        return 0

    # GUI 模式
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont(FONT_FAMILY, 10)
    app.setFont(font)
    window = UnifiedControlPanel()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
