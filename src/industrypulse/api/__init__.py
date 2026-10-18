"""FastAPI 服务（前缀 /v1，见任务书 §14 接口清单）。"""

from .app import ApiFailure, create_app

__all__ = ["ApiFailure", "create_app"]
