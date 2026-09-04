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
from mcp.types import ToolAnnotations
from pydantic import Field

from src.finance_api.auth import (
    FinanceApiAuthError,
    FinanceApiKeyAuth,
    FinanceApiPrincipal,
)
from src.finance_api.models import (
    FinanceAnswerRequest,
    FinanceQueryRequest,
    FinanceQueryResponse,
)
from src.finance_api.service import FinanceApiGateway
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
    "结构化原始数据，summary 返回基于数据的中文结论，both 同时返回两者。"
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


def _tool_descriptor() -> dict[str, Any]:
    schema = FinanceQueryRequest.model_json_schema()
    schema.pop("title", None)
    return {
        "name": FINANCE_TOOL_NAME,
        "title": "Fin Agent 金融数据查询",
        "description": FINANCE_TOOL_DESCRIPTION,
        "inputSchema": schema,
        "outputSchema": FinanceQueryResponse.model_json_schema(),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "http": {
            "method": "POST",
            "path": "/v1/finance/query",
            "authentication": "Authorization: Bearer <key> or X-API-Key: <key>",
        },
        "mcp": {
            "transport": "streamable-http",
            "path": "/mcp",
        },
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

    mcp = FastMCP(
        "fin-agent-finance",
        instructions=(
            "Use finance_data_query for structured Chinese financial and securities data. "
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
        conversation_id: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
                description="Stable caller-owned identifier for multi-turn context.",
            ),
        ] = None,
        max_rows: Annotated[
            int,
            Field(ge=1, le=100, description="Maximum returned rows per result."),
        ] = 100,
    ) -> FinanceQueryResponse:
        principal = _CURRENT_PRINCIPAL.get()
        if principal is None:
            raise PermissionError("finance API authentication context is missing")
        response = await current_gateway().execute(
            FinanceQueryRequest(
                query=query,
                response_mode=response_mode,
                runtime=runtime,
                research_mode=research_mode,
                conversation_id=conversation_id,
                max_rows=max_rows,
            ),
            principal_id=principal.principal_id,
        )
        if not response.ok:
            raise RuntimeError(
                response.error.message if response.error else "finance query failed"
            )
        return response

    mcp_http_app = mcp.streamable_http_app()

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
            try:
                yield
            finally:
                if owns_gateway and gateway_holder["value"] is not None:
                    gateway_holder["value"].close()

    app = FastAPI(
        title="Fin Agent Financial Data API",
        version="1.0.0",
        description=(
            "Independent financial question-answering and structured-data service. "
            "Query endpoints and the MCP transport require an API key."
        ),
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
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/data-map")

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

    @app.get("/data-map/catalog.json", include_in_schema=False)
    async def public_data_map_catalog() -> dict[str, Any]:
        return _catalog_projection(catalog_service)

    @app.get("/v1/finance/catalog", tags=["catalog"])
    async def finance_catalog(
        _principal: FinanceApiPrincipal = Depends(require_principal),
    ) -> dict[str, Any]:
        return _catalog_projection(catalog_service)

    @app.get("/v1/tools", tags=["tools"])
    async def list_tools(
        _principal: FinanceApiPrincipal = Depends(require_principal),
    ) -> dict[str, Any]:
        return {"tools": [_tool_descriptor()]}

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
        request_payload = FinanceQueryRequest(
            query=payload.query,
            response_mode="both" if payload.include_data else "summary",
            runtime=payload.runtime,
            research_mode=payload.research_mode,
            conversation_id=payload.conversation_id,
            max_rows=payload.max_rows,
        )
        try:
            response = await current_gateway().execute(
                request_payload,
                principal_id=principal.principal_id,
            )
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
