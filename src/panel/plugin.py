"""
插件连接路由模块 - 处理外部 Token Updater 推送凭证
支持从 flow2api_tupdater 等工具自动同步 OAuth 凭证
"""

import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import config
from log import log
from src.credential_manager import credential_manager
from src.storage_adapter import get_storage_adapter

router = APIRouter(prefix="/api/plugin", tags=["plugin"])


async def _get_plugin_token() -> str:
    """获取插件连接 Token"""
    return await config.get_config_value(
        "plugin_connection_token", default="", env_var="PLUGIN_CONNECTION_TOKEN"
    )


async def _verify_plugin_token(request: Request) -> str:
    """验证插件连接 Token"""
    token = await _get_plugin_token()
    if not token:
        raise HTTPException(status_code=503, detail="插件连接未配置 connection_token")

    # 支持 Authorization: Bearer <token> 和 body 中的 token 字段
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        req_token = auth[7:]
    else:
        try:
            body = await request.json()
            req_token = body.get("token", "")
        except Exception:
            req_token = ""

    if not req_token or req_token != token:
        raise HTTPException(status_code=401, detail="无效的连接 Token")

    return req_token


@router.post("/update-token")
async def update_token(request: Request):
    """
    接收外部推送的 OAuth 凭证（兼容 flow2api 插件协议）

    请求体格式:
    {
        "token": "<connection_token>",
        "credential": {
            "client_id": "...",
            "client_secret": "...",
            "token": "ya29...",
            "refresh_token": "1//...",
            "scopes": [...],
            "token_uri": "...",
            "project_id": "...",
            "expiry": "..."
        },
        "filename": "optional-filename.json",
        "mode": "geminicli",
        "name": "profile-name"
    }

    也支持简化格式（直接传凭证字段）:
    {
        "token": "<connection_token>",
        "client_id": "...",
        "client_secret": "...",
        "refresh_token": "1//...",
        ...
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 JSON")

    # 验证 token
    plugin_token = await _get_plugin_token()
    if not plugin_token:
        raise HTTPException(status_code=503, detail="插件连接未配置 connection_token")

    req_token = body.get("token", "")
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        req_token = auth[7:]

    if not req_token or req_token != plugin_token:
        raise HTTPException(status_code=401, detail="无效的连接 Token")

    # 提取凭证数据
    credential = body.get("credential")
    if not credential:
        # 简化格式：直接从 body 提取凭证字段
        cred_keys = {"client_id", "client_secret", "refresh_token", "token",
                     "access_token", "scopes", "token_uri", "project_id", "expiry"}
        credential = {k: v for k, v in body.items() if k in cred_keys and v}

    if not credential:
        raise HTTPException(status_code=400, detail="缺少凭证数据")

    # 验证必要字段
    has_refresh = bool(credential.get("refresh_token"))
    has_client = bool(credential.get("client_id"))
    if not has_refresh or not has_client:
        raise HTTPException(
            status_code=400,
            detail="凭证需要包含 client_id 和 refresh_token"
        )

    mode = body.get("mode", "geminicli")
    if mode not in ("geminicli", "antigravity"):
        mode = "geminicli"

    # 生成文件名
    filename = body.get("filename", "")
    if not filename:
        project_id = credential.get("project_id", "plugin")
        name = body.get("name", "")
        ts = int(time.time())
        prefix = f"{name}-" if name else ""
        filename = f"{prefix}{project_id}-{ts}.json"
    if not filename.endswith(".json"):
        filename += ".json"

    # 检查是否已有相同 project_id 的凭证（去重更新）
    storage_adapter = await get_storage_adapter()
    existing_files = await storage_adapter.list_credentials(mode=mode)
    project_id = credential.get("project_id", "")
    updated_existing = False

    if project_id:
        for ef in existing_files:
            try:
                existing_data = await storage_adapter.get_credential(ef, mode=mode)
                if existing_data and existing_data.get("project_id") == project_id:
                    # 更新已有凭证
                    filename = ef
                    updated_existing = True
                    log.info(f"[Plugin] 更新已有凭证: {filename} (project_id={project_id})")
                    break
            except Exception:
                continue

    # 存储凭证
    if mode == "antigravity":
        await credential_manager.add_antigravity_credential(filename, credential)
    else:
        await credential_manager.add_credential(filename, credential)

    action = "updated" if updated_existing else "created"
    log.info(f"[Plugin] 凭证已{action}: {filename} (mode={mode})")

    return JSONResponse(content={
        "success": True,
        "message": f"凭证已{'更新' if updated_existing else '创建'}",
        "filename": filename,
        "action": action,
        "id": filename,
    })


@router.post("/check-tokens")
async def check_tokens(request: Request):
    """
    检查 token 状态（兼容 flow2api 插件协议）
    外部 Token Updater 用此接口判断哪些凭证需要刷新
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    # 验证 token
    plugin_token = await _get_plugin_token()
    if not plugin_token:
        raise HTTPException(status_code=503, detail="插件连接未配置")

    req_token = body.get("token", "")
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        req_token = auth[7:]
    if not req_token or req_token != plugin_token:
        raise HTTPException(status_code=401, detail="无效的连接 Token")

    mode = body.get("mode", "geminicli")
    if mode not in ("geminicli", "antigravity"):
        mode = "geminicli"

    storage_adapter = await get_storage_adapter()
    files = await storage_adapter.list_credentials(mode=mode)

    tokens = []
    for f in files:
        try:
            state = await storage_adapter.get_credential_state(f, mode=mode)
            cred = await storage_adapter.get_credential(f, mode=mode)
            email = (state or {}).get("user_email", "") if state else ""
            disabled = (state or {}).get("disabled", False) if state else False
            error_codes = (state or {}).get("error_codes", []) if state else []

            # 判断是否需要刷新
            needs_refresh = False
            if cred:
                expiry = cred.get("expiry", "")
                if expiry:
                    try:
                        from datetime import datetime, timezone
                        exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        # 提前 5 分钟标记为需要刷新
                        needs_refresh = (exp_dt - now).total_seconds() < 300
                    except Exception:
                        needs_refresh = True
                else:
                    needs_refresh = True

            tokens.append({
                "filename": f,
                "email": email,
                "is_active": not disabled,
                "needs_refresh": needs_refresh,
                "error_codes": error_codes,
            })
        except Exception as e:
            log.warning(f"[Plugin] 检查凭证状态失败 {f}: {e}")

    return JSONResponse(content={
        "success": True,
        "tokens": tokens,
        "needs_refresh_emails": [
            t["email"] for t in tokens if t["needs_refresh"] and t["is_active"] and t["email"]
        ],
    })


@router.get("/status")
async def plugin_status(request: Request):
    """获取插件连接状态"""
    plugin_token = await _get_plugin_token()
    return JSONResponse(content={
        "enabled": bool(plugin_token),
        "has_token": bool(plugin_token),
    })
