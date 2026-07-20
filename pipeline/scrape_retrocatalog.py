"""从 retrocatalog.com 抓取设备列表

网站 JS 渲染，需 Playwright。运行:
    python pipeline/scrape_retrocatalog.py

输出格式可直接复制到 device_os_map.py。
"""

import sys
import json
from playwright.sync_api import sync_playwright


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://retrocatalog.com", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)

        # 从 DOM 提取所有设备卡片/行
        # 策略：找到所有设备名称，然后从相邻列中提取 OS 信息
        devices = page.evaluate("""
            () => {
                const results = [];

                // 尝试找到表格或列表
                const rows = document.querySelectorAll(
                    'tr, [class*="device"], [class*="item"], [class*="card"], [class*="row"]'
                );

                rows.forEach(row => {
                    const text = row.textContent.toLowerCase();
                    if (!text || text.length < 5 || text.length > 500) return;

                    let name = '';
                    let os = '';

                    // 提取设备名（通常是第一个链接或标题）
                    const nameEl = row.querySelector('a, h2, h3, h4, [class*="name"], [class*="title"]');
                    if (nameEl) name = nameEl.textContent.trim();

                    // 判断 OS
                    const hasAndroid = /android|google play/i.test(text);
                    const hasLinux = /linux|arkos|amberelec|jelos|minui|muos|garlicos/i.test(text);
                    const hasWindows = /windows|win 10|win 11|x86/i.test(text);

                    if (hasAndroid) os = 'android';
                    else if (hasWindows) os = 'windows';
                    else if (hasLinux) os = 'linux';
                    else os = 'linux';  // 默认 Linux（大部分 retro handheld）

                    if (name && os) {
                        results.push({name: name.toLowerCase(), os: os});
                    }
                });

                return results;
            }
        """)

        browser.close()

        # 分类输出
        android = sorted(set(d["name"] for d in devices if d["os"] == "android"))
        linux = sorted(set(d["name"] for d in devices if d["os"] == "linux"))
        windows = sorted(set(d["name"] for d in devices if d["os"] == "windows"))

        print(f"# Scraped {len(devices)} devices from retrocatalog.com")
        print(f"# Android: {len(android)}, Linux: {len(linux)}, Windows: {len(windows)}")
        print()
        print("# ============================================================")
        print("# Android")
        print("# ============================================================")
        print("ANDROID_DEVICES = {")
        for d in android:
            print(f'    "{d}",')
        print("}")
        print()
        print("# ============================================================")
        print("# Linux")
        print("# ============================================================")
        print("LINUX_DEVICES = {")
        for d in linux:
            print(f'    "{d}",')
        print("}")
        print()
        print("# ============================================================")
        print("# Windows")
        print("# ============================================================")
        print("WINDOWS_DEVICES = {")
        for d in windows:
            print(f'    "{d}",')
        print("}")


if __name__ == "__main__":
    scrape()
