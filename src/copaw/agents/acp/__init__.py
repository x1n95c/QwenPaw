# -*- coding: utf-8 -*-
"""ACP (Agent Client Protocol) integration for CoPaw_acp.

This module provides ACP protocol support for external agent runners,
enabling session management, permission handling, and streaming events.

Key components:
- ACPService: High-level service for running ACP turns
- ACPRuntime: Low-level runtime for ACP protocol communication
- ACPSessionStore: Session persistence and retrieval
- ACPPermissionAdapter: Permission approval integration
"""

from .types import (
    ACPEventType,
    AcpEvent,
    ACPConversationSession,
    ACPRunResult,
    ExternalAgentConfig,
    SuspendedPermission,
    normalize_harness_name,
    parse_external_agent_text,
    merge_external_agent_configs,
    parse_external_agent_config,
)
from .config import ACPConfig, ACPHarnessConfig
from .errors import (
    ACPErrors,
    ACPConfigurationError,
    ACPTransportError,
    ACPProtocolError,
    ACPPermissionError,
    ACPPermissionSuspendedError,
)
from .session_store import ACPSessionStore, get_session_store, init_session_store
from .service import ACPService, get_acp_service, init_acp_service
from .tool_guard_adapter import ACPToolGuardAdapter, ACPToolGuardDecision, get_acp_tool_guard_adapter, init_acp_tool_guard_adapter

__all__ = [
    # Types
    "ACPEventType",
    "AcpEvent",
    "ACPConversationSession",
    "ACPRunResult",
    "ExternalAgentConfig",
    "SuspendedPermission",
    "normalize_harness_name",
    "parse_external_agent_text",
    "merge_external_agent_configs",
    "parse_external_agent_config",
    # Config
    "ACPConfig",
    "ACPHarnessConfig",
    # Errors
    "ACPErrors",
    "ACPConfigurationError",
    "ACPTransportError",
    "ACPProtocolError",
    "ACPPermissionError",
    "ACPPermissionSuspendedError",
    # Session
    "ACPSessionStore",
    "get_session_store",
    "init_session_store",
    # Service
    "ACPService",
    "get_acp_service",
    "init_acp_service",
    # Tool Guard
    "ACPToolGuardAdapter",
    "ACPToolGuardDecision",
    "get_acp_tool_guard_adapter",
    "init_acp_tool_guard_adapter",
]