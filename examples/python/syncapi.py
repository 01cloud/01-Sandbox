## execute code synchronously inside a secure sandbox environment and return the result immediately. Your program waits until execution is finished
from datetime import timedelta

import httpx
from code_interpreter import CodeInterpreterSync
from opensandbox import SandboxSync
from opensandbox.config import ConnectionConfigSync

config = ConnectionConfigSync(
    domain="localhost:8080",
    api_key="your-api-key",
    request_timeout=timedelta(seconds=60),
    transport=httpx.HTTPTransport(limits=httpx.Limits(max_connections=20)),
)

sandbox = SandboxSync.create(
    "opensandbox/code-interpreter:v1.0.2",
    connection_config=config,
    entrypoint=["/opt/opensandbox/code-interpreter.sh"],
    env={"PYTHON_VERSION": "3.11"},
)
with sandbox:
    interpreter = CodeInterpreterSync.create(sandbox=sandbox)
    result = interpreter.codes.run("result = 2 + 2\nresult")
    if result.result:
        print(result.result[0].text)
    sandbox.kill()