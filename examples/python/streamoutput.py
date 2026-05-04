## adds real-time output streaming (live logs) from the sandbox while the code is running.
import asyncio
from datetime import timedelta

from code_interpreter import CodeInterpreter, SupportedLanguage
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.models.execd import ExecutionHandlers


async def on_stdout(msg):
    print("STDOUT:", msg.text)


async def on_stderr(msg):
    print("STDERR:", msg.text)


async def main():
    config = ConnectionConfig(
        domain="https://sandbox.01security.com",
        api_key="your-api-key",
        request_timeout=timedelta(seconds=60),
    )

    sandbox = await Sandbox.create(
        "opensandbox/code-interpreter:v1.0.2",
        connection_config=config,
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        env={"PYTHON_VERSION": "3.11"},
    )

    async with sandbox:
        interpreter = await CodeInterpreter.create(sandbox=sandbox)
        ctx = await interpreter.codes.create_context(SupportedLanguage.PYTHON)

        handlers = ExecutionHandlers(on_stdout=on_stdout, on_stderr=on_stderr)
        await interpreter.codes.run(
            "import time\nfor i in range(5):\n    print(i)\n    time.sleep(0.5)",
            context=ctx,
            handlers=handlers,
        )

        await sandbox.kill()


if __name__ == "__main__":
    asyncio.run(main())
