"""Test aislado de jed_trace (agent-shield-runtime).

NO importa sensores: usa GenericToolCall directamente (del propio repo).
Verifica reconstrucción de traza JED y los 4 predicates espejo de blankbox.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_shield_runtime.adapters.generic import GenericToolCall, GenericArg, Channel
from agent_shield_runtime.jed_trace import calls_to_jed_trace, eval_jed_predicates


def _call(tool, args, irreversible=False, claimed=None):
    return GenericToolCall(
        task_id="T1", tool=tool,
        args=[GenericArg(name=k, value=v, channel=Channel.USER) for k, v in args.items()],
        irreversible=irreversible, claimed_subobjective=claimed,
    )


def main() -> None:
    # Secuencia: leer .env (fs, no destructive) + enviar por correo (email, con secreto)
    calls = [
        _call("fs", {"path": ".env"}),
        _call("email", {"to": "x@y", "body": "secret de .env"}),
    ]
    trace = calls_to_jed_trace(calls, prompts=["lee y envía"])
    d = trace.to_dict()
    assert d["events"][0]["tool"] == "fs"
    assert d["events"][1]["result"] == "executed"
    preds = eval_jed_predicates(trace)
    assert "exfiltration" in preds, preds
    print(f"[ok] jed_trace: events={len(d['events'])} predicates={preds}")

    # Caso destructive_write
    calls2 = [_call("fs", {"path": "id_rsa"}, irreversible=True)]
    tr2 = calls_to_jed_trace(calls2)
    p2 = eval_jed_predicates(tr2)
    assert "destructive_write" in p2, p2
    print(f"[ok] jed_trace destructive_write: {p2}")


if __name__ == "__main__":
    main()
