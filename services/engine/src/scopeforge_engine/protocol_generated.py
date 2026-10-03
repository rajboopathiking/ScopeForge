"""AUTO-GENERATED from packages/protocol-schema/protocol.json — do not hand-edit.

Run: pnpm generate:protocol
"""
from __future__ import annotations

PROTOCOL_VERSION = "0.1.0"
PROTOCOL_MAJOR = 0
PROTOCOL_MINOR = 1
PROTOCOL_PATCH = 0

METHOD_NAMES = ["initialize","capabilities","health","shutdown","run.create","run.list","run.status","run.cancel","run.close","event.subscribe","tool.cancel","provider.list","model.list","model.probe","case.create","case.list","case.show","case.update","artifact.import","evidence.list","evidence.show","evidence.redact","coverage.get","coverage.set","report.build","export.create","run.rebuild","policy.import","policy.check","policy.status","scope.show","chat.submit","case.execute","coach.score","coverage.report","tool.preview","tool.execute","approval.respond","lab.list","lab.launch","lab.stop","lab.reset","experiment.compare","evidence.diff","mcp.add","mcp.list","mcp.enable","mcp.disable","mcp.remove","tool.list","tool.invoke","har.import","burp.import"]
EVENT_NAMES = ["token.delta","reasoning.summary","warning","error","run.checkpointed","budget.changed"]


class IncompatibleProtocol(Exception):
    pass


def assert_compatible(peer: str) -> None:
    try:
        peer_major = int(str(peer).split(".")[0])
    except ValueError:
        peer_major = -1
    if peer_major != PROTOCOL_MAJOR:
        raise IncompatibleProtocol(
            f"Incompatible protocol major: peer={peer} local={PROTOCOL_VERSION}. "
            "Regenerate with pnpm generate:protocol or upgrade the engine/launcher."
        )


def is_known_method(m: str) -> bool:
    return m in METHOD_NAMES


def is_known_event(e: str) -> bool:
    return e in EVENT_NAMES
