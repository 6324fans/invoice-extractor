"""桌面窗口入口：后台起 Flask，前台用 pywebview 打开窗口。

运行: python3 main.py
打包入口也是本文件。
"""
import os
import sys
import threading
import webview

import app as flask_app

# 随机端口避免与其他服务冲突
PORT = 5173


def start_server():
    """后台线程运行 Flask（关闭 reloader，避免双进程）。"""
    flask_app.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


def main():
    # 后台启动 Flask
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    # 打开桌面窗口
    webview.create_window(
        "微信发票提取",
        f"http://127.0.0.1:{PORT}/",
        width=1200, height=820,
        min_size=(900, 600),
    )
    webview.start()
    # 窗口关闭后退出
    os._exit(0)


if __name__ == "__main__":
    main()
