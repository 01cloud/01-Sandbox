import asyncio
from datetime import timedelta

from code_interpreter import CodeInterpreter, SupportedLanguage
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig


async def main() -> None:
    # 1. Configure connection
    config = ConnectionConfig(
        domain="https://sandbox.01security.com",
        api_key="your-api-key",
        request_timeout=timedelta(seconds=60),
    )

    # 2. Create a Sandbox with the code-interpreter image + runtime versions
    sandbox = await Sandbox.create(
        "opensandbox/code-interpreter:v1.0.2",
        connection_config=config,
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        env={
            "PYTHON_VERSION": "3.11",
            "JAVA_VERSION": "17",
            "NODE_VERSION": "20",
            "GO_VERSION": "1.24",
        },
    )

    # 3. Use async context manager to ensure local resources are cleaned up
    async with sandbox:
        # 4. Create CodeInterpreter wrapper
        interpreter = await CodeInterpreter.create(sandbox=sandbox)

        # 5. Create an execution context (Python)
        context = await interpreter.codes.create_context(SupportedLanguage.PYTHON)

        # 6. Run code
        result = await interpreter.codes.run(
            "import sys\nprint(sys.version)\nresult = 2 + 2\nresult",
            context=context,
        )

        # Alternatively, you can pass a language directly (recommended: SupportedLanguage.*).
        # This uses the default context for that language (state can persist across runs).
        # result = await interpreter.codes.run("print('hi')", language=SupportedLanguage.PYTHON)

        # 7. Print output
        if result.result:
            print(result.result[0].text)

        # 8. Cleanup remote instance (optional but recommended)
        await sandbox.kill()


if __name__ == "__main__":
    asyncio.run(main())