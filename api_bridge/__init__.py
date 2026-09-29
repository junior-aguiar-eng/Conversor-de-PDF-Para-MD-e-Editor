"""Módulos delegados e controladores da API Bridge NexoJuris."""

from __future__ import annotations

from api_bridge.conversion import ConversionMixin
from api_bridge.editor import EditorMixin
from api_bridge.library import LibraryMixin
from api_bridge.online import OnlineMixin
from api_bridge.reader import ReaderMixin
from api_bridge.resource_helpers import ResourceHelperMixin
from api_bridge.system import SystemMixin

__all__ = [
    "ConversionMixin",
    "EditorMixin",
    "LibraryMixin",
    "OnlineMixin",
    "ReaderMixin",
    "ResourceHelperMixin",
    "SystemMixin",
]
