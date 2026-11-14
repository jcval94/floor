"""Real MCP wire + separate worker harness, not a substitute for hosted deployment.

Requires a registered dataset. Never creates credentials or calls Slack unless
operator environment already explicitly configures that separately in the worker.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ai_lab.store import Store, TERMINAL


def unpack(result):
    if result.structuredContent:
        return result.structuredContent
    return json.loads(result.content[0].text)


async def request(root, dataset):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ai_lab.server", "--root", str(root)],
        env={
            **os.environ,
            "PYTHONPATH": str(Path("src").resolve()),
            "FLOOR_LAB_ROLE": "experiment_runner",
            "FLOOR_LAB_ACTOR": "python-sdk-smoke",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            listing = await session.list_tools()
            plan = unpack(
                await session.call_tool(
                    "floor_plan_experiment",
                    {
                        "question": "Compara los tres modelos registrados para W1",
                        "dataset_id": dataset,
                    },
                )
            )
            assert plan["ok"], plan
            accepted = unpack(
                await session.call_tool(
                    "floor_submit_experiment",
                    {"plan": plan["data"]["plan"], "idempotency_key": "sdk-smoke-v1"},
                )
            )
            assert accepted["ok"], accepted
            duplicate = unpack(
                await session.call_tool(
                    "floor_submit_experiment",
                    {"plan": plan["data"]["plan"], "idempotency_key": "sdk-smoke-v1"},
                )
            )
            assert accepted["data"]["run_id"] == duplicate["data"]["run_id"]
            return {
                "client": "official Python SDK 1.30.0",
                "protocol": initialized.protocolVersion,
                "tool_count": len(listing.tools),
                "accepted": accepted["data"],
                "disconnected_at": time.time(),
            }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    store = Store(a.root)
    env = {
        **os.environ,
        "PYTHONPATH": str(Path("src").resolve()),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }
    # This is a separate worker process, not a task inside the MCP HTTP handler.
    worker = subprocess.Popen(
        [sys.executable, "-m", "ai_lab.worker", "--root", str(store.root)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        evidence = asyncio.run(request(store.root, a.dataset))
        run_id = evidence["accepted"]["run_id"]
        deadline = time.monotonic() + 150
        while (
            store.get(run_id)["state"] not in TERMINAL and time.monotonic() < deadline
        ):
            if worker.poll() is not None:
                raise RuntimeError("worker exited")
            time.sleep(0.2)
        run = store.get(run_id)
        if run["state"] != "SUCCEEDED":
            raise RuntimeError(str(run["error"]))
        evidence.update(
            {
                "terminal_state": run["state"],
                "finished": run["finished"],
                "finished_after_client_disconnect": run["finished"]
                > evidence["disconnected_at"],
                "result": run["result"],
                "slack_real_delivery_verified": False,
                "hosting_scope": "local harness only; not deployed",
            }
        )
        a.output.write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence, indent=2))
    finally:
        worker.terminate()
        worker.wait(timeout=15)


if __name__ == "__main__":
    main()
