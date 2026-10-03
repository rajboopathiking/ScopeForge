"""Agent-to-Agent (A2A) Message Bus."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .protocol import A2AMessage, A2AIntent


class A2ABus:
    """Central bus for inter-agent communication, telemetry, and collaboration."""

    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = log_path or Path(".scopeforge/a2a_log.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.messages: List[A2AMessage] = []
        self.subscribers: List[Callable[[A2AMessage], None]] = []

    def subscribe(self, callback: Callable[[A2AMessage], None]):
        """Subscribe to incoming A2A messages."""
        self.subscribers.append(callback)

    def publish(self, message: A2AMessage) -> A2AMessage:
        """Sign and broadcast a message to subscribers and audit log."""
        if not message.signature:
            message.sign()

        self.messages.append(message)

        # Append to disk log
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(message.model_dump_json() + "\n")
        except Exception:
            pass

        # Notify subscribers
        for sub in self.subscribers:
            try:
                sub(message)
            except Exception:
                pass

        return message

    def send(
        self,
        sender: str,
        recipient: str,
        intent: A2AIntent,
        payload: Dict,
    ) -> A2AMessage:
        """Convenience method to construct and publish an A2A message."""
        msg = A2AMessage(
            sender=sender,
            recipient=recipient,
            intent=intent,
            payload=payload,
        )
        return self.publish(msg)

    def get_history(self, limit: int = 50) -> List[A2AMessage]:
        """Get recent A2A messages."""
        return self.messages[-limit:]
