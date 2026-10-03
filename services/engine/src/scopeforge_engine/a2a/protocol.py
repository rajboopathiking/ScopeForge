"""A2A (Agent-to-Agent) Protocol: Standardized inter-agent messaging and delegation format."""
from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class A2AIntent(str, Enum):
    TASK_DELEGATION = "TASK_DELEGATION"
    TASK_RESULT = "TASK_RESULT"
    EVIDENCE_SHARING = "EVIDENCE_SHARING"
    CONSENSUS_REQUEST = "CONSENSUS_REQUEST"
    CONSENSUS_RESPONSE = "CONSENSUS_RESPONSE"
    HANDOVER = "HANDOVER"
    ALERT = "ALERT"


class A2AMessage(BaseModel):
    """Standardized A2A Protocol Envelope."""
    message_id: str = Field(default_factory=lambda: f"a2a_{uuid.uuid4().hex[:10]}")
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    protocol_version: str = "1.0.0"
    sender: str
    recipient: str
    intent: A2AIntent
    payload: Dict[str, Any]
    signature: str = ""

    def sign(self) -> str:
        """Compute cryptographic hash of the message payload for integrity."""
        raw = f"{self.sender}:{self.recipient}:{self.intent}:{json.dumps(self.payload, sort_keys=True)}"
        self.signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self.signature

    def verify(self) -> bool:
        """Verify the integrity hash of the message."""
        raw = f"{self.sender}:{self.recipient}:{self.intent}:{json.dumps(self.payload, sort_keys=True)}"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self.signature == expected
