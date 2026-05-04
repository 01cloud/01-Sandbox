## code executions share the same memory, so variables created earlier can be reused later.
import asyncio
from datetime import timedelta

from code_interpreter import CodeInterpreter, SupportedLanguage
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig


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

        result1 = await interpreter.codes.run(
            "users = ['Alice', 'Bob', 'Charlie']\nprint(len(users))",
            context=ctx,
        )
        if result1.result:
            for item in result1.result:
                print(item.text)

        result2 = await interpreter.codes.run(
            "users.append('Dave')\nprint(users)\nresult = users\nresult",
            context=ctx,
        )
        if result2.result:
            for item in result2.result:
                print(item.text)

        await sandbox.kill()


if __name__ == "__main__":
    asyncio.run(main())
