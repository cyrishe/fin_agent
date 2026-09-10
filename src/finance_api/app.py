from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations, CallToolResult, TextContent
from pydantic import Field
from src.services.user_session_service import UserSessionService, UserSessionStorageError


async def require_admin_session(request: Request) -> dict:
    token = request.cookies.get(UserSessionService.MEMBER_SESSION_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    try:
        identity = await asyncio.to_thread(UserSessionService().resolve_member_session, session_token=token)
    except UserSessionStorageError:
        raise HTTPException(status_code=503, detail="账户服务暂时不可用")
    if not identity:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    if identity.get("user_type") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以查看系统统计")
    return identity

from src.finance_api.auth import (
    FinanceApiAuthError,
    FinanceApiKeyAuth,
    FinanceApiPrincipal,
)
from src.finance_api.models import (
    FinanceAnswerRequest,
    FinanceQueryRequest,
    FinanceQueryResponse,
    FinanceTaskRequest,
    SkillId,
    SKILL_SELECTION_DESCRIPTION,
)
from src.finance_api.service import FinanceApiGateway
from src.scenarios.financial_qa.business_skills import FinanceSkillUnavailableError
from src.finance_api.data_status import DataStatusMonitor
from src.services.request_usage_service import DailyUsageService
from src.services.finance_data_tool_catalog_service import (
    FinanceDataToolCatalogService,
)


# The standalone process follows the main application convention: deployment
# environment variables win, while a repository-local .env fills missing values.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

logger = logging.getLogger(__name__)
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_CURRENT_PRINCIPAL: contextvars.ContextVar[FinanceApiPrincipal | None] = (
    contextvars.ContextVar("finance_api_principal", default=None)
)

FINANCE_TOOL_NAME = "finance_data_query"
FINANCE_TOOL_DESCRIPTION = (
    "查询 Fin Agent 的结构化金融证券数据。适用于股票、指数、行业、板块、基金、债券和市场热点，"
    "以及行情、资金流、估值、财务三表、业绩预告、业务分部、股东、质押、公司行动、指数/行业/"
    "板块成分、研报观点和研报年度预测指标等问题。输入自然语言问题；response_mode=data 返回"
    "结构化原始数据，summary 返回基于数据的中文结论，both 同时返回两者。返回中的 data_sources "
    "会用公开业务名称说明实际查询的数据对象、数据类型、查询目标和记录数，便于核验与溯源。"
    "detail=true 另附轮数、逐步 Token、耗时与查询检查记录。"
    "默认每次调用独立；仅显式传入 conversation_id 时续接该用户的会话上下文。"
)
FINANCE_TASK_DESCRIPTION = (
    "通用金融任务入口。提交自然语言问题，Fin Agent 自动选择合适的已授权金融 Skill 和金融数据工具，"
    "完成取证与分析并返回综合结论。需要指定方法时，先用 list_skills 发现可用 ID，再传 skill_ids；"
    "多个 Skill 按传入顺序指导分析、共享基础数据、综合输出。省略 skill_ids 则自动选择。"
    "response_mode=both 同时返回结论与参考数据，summary 只返回结论，data 只返回数据。"
    "仅使用当前金融数据工具，不执行工具工坊自定义工具或通用 Web Search。"
    "默认 standard 执行与 auto 分析深度；conversation_id 可选，不传则每次独立。"
)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _catalog_projection(catalog: FinanceDataToolCatalogService) -> dict[str, Any]:
    tree = catalog.build_tree()
    subjects: list[dict[str, Any]] = []
    for subject in tree.get("subjects") or []:
        if not isinstance(subject, dict):
            continue
        dataviews: list[dict[str, Any]] = []
        for dataview in subject.get("dataviews") or []:
            if not isinstance(dataview, dict):
                continue
            operations = []
            for function in dataview.get("functions") or []:
                if not isinstance(function, dict):
                    continue
                operation = str(function.get("operation") or "").strip()
                if operation and operation not in operations:
                    operations.append(operation)
            dataviews.append(
                {
                    "name": str(dataview.get("name") or "").strip(),
                    "description": str(dataview.get("description") or dataview.get("desc") or "").strip(),
                    "operations": operations,
                    "field_count": len(dataview.get("fields") or []),
                }
            )
        subjects.append(
            {
                "name": str(subject.get("name") or "").strip(),
                "description": str(subject.get("description") or subject.get("desc") or "").strip(),
                "dataviews": dataviews,
            }
        )
    return {
        "version": str(tree.get("version") or ""),
        "revision": catalog.catalog_revision(),
        "subject_count": len(subjects),
        "dataview_count": sum(len(item["dataviews"]) for item in subjects),
        "subjects": subjects,
    }


def create_app(
    *,
    auth: FinanceApiKeyAuth | None = None,
    gateway: FinanceApiGateway | None = None,
    catalog: FinanceDataToolCatalogService | None = None,
) -> FastAPI:
    key_auth = auth or FinanceApiKeyAuth.from_env()
    catalog_service = catalog or FinanceDataToolCatalogService()
    gateway_holder: dict[str, FinanceApiGateway | None] = {"value": gateway}
    owns_gateway = gateway is None
    allowed_hosts = [
        item.strip()
        for item in str(
            os.environ.get("FINANCE_API_ALLOWED_HOSTS")
            or "127.0.0.1:*,localhost:*,testserver"
        ).split(",")
        if item.strip()
    ]
    allowed_origins = [
        item.strip()
        for item in str(
            os.environ.get("FINANCE_API_ALLOWED_ORIGINS") or ""
        ).split(",")
        if item.strip()
    ]
    root_path = str(os.environ.get("FINANCE_API_ROOT_PATH") or "").strip()
    if root_path:
        root_path = "/" + root_path.strip("/")

    mcp = FastMCP(
        "fin-agent-finance",
        instructions=(
            "Use finance_task for financial questions: the server selects suitable Skills and data tools. "
            "Pass ordered skill_ids to finance_task when specific methods are required; discover IDs with list_skills. "
            "Use finance_data_query for direct structured Chinese financial and securities data queries. "
            "Choose response_mode=data when another program or agent will analyze the rows, "
            "summary for a concise answer, and both when both evidence and explanation are needed."
        ),
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )

    def current_gateway() -> FinanceApiGateway:
        current = gateway_holder.get("value")
        if current is None:
            raise RuntimeError("finance API gateway is not ready")
        return current

    async def execute_mcp(request: FinanceQueryRequest) -> FinanceQueryResponse | CallToolResult:
        principal = _CURRENT_PRINCIPAL.get()
        if principal is None:
            raise PermissionError("finance API authentication context is missing")
        response = await current_gateway().execute(
            request, principal_id=principal.principal_id, request_channel="mcp",
        )
        if not response.ok:
            if request.detail:
                return CallToolResult(isError=True,
                    content=[TextContent(type="text", text=response.model_dump_json(by_alias=True))],
                    structuredContent=response.model_dump(mode="json", by_alias=True))
            raise RuntimeError(response.error.message if response.error else "finance query failed")
        return response

    @mcp.tool(
        name=FINANCE_TOOL_NAME,
        title="Fin Agent 金融数据查询",
        description=FINANCE_TOOL_DESCRIPTION,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def finance_data_query(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=4_000,
                description="Natural-language financial data question.",
            ),
        ],
        response_mode: Annotated[
            Literal["data", "summary", "both"],
            Field(
                description="Return structured rows, a generated summary, or both."
            ),
        ] = "both",
        runtime: Annotated[
            Literal["cc", "dsh"] | None,
            Field(description="Optional runtime override; omit for the server default."),
        ] = None,
        research_mode: Annotated[
            Literal["fast", "auto", "deep"],
            Field(description="Summary depth; does not change the data contract."),
        ] = "fast",
        execution_mode: Annotated[
            Literal["standard", "fast"],
            Field(
                description=(
                    "DSH execution policy. fast limits the agent to API discovery, "
                    "call generation, and return without inspection or retry."
                )
            ),
        ] = "standard",
        conversation_id: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
                description="Stable caller-owned identifier for multi-turn context. Omit for an independent query.",
            ),
        ] = None,
        max_rows: Annotated[
            int,
            Field(ge=1, le=100, description="Maximum returned rows per result."),
        ] = 100,
        detail: Annotated[bool, Field(description="Include turns, per-step token usage, timings and query validation evidence. No change to execution behavior.")] = False,
        is_test: Annotated[bool, Field(description="计入测试用量栏及系统总量，不改变权限或执行行为。")] = False,
    ) -> FinanceQueryResponse:
        return await execute_mcp(
            FinanceQueryRequest(
                query=query,
                response_mode=response_mode,
                runtime=runtime,
                research_mode=research_mode,
                execution_mode=execution_mode,
                conversation_id=conversation_id,
                max_rows=max_rows,
                detail=detail,
                is_test=is_test,
            ),
        )

    @mcp.tool(
        name="finance_task", title="Fin Agent 金融分析与 Skill 调用",
        description=FINANCE_TASK_DESCRIPTION,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        structured_output=True,
    )
    async def finance_task(
        query: Annotated[str, Field(min_length=1, max_length=4_000, description="自然语言金融问题。")],
        skill_ids: Annotated[list[SkillId] | None, Field(description=SKILL_SELECTION_DESCRIPTION)] = None,
        response_mode: Annotated[Literal["data", "summary", "both"], Field(description="返回数据、结论或两者。默认 both。")] = "both",
        research_mode: Annotated[Literal["fast", "auto", "deep"], Field(description="分析深度，默认 auto。")] = "auto",
        execution_mode: Annotated[Literal["standard", "fast"], Field(description="standard 支持取证和修正；fast 用于快速取数，省略检查与重试。")] = "standard",
        runtime: Annotated[Literal["cc", "dsh"] | None, Field(description="省略则使用服务端默认运行时。")] = None,
        conversation_id: Annotated[str | None, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", description="调用方会话 ID；省略则每次独立。")] = None,
        max_rows: Annotated[int, Field(ge=1, le=100, description="每个结果集返回行数上限。")] = 100,
        detail: Annotated[bool, Field(description="附带执行与用量明细。")] = False,
        is_test: Annotated[bool, Field(description="计入测试用量栏及系统总量，不改变权限或执行行为。")] = False,
    ) -> FinanceQueryResponse:
        return await execute_mcp(FinanceTaskRequest(
            query=query, skill_ids=skill_ids, response_mode=response_mode,
            research_mode=research_mode, execution_mode=execution_mode, runtime=runtime,
            conversation_id=conversation_id, max_rows=max_rows, detail=detail, is_test=is_test,
        ))

    @mcp.tool(
        name="list_skills", title="Fin Agent 可用金融 Skill",
        description="列出当前调用身份可用的已发布金融方法，包含 ID、用途与版本。将 ID 传给 finance_task 的 skill_ids 可显式指定方法。不返回方法正文、私有资源或未发布草稿。",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
        structured_output=True,
    )
    async def list_finance_skills() -> dict[str, Any]:
        principal = _CURRENT_PRINCIPAL.get()
        if principal is None:
            raise PermissionError("finance API authentication context is missing")
        return await asyncio.to_thread(current_gateway().list_skills, principal_id=principal.principal_id)

    mcp_http_app = mcp.streamable_http_app()
    data_monitor = DataStatusMonitor()
    daily_usage = DailyUsageService()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if gateway_holder["value"] is None:
            gateway_holder["value"] = FinanceApiGateway()
        if (
            owns_gateway
            and str(os.environ.get("FINANCE_DSH_PREWARM_ON_START") or "1")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        ):
            try:
                status = await asyncio.to_thread(current_gateway().prewarm)
                logger.info("Finance DSH prewarm status: %s", status)
            except Exception as exc:  # startup remains observable and retryable
                logger.warning("Finance DSH prewarm failed: %s", exc)
        async with mcp.session_manager.run():
            monitor_task = None
            if owns_gateway and os.environ.get("FINANCE_STATUS_ENABLED", "1") == "1":
                monitor_task = asyncio.create_task(data_monitor.run())
            try:
                yield
            finally:
                if monitor_task:
                    monitor_task.cancel()
                    try:
                        await monitor_task
                    except asyncio.CancelledError:
                        pass
                if owns_gateway and gateway_holder["value"] is not None:
                    gateway_holder["value"].close()

    app = FastAPI(
        title="Fin Agent Financial Data API",
        version="1.0.0",
        description=(
            "Independent financial question-answering and structured-data service. "
            "Query endpoints and the MCP transport require an API key."
        ),
        root_path=root_path,
        lifespan=lifespan,
    )

    cors_origins = [
        item.strip()
        for item in str(os.environ.get("FINANCE_API_CORS_ORIGINS") or "").split(",")
        if item.strip()
    ]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "X-API-Key", "Content-Type", "MCP-Protocol-Version", "MCP-Session-Id"],
        )

    bearer_scheme = HTTPBearer(auto_error=False, scheme_name="FinanceApiBearer")
    api_key_scheme = APIKeyHeader(
        name="X-API-Key",
        auto_error=False,
        scheme_name="FinanceApiKeyHeader",
    )

    async def require_principal(
        bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
        x_api_key: str | None = Security(api_key_scheme),
    ) -> FinanceApiPrincipal:
        authorization = (
            f"{bearer.scheme} {bearer.credentials}" if bearer is not None else ""
        )
        try:
            return key_auth.authenticate(authorization, x_api_key or "")
        except FinanceApiAuthError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": exc.message},
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    @app.middleware("http")
    async def authenticate_mcp(request: Request, call_next):
        if request.url.path == "/mcp" or request.url.path.startswith("/mcp/"):
            try:
                principal = key_auth.authenticate(
                    request.headers.get("Authorization", ""),
                    request.headers.get("X-API-Key", ""),
                )
            except FinanceApiAuthError as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={
                        "ok": False,
                        "error": {"code": exc.code, "message": exc.message},
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
            token = _CURRENT_PRINCIPAL.set(principal)
            try:
                return await call_next(request)
            finally:
                _CURRENT_PRINCIPAL.reset(token)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def root(request: Request) -> RedirectResponse:
        public_root = str(request.scope.get("root_path") or "").rstrip("/")
        return RedirectResponse(url=f"{public_root}/data-map")

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "fin-agent-finance-api",
            "default_runtime": current_gateway().default_runtime,
            "authentication": key_auth.status(),
            "catalog_revision": catalog_service.catalog_revision(),
        }

    @app.get("/data-map", response_class=HTMLResponse, include_in_schema=False)
    async def data_map() -> HTMLResponse:
        return HTMLResponse(
            (_STATIC_DIR / "data-map.html").read_text(encoding="utf-8")
        )

    @app.get("/status", response_class=HTMLResponse, include_in_schema=False)
    async def data_status_page(request: Request) -> HTMLResponse:
        try:
            await require_admin_session(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                return RedirectResponse(str(request.scope.get("root_path", "")) + "/login", status_code=303)
            raise
        return HTMLResponse((_STATIC_DIR / "status.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store, private"})

    @app.get("/status/data", tags=["system"])
    async def data_status_snapshot(_admin: dict = Depends(require_admin_session)) -> dict[str, Any]:
        # Requests only read the cached observation; visitors cannot trigger SQL.
        return data_monitor.current()

    @app.get("/mcp-guide", response_class=HTMLResponse, include_in_schema=False)
    async def mcp_guide() -> HTMLResponse:
        return HTMLResponse(
            (_STATIC_DIR / "mcp-guide.html").read_text(encoding="utf-8")
        )

    @app.get("/data-map/catalog.json", include_in_schema=False)
    async def public_data_map_catalog() -> dict[str, Any]:
        return _catalog_projection(catalog_service)

    @app.get("/v1/finance/catalog", tags=["catalog"])
    async def finance_catalog(
        _principal: FinanceApiPrincipal = Depends(require_principal),
    ) -> dict[str, Any]:
        return _catalog_projection(catalog_service)

    @app.get("/v1/usage/daily", tags=["system"])
    async def usage_daily(days: int = 30,
                          _admin: dict = Depends(require_admin_session)):
        if not 1 <= days <= 90:
            raise HTTPException(status_code=422, detail="days must be between 1 and 90")
        try:
            return await asyncio.to_thread(daily_usage.daily, days)
        except Exception:
            logger.exception("Daily usage statistics unavailable")
            raise HTTPException(status_code=503, detail="Usage statistics unavailable")

    @app.get("/v1/tools", tags=["tools"])
    async def list_tools(
        _principal: FinanceApiPrincipal = Depends(require_principal),
    ) -> dict[str, Any]:
        # Use the registered MCP schemas so REST discovery cannot drift from tools/list.
        paths = {"finance_data_query": ("POST", "/v1/finance/query"),
                 "finance_task": ("POST", "/v1/finance/task"), "list_skills": ("GET", "/v1/skills")}
        return {"tools": [{
            **tool.model_dump(mode="json", exclude_none=True),
            "http": {"method": paths[tool.name][0], "path": paths[tool.name][1],
                     "authentication": "Authorization: Bearer <key> or X-API-Key: <key>"},
            "mcp": {"transport": "streamable-http", "path": "/mcp"},
        } for tool in await mcp.list_tools()]}

    @app.get("/v1/skills", tags=["catalog"])
    async def available_skills(principal: FinanceApiPrincipal = Depends(require_principal)) -> dict[str, Any]:
        return await asyncio.to_thread(current_gateway().list_skills, principal_id=principal.principal_id)

    @app.post("/v1/finance/task", response_model=FinanceQueryResponse, response_model_by_alias=True,
              tags=["finance"], summary="Run a financial task with automatic or explicit Skills",
              description=FINANCE_TASK_DESCRIPTION)
    async def run_finance_task(payload: FinanceTaskRequest,
                               principal: FinanceApiPrincipal = Depends(require_principal)) -> FinanceQueryResponse | JSONResponse:
        try:
            response = await current_gateway().execute(payload, principal_id=principal.principal_id)
        except FinanceSkillUnavailableError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("finance task execution failed")
            raise HTTPException(status_code=500, detail="The financial task failed to execute.") from exc
        if response.ok:
            return response
        return JSONResponse(status_code=502, content=response.model_dump(mode="json", by_alias=True))

    @app.post(
        "/v1/finance/query",
        response_model=FinanceQueryResponse,
        response_model_by_alias=True,
        tags=["finance"],
        summary="Query financial data",
        description=FINANCE_TOOL_DESCRIPTION,
    )
    async def query_finance(
        payload: FinanceQueryRequest,
        principal: FinanceApiPrincipal = Depends(require_principal),
    ) -> FinanceQueryResponse | JSONResponse:
        try:
            response = await current_gateway().execute(
                payload,
                principal_id=principal.principal_id,
            )
        except Exception as exc:
            logger.exception("finance API execution failed")
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "finance_service_error",
                    "message": "The financial data service failed to execute the request.",
                },
            ) from exc
        if response.ok:
            return response
        return JSONResponse(
            status_code=502,
            content=response.model_dump(mode="json", by_alias=True),
        )

    @app.post(
        "/v1/finance/answer",
        response_model=FinanceQueryResponse,
        response_model_by_alias=True,
        tags=["finance"],
        summary="Answer a financial question",
    )
    async def answer_finance(
        payload: FinanceAnswerRequest,
        principal: FinanceApiPrincipal = Depends(require_principal),
    ) -> FinanceQueryResponse | JSONResponse:
        request_payload = FinanceTaskRequest(
            query=payload.query,
            skill_ids=payload.skill_ids,
            response_mode="both" if payload.include_data else "summary",
            runtime=payload.runtime,
            research_mode=payload.research_mode,
            execution_mode=payload.execution_mode,
            conversation_id=payload.conversation_id,
            max_rows=payload.max_rows,
            detail=payload.detail,
            is_test=payload.is_test,
        )
        try:
            response = await current_gateway().execute(
                request_payload,
                principal_id=principal.principal_id,
            )
        except FinanceSkillUnavailableError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("finance answer execution failed")
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "finance_service_error",
                    "message": "The financial question-answering service failed to execute the request.",
                },
            ) from exc
        if response.ok:
            return response
        return JSONResponse(
            status_code=502,
            content=response.model_dump(mode="json", by_alias=True),
        )

    # Reuse the official MCP SDK's Streamable HTTP route and lifecycle instead
    # of maintaining a second JSON-RPC implementation.  Extending the routes
    # keeps the canonical endpoint at /mcp without a redirecting mount prefix.
    app.router.routes.extend(mcp_http_app.routes)
    return app


def main() -> None:
    host = str(os.environ.get("FINANCE_API_HOST") or "0.0.0.0").strip()
    port = _env_int("FINANCE_API_PORT", 22100, minimum=1, maximum=65535)
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        log_level=str(os.environ.get("FINANCE_API_LOG_LEVEL") or "info").lower(),
        workers=1,
    )


if __name__ == "__main__":
    main()
