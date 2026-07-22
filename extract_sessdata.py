#!/usr/bin/env python3
"""B站 SESSDATA Cookie 半自动提取工具

使用方法:
    python extract_sessdata.py

流程:
    1. 自动打开 Chromium 浏览器到 B站 首页
    2. 你在浏览器中手动登录（扫码/账号密码/短信验证）
    3. 登录成功后回到终端按 Enter
    4. 脚本自动提取 SESSDATA 值并打印

说明:
    - 本脚本不会记录或上传你的账号信息
    - SESSDATA 有效期通常 1-30 天，过期后需重新提取
    - 提取后更新 GitHub Secrets → BILIBILI_SESSDATA
"""

import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("错误：需要安装 Playwright")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)

LOGIN_URL = "https://passport.bilibili.com/login"
HOME_URL = "https://www.bilibili.com"


def main():
    print("正在启动浏览器...")
    print("请在浏览器中完成 B站 登录（扫码或账号密码）\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = context.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        # 打开 B站登录页
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=15000)

        print("=" * 50)
        print("请在浏览器中登录 B站")
        print("登录成功后，回到此终端按 Enter 继续...")
        print("=" * 50)

        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            browser.close()
            sys.exit(0)

        # 提取 SESSDATA
        cookies = context.cookies()
        sessdata = None
        for c in cookies:
            if c.get("name") == "SESSDATA":
                sessdata = c.get("value")
                break

        if sessdata:
            print("\n" + "=" * 60)
            print("已成功提取 SESSDATA：")
            print()
            print(f"  {sessdata}")
            print()
            print("请将上面的值更新到 GitHub：")
            print("  → https://github.com/lenventh/gaming-news/settings/secrets/actions")
            print("  → Secrets: BILIBILI_SESSDATA")
            print("=" * 60)
        else:
            print("\n未检测到 SESSDATA Cookie，请确认：")
            print("  1. 是否已在浏览器中完成登录？")
            print("  2. 登录账号是否被 B站 风控？")

        browser.close()


if __name__ == "__main__":
    main()
