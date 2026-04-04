from flask import Blueprint, render_template

from admin import require_admin


admin_user_access_bp = Blueprint("admin_user_access", __name__, url_prefix="/admin")


@admin_user_access_bp.route("/user-access-center", methods=["GET"])
@require_admin
def user_access_center_page():
    return render_template("admin_user_access.html")
