import yaml

template_str = """
metadata:
  labels:
    app.kubernetes.io/name: opensandbox
spec:
  replicas: 1
  template:
    metadata:
      labels:
        app: opensandbox-sandbox
    spec:
      runtimeClassName: gvisor
      restartPolicy: Never
      tolerations:
        - operator: "Exists"
      containers:
      - name: main
        image: "199012118961/01sandbox-codeinterpreter:dev"
        imagePullPolicy: IfNotPresent
        command:
        - tail
        - -f
        - /dev/null
"""


def _deep_copy(obj):
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy(item) for item in obj]
    return obj


def _deep_merge(base, override):
    result = base.copy()
    for key, override_value in override.items():
        if override_value is None:
            continue
        if key not in result:
            result[key] = _deep_copy(override_value)
        elif isinstance(result[key], dict) and isinstance(override_value, dict):
            result[key] = _deep_merge(result[key], override_value)
        else:
            result[key] = _deep_copy(override_value)
    return result


base = yaml.safe_load(template_str)

pod_spec = {
    "initContainers": [{"name": "execd-installer"}],
    "containers": [
        {"name": "sandbox", "image": "199012118961/01sandbox-codeinterpreter:dev"}
    ],
    "volumes": [{"name": "opensandbox-bin", "emptyDir": {}}],
    "runtimeClassName": "kata-fc",
}

spec = {
    "replicas": 1,
    "template": {
        "spec": pod_spec,
    },
}

runtime_manifest = {
    "apiVersion": "sandbox.opensandbox.io/v1alpha1",
    "kind": "BatchSandbox",
    "metadata": {
        "name": "test-sandbox",
        "namespace": "opensandbox-system",
        "labels": {"runtime": "kata-fc"},
    },
    "spec": spec,
}

merged = _deep_merge(base, runtime_manifest)
print(yaml.dump(merged))
