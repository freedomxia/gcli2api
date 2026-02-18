"""
模型信息路由模块 - 处理 /models-info/* 相关的HTTP请求
展示 GCLI 和 Antigravity 模式的可用模型列表及 API 连接方式
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from log import log
from config import get_api_password
from src.utils import get_available_models, verify_panel_token
from src.api.antigravity import fetch_available_models


# 创建路由器
router = APIRouter(prefix="/models-info", tags=["models-info"])


@router.get("/list")
async def get_models_info(token: str = Depends(verify_panel_token)):
    """获取所有模式的模型列表和 API 连接信息"""
    try:
        # 获取 GCLI 模型列表（同步）
        gcli_models = get_available_models("gemini")

        # 获取 Antigravity 模型列表（异步，失败返回空列表）
        antigravity_models = []
        try:
            antigravity_models = await fetch_available_models()
        except Exception as e:
            log.warning(f"获取 Antigravity 模型列表失败: {e}")

        # 获取 API Key
        api_key = await get_api_password()

        # API 端点信息
        api_endpoints = {
            "gcli": {
                "openai": ["/v1/chat/completions", "/v1/models"],
                "gemini": [
                    "/v1beta/models",
                    "/{model}:generateContent",
                    "/{model}:streamGenerateContent",
                ],
                "anthropic": ["/v1/messages"],
            },
            "antigravity": {
                "openai": ["/antigravity/v1/chat/completions", "/antigravity/v1/models"],
                "gemini": [
                    "/antigravity/v1beta/models",
                    "/antigravity/{model}:generateContent",
                    "/antigravity/{model}:streamGenerateContent",
                ],
                "anthropic": ["/antigravity/v1/messages"],
            },
        }

        return JSONResponse(content={
            "gcli_models": gcli_models,
            "antigravity_models": antigravity_models,
            "api_endpoints": api_endpoints,
            "api_key": api_key,
        })

    except Exception as e:
        log.error(f"获取模型信息失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"获取模型信息失败: {str(e)}"}
        )
