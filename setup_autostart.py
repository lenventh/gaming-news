#!/usr/bin/env python3
"""配置 Windows 开机自启 — 仪表盘 + 管道自动运行。

用法:
    python setup_autostart.py           # 安装自启
    python setup_autostart.py --remove  # 移除自启
"""

import os
import sys

STARTUP_NAME = "GamingNewsDashboard"


def install():
    startup_folder = os.path.join(
        os.getenv("APPDATA"),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )

    # 1. 后台启动仪表盘服务器
    python = sys.executable
    project_dir = os.path.dirname(os.path.abspath(__file__))
    dashboard_script = os.path.join(project_dir, "dashboard.py")

    bat_content = f'''@echo off
cd /d "{project_dir}"
start /min {python} {dashboard_script} --run
'''

    bat_path = os.path.join(project_dir, "_startup_dashboard.bat")
    with open(bat_path, "w", encoding="ascii") as f:
        f.write(bat_content)

    # 2. 创建开机自启快捷方式
    shortcut_path = os.path.join(startup_folder, f"{STARTUP_NAME}.lnk")
    try:
        import pythoncom
        from win32com.client import Dispatch
        pythoncom.CoInitialize()
        shell = Dispatch("WScript.Shell")
        lnk = shell.CreateShortcut(shortcut_path)
        lnk.TargetPath = bat_path
        lnk.WorkingDirectory = project_dir
        lnk.WindowStyle = 7  # minimized
        lnk.Save()
        print(f"Auto-start installed: {shortcut_path}")
    except ImportError:
        # Fallback: write VBS to startup folder
        vbs_path = os.path.join(startup_folder, f"{STARTUP_NAME}.vbs")
        vbs = f'CreateObject("WScript.Shell").Run """{bat_path}""", 7, False'
        with open(vbs_path, "w") as f:
            f.write(vbs)
        print(f"Auto-start installed (VBS): {vbs_path}")

    print()
    print("On next boot:")
    print("  1. Dashboard server starts minimized (--run auto-launches pipeline)")
    print("  2. Browser window opens in app mode on desktop")
    print()
    print("To remove: python setup_autostart.py --remove")


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
    bat_path = os.path.join(project_dir, "_startup_dashboard.bat")
    if os.path.exists(bat_path):
        os.remove(bat_path)

    print("Auto-start removed.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        install()
