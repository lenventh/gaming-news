#!/usr/bin/env python3
"""配置开机自启 - 悬浮窗 + 管道自动运行。

用法:
    python setup_autostart.py           # 安装自启
    python setup_autostart.py --widget  # 仅悬浮窗 (不自动跑管道)
    python setup_autostart.py --remove  # 移除
"""

import os
import sys

STARTUP_NAME = "GamingNewsWidget"


def install(auto_run=True):
    startup_folder = os.path.join(
        os.getenv("APPDATA"),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )

    python = sys.executable
    project_dir = os.path.dirname(os.path.abspath(__file__))
    widget_script = os.path.join(project_dir, "floating_widget.py")

    run_flag = "--run" if auto_run else ""

    pythonw = python.replace("python.exe", "pythonw.exe")
    bat_content = f'''@echo off
cd /d "{project_dir}"
start "" {pythonw} {widget_script} {run_flag}
'''

    bat_path = os.path.join(project_dir, "_startup_dashboard.bat")
    with open(bat_path, "w", encoding="ascii") as f:
        f.write(bat_content)

    shortcut_path = os.path.join(startup_folder, f"{STARTUP_NAME}.lnk")
    try:
        from win32com.client import Dispatch
        shell = Dispatch("WScript.Shell")
        lnk = shell.CreateShortcut(shortcut_path)
        lnk.TargetPath = bat_path
        lnk.WorkingDirectory = project_dir
        lnk.WindowStyle = 7
        lnk.Save()
        print(f"Installed: {shortcut_path}")
    except ImportError:
        vbs_path = os.path.join(startup_folder, f"{STARTUP_NAME}.vbs")
        vbs = f'CreateObject("WScript.Shell").Run """{bat_path}""", 7, False'
        with open(vbs_path, "w") as f:
            f.write(vbs)
        print(f"Installed (VBS): {vbs_path}")

    print()
    print("On next boot:")
    print(f"  Semi-transparent floating widget appears on desktop")
    if auto_run:
        print(f"  Pipeline auto-starts if CI data available")
    print(f"  Right-click widget for more options")
    print()
    print(f"Remove: python setup_autostart.py --remove")


def remove():
    startup_folder = os.path.join(
        os.getenv("APPDATA"),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    for fname in os.listdir(startup_folder):
        if STARTUP_NAME in fname:
            os.remove(os.path.join(startup_folder, fname))
            print(f"Removed: {fname}")

    project_dir = os.path.dirname(os.path.abspath(__file__))
    bat = os.path.join(project_dir, "_startup_dashboard.bat")
    if os.path.exists(bat):
        os.remove(bat)

    print("Auto-start removed.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    elif "--widget" in sys.argv:
        install(auto_run=False)
    else:
        install(auto_run=True)
