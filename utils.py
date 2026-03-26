# utils.py — 共享工具函数
import sqlite3
from flask import g, current_app


def get_db():
    """获取数据库连接（请求级）

    使用 current_app.config['DATABASE_PATH'] 确保配置覆盖生效。
    必须在 Flask 请求上下文中调用。
    """
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE_PATH'])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout=30000")
    return g.db


def close_db(exception=None):
    """请求结束后关闭数据库连接（teardown callback）"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_app(app):
    """初始化应用（注册teardown等）

    在Flask应用创建后调用:
        from utils import init_app
        init_app(app)
    """
    app.teardown_appcontext(close_db)
